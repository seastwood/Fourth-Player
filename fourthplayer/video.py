"""The one pipeline everybody watches, and one WebRTC peer per guest.

The shape of this file is a single decision: the screen is captured and encoded
**once**, and a `tee` hands the same encoded bytes to every guest. A fourth
guest therefore costs bandwidth and nothing else -- no extra capture, no extra
encode. On the machine this was written for that is not an optimisation, it is
the difference between working and not: measured there, 720p60 through the
Radeon's encoder runs at about 54 fps and a second concurrent encode does not
fit.

What it costs is per-guest adaptation. Everyone shares one bitrate ladder, so a
guest on a bad connection cannot be given a smaller picture without encoding a
second stream. The queue feeding each peer is therefore leaky: a slow guest
drops frames and stays live rather than stalling and dragging the pipeline down
with them.

GStreamer wants a GLib main loop and the rest of the program is asyncio, so the
loop runs on its own thread and every callback is marshalled back with
`call_soon_threadsafe`. Nothing in this module touches session state directly.
"""

import concurrent.futures
import logging
import threading
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst, GstWebRTC, GstSdp, GstVideo, GLib, GObject  # noqa: E402

from . import net  # noqa: E402

log = logging.getLogger("fourthplayer.video")

# Long enough for a cold VAAPI encoder on slow hardware, short enough that a
# genuinely broken pipeline is reported rather than hung on.
START_TIMEOUT = 10 * Gst.SECOND if hasattr(Gst, "SECOND") else 10_000_000_000

_initialised = False


def init():
    global _initialised
    if not _initialised:
        Gst.init(None)
        _initialised = True


class PipelineWorker:
    """The one thread allowed to change the pipeline, and a way to replace it.

    Serialising every add and remove keeps the GPU driver from being used from
    two places at once, which this process has segfaulted over. The cost is a
    single point of failure: if that thread stops making progress -- wedged in
    a driver call, or gone entirely, which is what happened -- then every later
    job queues behind nothing and nothing works again until a restart. So it
    can be thrown away and replaced.
    """

    def __init__(self, name="gst-mutate"):
        self._name = name
        self._pool = self._make()

    def _make(self):
        return concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=self._name)

    @property
    def executor(self):
        return self._pool

    def submit(self, fn, *args):
        return self._pool.submit(fn, *args)

    def alive(self, timeout=5.0):
        """Whether it is still doing what it is given."""
        try:
            self._pool.submit(lambda: None).result(timeout=timeout)
            return True
        except Exception:
            return False

    def reset(self):
        """Abandon the current thread and start a fresh one.

        The old executor is not waited for: waiting is precisely the thing that
        does not finish when a worker has wedged.
        """
        old, self._pool = self._pool, self._make()
        try:
            old.shutdown(wait=False, cancel_futures=True)
        except TypeError:                       # older Python
            old.shutdown(wait=False)
        return old

    def shutdown(self, wait=True):
        self._pool.shutdown(wait=wait)


def _nice_type():
    """The GType of webrtcbin's ICE agent, or None if it cannot be had.

    It is registered by the webrtc plugin rather than published in a typelib,
    and only once something has actually instantiated it -- so a throwaway
    webrtcbin has to exist before the name resolves.
    """
    try:
        Gst.ElementFactory.make("webrtcbin", None)
        return GObject.type_from_name("GstWebRTCNice")
    except Exception:
        return None


def make_ice_agent(cfg):
    """An ICE agent whose UDP ports fall in a range a router can forward.

    This is what makes fourth-player reachable from outside at all. Left to
    itself webrtcbin takes ephemeral ports -- eight of them, scattered across
    the whole range -- so there is nothing to write a firewall rule about, and
    a guest outside the network gets a black picture however much is forwarded.

    It has to be built here and passed to webrtcbin at construction: `ice-agent`
    is construct-only, and *reading* the one webrtcbin makes for itself
    corrupts it (`g_object_get_qdata: assertion G_IS_OBJECT (object) failed`,
    then a crash at negotiation). Returns None if any of that fails, which
    costs the port range and nothing else.
    """
    gtype = _nice_type()
    if gtype is None:
        log.warning("could not reach the ICE agent's type; ports will be "
                    "ephemeral and cannot be forwarded")
        return None
    try:
        ice = GObject.new(gtype)
        ice.set_property("min-rtp-port", cfg.rtp_port_min)
        ice.set_property("max-rtp-port", cfg.rtp_port_max)
        return ice
    except Exception as exc:
        log.warning("could not bound the ICE port range (%s); ports will be "
                    "ephemeral", exc)
        return None


# profile_idc + constraint flags, per RFC 6184. The level is appended.
_H264_PROFILE_IDC = {
    "constrained-baseline": "42e0",
    "baseline": "4200",
    "main": "4d00",
    "high": "6400",
}


def h264_profile_level_id(profile, height):
    """The profile-level-id a guest's browser will check us against.

    webrtcbin builds `a=fmtp` from the payloader's caps, and the payloader only
    learns the profile once it has seen an SPS -- which happens after the offer
    is made. So the offer went out with no fmtp at all, every browser applied
    the spec default of constrained baseline, and the ones that check properly
    refused the high-profile stream that then arrived. Stating it up front is
    the fix, and it is only honest because the encoder's profile is pinned.

    The level is rounded up to cover the frame size; advertising a level above
    what is sent is allowed, the reverse is not.
    """
    idc = _H264_PROFILE_IDC.get(profile, _H264_PROFILE_IDC["constrained-baseline"])
    level = "1e" if height <= 480 else "1f" if height <= 720 else "28"
    return idc + level


def with_fmtp(sdp, fmtp):
    """Add the H.264 parameters a browser needs, if webrtcbin left them out.

    It leaves them out every time: the payloader learns the profile from the
    stream's first SPS, and the offer is written before a single frame has
    flowed. So every offer went out with no `a=fmtp` at all, browsers applied
    the spec default of constrained baseline with single-NAL packetisation, and
    the strict ones refused the high-profile stream that arrived instead.

    Done here rather than by forcing the payloader's caps, because a capsfilter
    is a filter: one that names a profile-level-id the payloader does not
    produce matches nothing and passes nothing, which turns a picture that was
    merely refused by some browsers into no picture for anybody.
    """
    if not fmtp or "a=fmtp:96" in sdp:
        return sdp
    out, added = [], False
    for line in sdp.splitlines(True):
        out.append(line)
        if not added and line.startswith("a=rtpmap:96 H264"):
            ending = "\r\n" if line.endswith("\r\n") else "\n"
            out.append(f"a=fmtp:96 {fmtp}{ending}")
            added = True
    return "".join(out)


def _caps(width, height):
    return f"video/x-raw(memory:VAMemory),format=NV12,width={width},height={height}"


class Stage:
    """Capture, encode once, and fan the result out."""

    def __init__(self, cfg, loop):
        init()
        self.cfg = cfg
        self.loop = loop
        self.peers = {}
        self._glib_loop = None
        self._thread = None
        self.has_audio = False
        # Where this machine is reachable from outside, discovered once. Only
        # the address is used; see fourthplayer/net.py for why not the port.
        self.public_ip = cfg.public_ip
        if cfg.advertise_public_ip and not self.public_ip:
            found = net.public_address()
            if found:
                self.public_ip = found[0]
                log.info("public address is %s; guests outside the network will "
                         "be offered it on the forwarded ports", self.public_ip)
            else:
                log.info("could not discover a public address; only guests on "
                         "this network will be able to connect")
        # Every add and remove of a peer goes through this one thread. Adding
        # a peer while another is being torn down means two threads mutating
        # one pipeline, and the symptom is the replacement refusing to reach
        # PLAYING -- so a guest who reloaded got a black screen. One worker
        # keeps them ordered; being a worker at all keeps them off the event
        # loop, which is what stopped the server freezing for seconds at a
        # time while a live peer was dismantled.
        self.worker = PipelineWorker()

        keyint = cfg.keyframe_interval or max(1, cfg.fps * 2)
        # Bits the encoder may hold back to smooth a burst. Smoothing is delay.
        cpb = max(16, int(cfg.bitrate_kbps * cfg.cpb_ms / 1000))
        hevc = cfg.codec.lower() in ("h265", "hevc")
        if cfg.hardware_encode:
            element = "vah265enc" if hevc else "vah264enc"
            encoder = (f"{element} name=enc target-usage={cfg.target_usage} "
                       f"bitrate={cfg.bitrate_kbps} key-int-max={keyint} "
                       f"cpb-size={cpb} b-frames=0")
        else:
            element = "x265enc" if hevc else "x264enc"
            encoder = (f"{element} name=enc speed-preset=ultrafast "
                       f"tune=zerolatency bitrate={cfg.bitrate_kbps} "
                       f"key-int-max={keyint}")
        # Pin the profile between encoder and parser: the payloader reads it
        # from these caps to build profile-level-id, and without it a browser
        # is guessing.
        profile = "" if hevc else f"! video/x-h264,profile={cfg.h264_profile} "
        self._fmtp = "" if hevc else (
            f"profile-level-id={h264_profile_level_id(cfg.h264_profile, cfg.height)};"
            f"packetization-mode=1;level-asymmetry-allowed=1")
        parser = "h265parse" if hevc else "h264parse"
        payloader = "rtph265pay" if hevc else "rtph264pay"
        encoding = "H265" if hevc else "H264"
        convert = (f"vapostproc ! {_caps(cfg.width, cfg.height)}"
                   if cfg.hardware_encode else
                   f"videoscale ! videoconvert ! video/x-raw,format=I420,"
                   f"width={cfg.width},height={cfg.height}")

        # config-interval=-1 puts SPS/PPS in front of every keyframe. Without it
        # a guest who joins mid-session has the parameter sets they need only if
        # they happened to be listening at the start, which they never are.
        description = (
            f"ximagesrc display-name={cfg.display} use-damage=0 show-pointer=false "
            f"! video/x-raw,framerate={cfg.fps}/1 "
            f"! {convert} "
            f"! {encoder} "
            f"{profile}"
            f"! {parser} config-interval=-1 "
            f"! {payloader} pt=96 config-interval=-1 aggregate-mode=zero-latency "
            f"mtu={cfg.rtp_mtu} "
            f"! application/x-rtp,media=video,encoding-name={encoding},"
            f"payload=96,clock-rate=90000 "
            f"! tee name=vtee allow-not-linked=true"
        )
        self._description = description
        self._audio_description = self._audio_branch() if cfg.audio else ""
        self._build(with_audio=bool(self._audio_description))

    def _audio_branch(self):
        """The game's sound, or nothing at all if this machine cannot give it."""
        cfg = self.cfg
        for element in ("pulsesrc", "opusenc", "rtpopuspay"):
            if not Gst.ElementFactory.find(element):
                log.warning("no %s: the session will be silent", element)
                return ""
        return (
            f" pulsesrc device={cfg.audio_device} provide-clock=false "
            f"! audioconvert ! audioresample "
            f"! audio/x-raw,rate=48000,channels=2 "
            f"! opusenc bitrate={cfg.audio_bitrate_kbps * 1000} "
            f"frame-size={cfg.audio_frame_ms} inband-fec=true "
            f"! rtpopuspay pt=97 mtu={cfg.rtp_mtu} "
            f"! application/x-rtp,media=audio,encoding-name=OPUS,payload=97,clock-rate=48000 "
            f"! tee name=atee allow-not-linked=true")

    def _build(self, with_audio):
        description = self._description
        if with_audio:
            description += self._audio_description
        log.debug("pipeline: %s", description)
        self.pipeline = Gst.parse_launch(description)
        self.tee = self.pipeline.get_by_name("vtee")
        self.audio_tee = self.pipeline.get_by_name("atee")
        self.encoder = self.pipeline.get_by_name("enc")
        self.has_audio = self.audio_tee is not None

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        bus.connect("message::warning", self._on_warning)

    @property
    def mutations(self):
        """The executor to hand to run_in_executor. Always the current one."""
        return self.worker.executor

    def reset_worker(self, why="it stopped responding"):
        """Replace the pipeline worker after it has stopped making progress.

        One thread owns every change to the pipeline, which is what keeps the
        GPU driver from being used from two places at once. The cost is that if
        that thread ever stops -- wedged in a driver call, or gone altogether,
        which is what happened here: the process was left with no worker at all
        and every later job queued behind nothing for ever -- then nothing
        works again until a restart. It "worked initially" and never after.

        A queue nobody is serving is worth abandoning. The old executor is left
        to its fate rather than waited for, precisely because waiting is the
        thing that does not finish.
        """
        log.warning("replacing the pipeline worker: %s", why)
        self.worker.reset()

    def worker_alive(self, timeout=5.0):
        return self.worker.alive(timeout)

    # -- lifecycle ----------------------------------------------------------

    def start(self):
        self._glib_loop = GLib.MainLoop()
        self._thread = threading.Thread(target=self._glib_loop.run,
                                        name="gst-mainloop", daemon=True)
        self._thread.start()
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            # Refused outright, which is what a missing or busy sound server
            # produces: pulsesrc answers "Connection refused" and takes the
            # whole pipeline down with it. Losing sound must not cost the
            # picture, and this branch is the one that actually fires -- the
            # retry below only ever covered a pipeline that stalled on its way
            # to PLAYING, so an audio device that failed immediately killed the
            # session and the session was the thing everybody wanted.
            if self.has_audio:
                log.warning("the pipeline refused to start with audio; "
                            "retrying without it")
                self.pipeline.set_state(Gst.State.NULL)
                self._build(with_audio=False)
                return self.start()
            raise RuntimeError("the capture pipeline refused to start")
        # set_state is asynchronous: it returns ASYNC while the pipeline is
        # still getting there. Returning here would let a guest who joins a
        # second later attach a peer to a pipeline that is not yet PLAYING,
        # and that peer never fires on-negotiation-needed -- the join succeeds,
        # no offer is ever made, and the guest sits on a black screen with no
        # error anywhere. Wait for it.
        change, state, _pending = self.pipeline.get_state(START_TIMEOUT)
        if change == Gst.StateChangeReturn.FAILURE or state != Gst.State.PLAYING:
            self.pipeline.set_state(Gst.State.NULL)
            # A missing or busy sound device must not cost anybody the picture.
            # Losing audio is a disappointment; losing the session is a
            # failure, and one that would be hard to diagnose from a guest's
            # black screen.
            if self.has_audio:
                log.warning("the pipeline would not start with audio; "
                            "retrying without it")
                self._build(with_audio=False)
                return self.start()
            raise RuntimeError(
                f"the capture pipeline stalled reaching PLAYING (got {state.value_nick})")
        log.info("capture running: %dx%d @%d, %d kb/s, audio %s",
                 self.cfg.width, self.cfg.height, self.cfg.fps,
                 self.cfg.bitrate_kbps, "on" if self.has_audio else "off")

    def stop(self):
        for peer_id in list(self.peers):
            self.remove_peer(peer_id)
        self.pipeline.set_state(Gst.State.NULL)
        if self._glib_loop:
            self._glib_loop.quit()
        self.worker.shutdown(wait=True)

    # -- peers --------------------------------------------------------------

    def add_peer(self, peer_id, on_signal, configure=None):
        """Attach one guest. `on_signal(kind, payload)` is called on the asyncio loop.

        `configure` runs after the peer exists and before it is wired up, which
        is the only window in which its callbacks can be set without racing the
        first thing it does.
        """
        started = time.monotonic()
        # A peer left over under this name is wreckage, not a guest: the only
        # way one survives is an attach that failed partway. Clear it out
        # rather than refusing, because refusing makes the slot unusable for
        # the rest of the session -- every later guest met
        # "peer slot0 is already attached" and got nothing.
        stale = self.peers.pop(peer_id, None)
        if stale is not None:
            log.warning("peer %s was still registered; discarding it", peer_id)
            try:
                stale.detach()
            except Exception:
                pass

        peer = Peer(self, peer_id, on_signal)
        if configure is not None:
            configure(peer)
        try:
            peer.attach()
        except Exception:
            # Registered only once it is genuinely attached. Doing it first
            # meant a failure here left the name taken by something that had
            # never worked and would never be cleaned up.
            try:
                peer.detach()
            except Exception:
                pass
            raise
        self.peers[peer_id] = peer
        took = time.monotonic() - started
        if took > 1.0:
            log.warning("peer %s took %.1fs to attach", peer_id, took)
        else:
            log.debug("peer %s attached in %.2fs", peer_id, took)
        # A guest who joins between keyframes sees nothing until the next one.
        # At a one-second interval that is a second of black, so ask for one now.
        self.force_keyframe()
        return peer

    def take_peer(self, peer_id):
        """Unregister a peer and hand it back, without tearing it down yet.

        Freeing the name immediately matters: a guest who reloads is replaced
        within milliseconds, and the new peer wants the same slot id while the
        old one is still shutting down.
        """
        return self.peers.pop(peer_id, None)

    def remove_peer(self, peer_id):
        peer = self.take_peer(peer_id)
        if peer:
            peer.detach()
        return peer is not None

    def force_keyframe(self):
        pad = self.encoder.get_static_pad("src")
        if pad:
            pad.send_event(GstVideo.video_event_new_upstream_force_key_unit(
                Gst.CLOCK_TIME_NONE, True, 0))

    # -- bus ----------------------------------------------------------------

    def _on_error(self, _bus, message):
        err, debug = message.parse_error()
        log.error("pipeline error: %s (%s)", err.message, debug)
        # An error inside one guest's branch is one guest's problem. The usual
        # one is the data channel: "SCTP association went into error state",
        # which kills that peer's video too and leaves them staring at black
        # while everybody else carries on. Nothing else notices -- ICE stays
        # connected, so no failure handler fires -- so it has to be spotted
        # here and the peer rebuilt.
        blamed = self._peer_named(f"{message.src}") or self._peer_named(debug or "")
        if blamed is not None and blamed.on_broken is not None:
            log.warning("peer %s: its branch failed; rebuilding it", blamed.id)
            self.loop.call_soon_threadsafe(blamed.on_broken, err.message)

    def _peer_named(self, text):
        for peer_id, peer in self.peers.items():
            if f"peer_{peer_id}" in text or f"_{peer_id}" in text:
                return peer
        return None

    def _on_warning(self, _bus, message):
        err, debug = message.parse_warning()
        log.warning("pipeline warning: %s (%s)", err.message, debug)


class Peer:
    """One guest's webrtcbin: a send-only video track and an input channel."""

    def __init__(self, stage, peer_id, on_signal):
        self.stage = stage
        self.id = peer_id
        self._on_signal = on_signal
        self.channel = None
        self.on_input = None          # set by the session; called with raw bytes
        self.on_dead = None           # called when the media connection is over
        self.on_broken = None         # called when this peer's branch errors
        # Whether this peer currently has a usable path. A peer *object* is not
        # evidence of one: after a guest's network changes, the object survives
        # with every address in it dead, and ICE settles on "disconnected"
        # rather than "failed" -- so nothing declares it over and it sat there
        # holding a slot for the whole session.
        self.ice_ok = False
        self.ice = None               # held so webrtcbin's agent outlives it
        self._candidate_kinds = set()
        self._announced = set()
        self._route_logged = False
        self.webrtc = None
        self._branches = []           # (tee, tee pad, queue) per media type
        # What we actually handed to this peer. The difference between "the
        # server sent nothing" and "the network ate it" is the first question
        # to ask about a black picture, and without this it cannot be answered
        # from the host at all.
        self.sent = {"video_bytes": 0, "video_packets": 0,
                     "audio_bytes": 0, "audio_packets": 0}
        self._offered = False
        self._assembled = False
        # Every signal we connect, so all of them can be disconnected before
        # the elements are destroyed. A handler that fires during teardown
        # reaches into a GObject whose C half is already gone, and PyGObject
        # follows it straight into a segfault:
        #   g_object_get_qdata -> g_type_check_instance_is_fundamentally_a
        # This process died that way three times in twenty minutes.
        self._handlers = []

    # -- wiring -------------------------------------------------------------

    def attach(self):
        cfg = self.stage.cfg
        pipeline = self.stage.pipeline

        # The agent is kept on the peer for its whole life: webrtcbin does not
        # take a reference that survives our own, and letting it go early
        # produces a refcount assertion at teardown.
        self.ice = make_ice_agent(cfg)
        if self.ice is not None:
            self.webrtc = Gst.ElementFactory.make_with_properties(
                "webrtcbin", ["ice-agent"], [self.ice])
            if self.webrtc is not None:
                self.webrtc.set_property("name", f"peer_{self.id}")
        else:
            self.webrtc = None
        if self.webrtc is None:
            self.webrtc = Gst.ElementFactory.make("webrtcbin", f"peer_{self.id}")
        self.webrtc.set_property("bundle-policy", "max-bundle")
        self.webrtc.set_property("latency", cfg.jitter_ms)
        if cfg.stun_server:
            self.webrtc.set_property("stun-server", cfg.stun_server)
        if cfg.turn_server:
            self.webrtc.set_property("turn-server", cfg.turn_server)

        pipeline.add(self.webrtc)

        # Connect the signals BEFORE anything that can make webrtcbin want to
        # negotiate. Creating the data channel and following the pipeline into
        # PLAYING both emit on-negotiation-needed, and they can do it
        # synchronously -- so connecting afterwards loses the signal outright
        # whenever it wins the race. The symptom is the nastiest kind: the
        # guest joins successfully, no offer is ever made, no error is logged
        # anywhere, and they sit on a black screen. It reproduced roughly one
        # join in three.
        self._connect(self.webrtc, "on-negotiation-needed", self._on_negotiation_needed)
        self._connect(self.webrtc, "on-ice-candidate", self._on_ice_candidate)
        self._connect(self.webrtc, "notify::ice-connection-state", self._on_ice_state)
        # `on-negotiation-needed` is connected purely so an early one is not
        # lost; it cannot make the offer, because it fires the moment the
        # element reaches PLAYING -- before the track is linked and before the
        # data channel exists. Offering there produces a valid but *empty*
        # description, the guest answers it agreeably, and nothing ever flows.
        # The offer is made explicitly at the end of assembly instead.

        # NOTE: do not reach for webrtcbin's "ice-agent" here to bound the UDP
        # port range. On GStreamer 1.24 with this PyGObject, simply *reading*
        # that property corrupts the agent -- `g_object_get_qdata: assertion
        # G_IS_OBJECT (object) failed` fires immediately, and the process then
        # dies at negotiation with `gst_webrtc_ice_add_stream: assertion
        # GST_IS_WEBRTC_ICE (ice) failed`. It takes the whole server down the
        # moment the first guest joins, which is the worst possible time.
        #
        # gst_child_proxy is not a way around it either: the agent is not
        # registered as a child at NULL, READY or PAUSED, so the lookup fails.
        # Ports are therefore ephemeral, and `docs/NETWORK.md` says what that
        # means for the firewall rule. Revisit if webrtcbin gains real port
        # properties.

        # Into PLAYING first. `create-data-channel` needs the SCTP transport,
        # which does not exist while the element is still NULL -- it returns
        # None there, and the peer is dead on arrival.
        if not self.webrtc.sync_state_with_parent():
            raise RuntimeError("webrtcbin would not follow the pipeline into PLAYING")

        # Video first, then audio, so the offer's m-lines come out in that
        # order and transceiver 0 is always the picture.
        self._branch(self.stage.tee, "v")
        if self.stage.has_audio:
            self._branch(self.stage.audio_tee, "a")

        index = 0
        while True:
            transceiver = self.webrtc.emit("get-transceiver", index)
            if transceiver is None:
                break
            transceiver.set_property(
                "direction", GstWebRTC.WebRTCRTPTransceiverDirection.SENDONLY)
            index += 1

        # Unreliable and unordered on purpose: pad state is a snapshot, so a
        # retransmitted frame is always worse than the one behind it.
        options = Gst.Structure.new_from_string(
            "options, ordered=(boolean)false, max-retransmits=(int)0")
        self.channel = self.webrtc.emit("create-data-channel", "input", options)
        if self.channel is None:
            raise RuntimeError("webrtcbin would not create the input channel")
        self._connect(self.channel, "on-message-data", self._on_channel_data)
        self._connect(self.channel, "on-open",
                      lambda _c: log.info("peer %s: input open", self.id))
        self._connect(self.channel, "on-close",
                      lambda _c: log.info("peer %s: input closed", self.id))

        self._assembled = True
        self._negotiate()

    def _connect(self, obj, signal, handler):
        self._handlers.append((obj, obj.connect(signal, handler)))

    def _disconnect_all(self):
        """Take our callbacks off before anything is destroyed.

        Order matters more than it looks: tearing down a webrtcbin makes it
        emit -- ICE state changes, a data channel closing -- and by then the
        Python side is halfway through dismantling the very objects those
        handlers reach for.
        """
        for obj, handler_id in self._handlers:
            try:
                obj.disconnect(handler_id)
            except Exception:
                pass                    # already gone is the outcome we wanted
        self._handlers = []

    def _branch(self, tee, tag):
        """One tee -> queue -> webrtcbin path, remembered so it can be undone."""
        queue = Gst.ElementFactory.make("queue", f"q{tag}_{self.id}")
        # Drop the oldest rather than block. One slow guest must never apply
        # back-pressure to a tee that everybody else is reading from.
        queue.set_property("leaky", 2)
        queue.set_property("max-size-time", self.stage.cfg.queue_ms * Gst.MSECOND)
        queue.set_property("max-size-bytes", 0)
        queue.set_property("max-size-buffers", 0)
        self.stage.pipeline.add(queue)
        if not queue.sync_state_with_parent():
            raise RuntimeError(f"{queue.get_name()} would not reach PLAYING")

        tee_pad = tee.request_pad_simple("src_%u")
        tee_pad.link(queue.get_static_pad("sink"))
        src = queue.get_static_pad("src")
        src.link(self.webrtc.request_pad_simple("sink_%u"))
        kind = "video" if tag == "v" else "audio"
        src.add_probe(Gst.PadProbeType.BUFFER,
                      lambda _pad, info, k=kind: self._count(k, info))
        self._branches.append((tee, tee_pad, queue))

    def _count(self, kind, info):
        if self.webrtc is None:
            return Gst.PadProbeReturn.REMOVE
        buffer = info.get_buffer()
        if buffer is not None:
            self.sent[f"{kind}_bytes"] += buffer.get_size()
            self.sent[f"{kind}_packets"] += 1
        return Gst.PadProbeReturn.OK

    def detach(self):
        if self.webrtc is None:
            return
        started = time.monotonic()
        # Before anything else, and before any state change.
        self._disconnect_all()
        self.on_dead = self.on_broken = self.on_input = None
        try:
            for tee, tee_pad, queue in self._branches:
                tee_pad.unlink(queue.get_static_pad("sink"))
                tee.release_request_pad(tee_pad)
                queue.set_state(Gst.State.NULL)
                self.stage.pipeline.remove(queue)
            self._branches = []
            self.webrtc.set_state(Gst.State.NULL)
            self.stage.pipeline.remove(self.webrtc)
        except Exception as exc:
            log.warning("peer %s did not detach cleanly: %s", self.id, exc)
        finally:
            self.webrtc = self.channel = None
            self._branches = []
            took = time.monotonic() - started
            if took > 1.0:
                log.warning("peer %s took %.1fs to detach -- everything queued "
                            "behind it waited", self.id, took)

    # -- negotiation --------------------------------------------------------

    def _on_negotiation_needed(self, _element):
        if self.webrtc is None:
            return
        self._negotiate()

    def _negotiate(self):
        """Make exactly one offer, and only once there is something to offer.

        Guarded twice over. `_assembled` keeps an early request from describing
        a peer with no track and no channel; `_offered` keeps the later ones --
        the video track and the data channel each ask -- from offering again
        while the first is still in flight.
        """
        if self._offered or not self._assembled or self.webrtc is None:
            return
        self._offered = True
        promise = Gst.Promise.new_with_change_func(
            self._on_offer_created, self.webrtc, None)
        self.webrtc.emit("create-offer", None, promise)

    def _on_offer_created(self, promise, element, _data):
        if self.webrtc is None:
            return
        # wait() before get_reply(). The change callback can run before the
        # promise has actually settled, and the reply then carries a NULL
        # description -- which surfaces much later as an AttributeError on
        # `.sdp`, nowhere near the cause.
        promise.wait()
        reply = promise.get_reply()
        if reply is None:
            log.error("peer %s: create-offer returned nothing", self.id)
            return
        offer = reply.get_value("offer")
        if offer is None or offer.sdp is None:
            log.error("peer %s: create-offer produced an empty description", self.id)
            return
        element.emit("set-local-description", offer, Gst.Promise.new())
        self._emit("offer", {"sdp": with_fmtp(offer.sdp.as_text(), self.stage._fmtp),
                             "type": "offer"})

    def _on_ice_candidate(self, _element, mline_index, candidate):
        # Candidate types decide whether anybody outside can reach us at all:
        # `host` is a LAN address, `srflx` is what STUN discovered our public
        # address to be, `relay` came from a TURN server. A guest on the
        # internet with only host candidates offered will connect, negotiate,
        # and show a black screen forever.
        parts = candidate.split()
        kind = ""
        if "typ" in parts:
            index = parts.index("typ")
            if index + 1 < len(parts):
                kind = parts[index + 1]
        if kind and kind not in self._candidate_kinds:
            self._candidate_kinds.add(kind)
            log.info("peer %s: gathered a %s candidate", self.id, kind)
        self._emit("ice", {"candidate": candidate, "sdpMLineIndex": mline_index})

        extra = self._forwarded_candidate(parts, kind)
        if extra:
            log.info("peer %s: also offering %s:%s at the public address",
                     self.id, parts[4] if len(parts) > 4 else "?",
                     parts[5] if len(parts) > 5 else "?")
            self._emit("ice", {"candidate": extra, "sdpMLineIndex": mline_index})

    def _forwarded_candidate(self, parts, kind):
        """The same socket, announced at the public address and *same port*.

        Without this, port forwarding cannot work behind a symmetric NAT --
        which is most home routers. STUN reports the external port the router
        happened to allocate for talking to the STUN server, and that mapping
        is per-destination: no guest can use it. The forward, meanwhile, sends
        WAN 40005 straight to this machine's 40005 and works for everybody --
        and ICE never discovers it, because nothing on this machine can observe
        a static rule in the router.

        So we say it ourselves: for each LAN candidate, an identical one at the
        public address. It is exactly what a 1:1 NAT address is for in every
        other WebRTC server. A guest that cannot reach it simply loses that
        candidate; the LAN ones still work for people in the house.
        """
        public = self.stage.public_ip
        if not public or kind != "host" or len(parts) < 6:
            return None
        address, port = parts[4], parts[5]
        if not net.is_private(address) or ":" in address:
            return None
        # Port 9 is the discard port: ICE-TCP candidates use it as a
        # placeholder for a socket that does not accept datagrams. Shadowing
        # one produces a candidate pointing at nothing.
        if port == "9" or (len(parts) > 2 and parts[2].upper() != "UDP"):
            return None
        if (address, port) in self._announced:
            return None
        self._announced.add((address, port))

        fields = list(parts)
        fields[4] = public
        # Foundation and priority must differ from the candidate it shadows, or
        # a browser treats it as a duplicate and ignores it.
        if fields[0].startswith("candidate:"):
            fields[0] = "candidate:" + fields[0].split(":", 1)[1] + "9"
        try:
            fields[3] = str(max(1, int(fields[3]) - 100))
        except ValueError:
            pass
        # srflx, with the local socket recorded as its base, which is what a
        # reflexive candidate means and what browsers expect to parse.
        typ = fields.index("typ")
        fields[typ + 1] = "srflx"
        fields = fields[:typ + 2] + ["raddr", address, "rport", port]
        return " ".join(fields)

    def _on_ice_state(self, element, _param):
        if self.webrtc is None:
            return                      # torn down while this was in flight
        state = element.get_property("ice-connection-state")
        self.ice_ok = state.value_nick in ("connected", "completed")
        log.info("peer %s: ice %s", self.id, state.value_nick)
        self._emit("ice-state", {"state": state.value_nick})
        # "disconnected" is recoverable and often just a moment of packet loss,
        # so it is deliberately not in here. "failed" and "closed" are not.
        if state.value_nick in ("connected", "completed"):
            self._log_route()
        if state.value_nick in ("failed", "closed") and self.on_dead:
            self.stage.loop.call_soon_threadsafe(
                self.on_dead, f"media connection {state.value_nick}")

    def _log_route(self):
        """Which pair of addresses the media is actually using.

        The one fact worth having when a guest reports a black screen: whether
        the stream is going over the LAN, over the public address, or through a
        relay. Everything else -- ports forwarded, candidates gathered -- is a
        guess about this.
        """
        if self._route_logged or self.webrtc is None:
            return
        self._route_logged = True

        def report(promise, _a, _b):
            try:
                promise.wait()
                stats = promise.get_reply()
                if stats is None:
                    return
                pairs, locals_, remotes = {}, {}, {}
                # Walk the fields by index. GstStructure.foreach's callback
                # signature is awkward from Python and a mismatch there is
                # swallowed as "could not read the route", which is how this
                # silently produced nothing at all the first time.
                for i in range(stats.n_fields()):
                    name = stats.nth_field_name(i)
                    value = stats.get_value(name)
                    if not isinstance(value, Gst.Structure):
                        continue
                    kind = value.get_string("type") or ""
                    if kind == "candidate-pair":
                        pairs[name] = value
                    elif kind == "local-candidate":
                        locals_[value.get_string("id") or name] = value
                    elif kind == "remote-candidate":
                        remotes[value.get_string("id") or name] = value

                for pair in pairs.values():
                    ok, nominated = pair.get_boolean("nominated")
                    if not (ok and nominated):
                        continue
                    log.info("peer %s: media route %s <- %s", self.id,
                             describe(locals_, pair.get_string("local-candidate-id") or ""),
                             describe(remotes, pair.get_string("remote-candidate-id") or ""))
            except Exception as exc:
                log.debug("could not read the media route: %s", exc)

        try:
            self.webrtc.emit("get-stats", None,
                             Gst.Promise.new_with_change_func(report, None, None))
        except Exception as exc:
            log.debug("get-stats unavailable: %s", exc)

    def on_pipeline_thread(self, fn, *args):
        """Run something that touches webrtcbin on the one thread that may.

        GStreamer is thread-safe in principle and the AMD VAAPI driver under it
        is not, in practice: this process took two SIGSEGVs inside
        radeonsi_drv_video.so with PyGObject on the stack, both while a peer
        was being negotiated from the asyncio thread as the encoder ran. Every
        add and remove already goes through one worker; the answer and the
        candidates now go the same way, so nothing reaches webrtcbin from two
        threads at once.
        """
        return self.stage.worker.submit(fn, *args)

    def set_remote_answer(self, sdp_text):
        self.on_pipeline_thread(self._set_remote_answer, sdp_text)

    def _set_remote_answer(self, sdp_text):
        if self.webrtc is None:
            return
        ok, message = GstSdp.SDPMessage.new()
        if ok != GstSdp.SDPResult.OK:
            raise RuntimeError("could not allocate an SDP message")
        GstSdp.sdp_message_parse_buffer(sdp_text.encode(), message)
        answer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.ANSWER, message)
        self.webrtc.emit("set-remote-description", answer, Gst.Promise.new())

    def add_ice_candidate(self, mline_index, candidate):
        self.on_pipeline_thread(self._add_ice_candidate, mline_index, candidate)

    def _add_ice_candidate(self, mline_index, candidate):
        if self.webrtc is not None:
            self.webrtc.emit("add-ice-candidate", mline_index, candidate)

    # -- input --------------------------------------------------------------

    def _on_channel_data(self, _channel, glib_bytes):
        if self.on_input is None or self.webrtc is None:
            return
        data = glib_bytes.get_data() if hasattr(glib_bytes, "get_data") else bytes(glib_bytes)
        self.stage.loop.call_soon_threadsafe(self.on_input, data)

    def _emit(self, kind, payload):
        # Read the callback at call time: the session may have re-pointed it at
        # a new socket since this peer was created.
        self.stage.loop.call_soon_threadsafe(
            lambda: self._on_signal(kind, payload) if self._on_signal else None)
