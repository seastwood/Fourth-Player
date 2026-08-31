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


# What this machine can produce, in the order we would prefer it. H.265 is
# roughly half the bitrate for the same picture, which on a thin link is a
# better picture rather than a faster one.
CODEC_PREFERENCE = ("h265", "h264")

_ELEMENTS = {
    "h264": ("vah264enc", "x264enc", "h264parse", "rtph264pay"),
    "h265": ("vah265enc", "x265enc", "h265parse", "rtph265pay"),
}

_host_codecs = None


# The shortest gap between keyframes forced by guests asking for one.
KEYFRAME_MIN_GAP = 0.5


def host_codecs(hardware=True):
    """The codecs this host can actually encode, best first.

    Probed once and remembered. Presence of an element is not quite proof it
    will run, but it is what can be known without building a pipeline, and a
    codec that then fails to start is caught by the usual fallback.
    """
    global _host_codecs
    if _host_codecs is not None:
        return _host_codecs
    init()
    found = []
    for codec in CODEC_PREFERENCE:
        va, sw, parser, payloader = _ELEMENTS[codec]
        # Fall back the same way the pipeline does, or this promises H.265 on
        # a machine that has no VA driver and the offer is a lie.
        encoder = va if (hardware and Gst.ElementFactory.find(va)) else sw
        if (Gst.ElementFactory.find(encoder)
                and Gst.ElementFactory.find(parser)
                and Gst.ElementFactory.find(payloader)):
            found.append(codec)
    if not found:
        found = ["h264"]
    _host_codecs = found
    log.info("this host can encode: %s", ", ".join(found))
    return found


def best_shared_codec(guest_codecs, hardware=True):
    """The best codec both ends can manage, or h264 if they cannot agree.

    A guest that tells us nothing gets H.264, which every browser decodes --
    guessing better than that on no information is how a black screen happens.
    """
    ours = host_codecs(hardware)
    theirs = {str(c).lower().replace("video/", "") for c in (guest_codecs or [])}
    if not theirs:
        return "h264" if "h264" in ours else ours[0]
    for codec in ours:
        if codec in theirs:
            return codec
    return "h264"


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


def describe_sdp(text):
    """The shape of an SDP in one line, for the log.

    Whether the two sides bundled decides how many transports have to connect,
    and a session that bundles on the first connection and not on the rebuilt
    one is two different connections wearing the same name -- which is not
    visible from anything else that gets logged.
    """
    kinds = [line.split()[0][2:] for line in text.splitlines()
             if line.startswith("m=")]
    ports = [line.split()[1] for line in text.splitlines() if line.startswith("m=")]
    bundled = any(line.startswith("a=group:BUNDLE") for line in text.splitlines())
    refused = [k for k, port in zip(kinds, ports) if port == "0"]
    return "%d m-line(s) [%s]%s%s" % (
        len(kinds), ", ".join(kinds),
        ", bundled" if bundled else ", NOT bundled",
        ", refused: " + ",".join(refused) if refused else "")


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

    def __init__(self, cfg, loop, codec=None):
        init()
        self.cfg = cfg
        # The codec for this capture, which may differ from the configured one
        # when it was negotiated with a guest.
        self.codec = (codec or cfg.codec or "h264").lower()
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
        self._last_keyframe = 0.0

        keyint = cfg.keyframe_interval or max(1, cfg.fps * 2)
        # Bits the encoder may hold back to smooth a burst. Smoothing is delay.
        cpb = max(16, int(cfg.bitrate_kbps * cfg.cpb_ms / 1000))
        hevc = self.codec in ("h265", "hevc")
        # Asking for hardware that is not there used to fail at pipeline
        # construction -- `no element "vapostproc"` -- and the session simply
        # would not start. On a machine with no VA driver, which is every
        # virtual machine and plenty of real ones, that is the first thing a
        # new install does and the last thing it explains. `check` noticed and
        # told the user to edit the config; doing it here means they never have
        # to. Software encoding is slower, not broken.
        hardware = cfg.hardware_encode
        if hardware:
            missing = [name for name in
                       (("vah265enc" if hevc else "vah264enc"), "vapostproc")
                       if not Gst.ElementFactory.find(name)]
            if missing:
                log.warning("no %s on this machine, so encoding in software "
                            "instead (slower, and it works)",
                            " or ".join(missing))
                hardware = False
        if hardware:
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
        self.encoding = encoding
        convert = (f"vapostproc ! {_caps(cfg.width, cfg.height)}"
                   if hardware else
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
            f"! appsink name=vsink emit-signals=true sync=false "
            f"max-buffers=4 drop=true"
        )
        # Named so they can be found and silenced directly when stopping.
        description = description.replace("ximagesrc ", "ximagesrc name=capture ")
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
            f" pulsesrc name=sound device={cfg.audio_device} provide-clock=false "
            f"! audioconvert ! audioresample "
            f"! audio/x-raw,rate=48000,channels=2 "
            f"! opusenc bitrate={cfg.audio_bitrate_kbps * 1000} "
            f"frame-size={cfg.audio_frame_ms} inband-fec=true "
            f"! rtpopuspay pt=97 mtu={cfg.rtp_mtu} "
            f"! application/x-rtp,media=audio,encoding-name=OPUS,payload=97,clock-rate=48000 "
            f"! appsink name=asink emit-signals=true sync=false "
            f"max-buffers=16 drop=true")

    def _build(self, with_audio):
        description = self._description
        if with_audio:
            description += self._audio_description
        log.debug("pipeline: %s", description)
        self.pipeline = Gst.parse_launch(description)
        self.encoder = self.pipeline.get_by_name("enc")
        self.vsink = self.pipeline.get_by_name("vsink")
        self.asink = self.pipeline.get_by_name("asink")
        self.has_audio = self.asink is not None
        # The caps the guests' pipelines have to be told about. They are not
        # known until the first buffer arrives.
        self.video_caps = None
        self.audio_caps = None
        self.vsink.connect("new-sample", self._on_video)
        if self.asink is not None:
            self.asink.connect("new-sample", self._on_audio)

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
        """Stop capturing. Must not block, whatever state anything is in.

        The pipeline is silenced first and the worker is never waited for. It
        used to be waited for, and the case that matters is precisely the one
        where it will not answer: a stage is replaced *because* its worker
        wedged, so stopping it hung, the old pipeline kept capturing, and the
        replacements piled up. Five ximagesrc threads were found running at
        once, which is five screen captures competing for one GPU.
        """
        # Silence the sources first. Taking a whole pipeline to NULL can block
        # for a long time when webrtcbin has live transports in it, and an
        # abandoned pipeline that is still capturing costs a screen grab and an
        # encode for as long as the process lives. Stopping ximagesrc and
        # pulsesrc is immediate and ends that cost even if the rest hangs.
        for name in ("capture", "sound"):
            element = self.pipeline.get_by_name(name)
            if element is not None:
                try:
                    element.set_state(Gst.State.NULL)
                except Exception:
                    pass
        for element in (self.encoder,):
            if element is not None:
                try:
                    element.set_state(Gst.State.NULL)
                except Exception:
                    pass
        try:
            self.pipeline.set_state(Gst.State.NULL)
        except Exception as exc:
            log.warning("could not stop the pipeline cleanly: %s", exc)
        for peer_id in list(self.peers):
            peer = self.peers.pop(peer_id, None)
            if peer is not None:
                try:
                    peer.detach()
                except Exception:
                    pass
        if self._glib_loop:
            self._glib_loop.quit()
        self.worker.shutdown(wait=False)

    def ensure_playing(self, timeout=5 * Gst.SECOND):
        """Put the pipeline back to PLAYING if something knocked it out.

        A GStreamer error is posted against the whole pipeline, not the branch
        that raised it -- so one guest's data channel failing left the capture
        stopped, and every later guest was refused with "webrtcbin would not
        follow the pipeline into PLAYING". The session stayed open and served
        nobody.
        """
        _change, state, _pending = self.pipeline.get_state(0)
        if state == Gst.State.PLAYING:
            return True
        log.warning("pipeline is %s; putting it back to PLAYING",
                    state.value_nick)
        self.pipeline.set_state(Gst.State.PLAYING)
        _change, state, _pending = self.pipeline.get_state(timeout)
        if state != Gst.State.PLAYING:
            log.error("pipeline would not return to PLAYING (%s)", state.value_nick)
            return False
        return True

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

        # The capture still has to be running to have anything to hand out.
        # It is no longer at the mercy of the guests, though: their pipelines
        # are their own, so this is now only about the capture itself.
        if not self.ensure_playing():
            raise RuntimeError("the capture pipeline is not running")

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

    def request_keyframe(self, who=""):
        """A guest has lost the picture and wants a fresh start.

        Rate-limited, because the encoder is shared: four guests on a bad
        connection all asking at once would otherwise turn the stream into
        keyframes, which is the one thing guaranteed to make a struggling link
        worse. One every half second is enough to recover in a blink and not
        enough to matter to the bitrate.
        """
        now = time.monotonic()
        if now - self._last_keyframe < KEYFRAME_MIN_GAP:
            return
        self._last_keyframe = now
        log.info("peer %s asked for a keyframe after losing the picture", who)
        self.worker.submit(self.force_keyframe)

    def force_keyframe(self):
        pad = self.encoder.get_static_pad("src")
        if pad:
            pad.send_event(GstVideo.video_event_new_upstream_force_key_unit(
                Gst.CLOCK_TIME_NONE, True, 0))

    # -- bus ----------------------------------------------------------------

    def _on_error(self, _bus, message):
        err, debug = message.parse_error()
        log.error("pipeline error: %s (%s)", err.message, debug)
        # This bus carries the capture only. A guest's pipeline has its own bus
        # and its own errors, which is the point of them being separate.
        self.worker.submit(self.ensure_playing)

    # -- fanning the encoded stream out to the guests ------------------------

    def _on_video(self, sink):
        return self._forward(sink, "video")

    def _on_audio(self, sink):
        return self._forward(sink, "audio")

    def _forward(self, sink, kind):
        """Hand one encoded packet to every guest.

        This is where the guests stop sharing anything. Each one has its own
        pipeline, so a failure inside theirs -- a data channel giving up, a
        transport erroring -- is theirs alone. It used to be a `tee` inside one
        pipeline, and a GStreamer error belongs to the pipeline rather than the
        branch that raised it: one guest's SCTP association failing therefore
        stopped the capture and ended the session for everybody.
        """
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buffer = sample.get_buffer()
        caps = sample.get_caps()
        if kind == "video":
            self.video_caps = caps
        else:
            self.audio_caps = caps
        for peer in list(self.peers.values()):
            try:
                peer.push(kind, buffer, caps)
            except Exception as exc:
                log.debug("peer %s would not take a %s buffer: %s",
                          peer.id, kind, exc)
        return Gst.FlowReturn.OK

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
        self.pipeline = None          # this guest's own pipeline
        self._sources = {}            # kind -> appsrc
        self._caps = {}               # kind -> caps last set
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
        """Build this guest's own pipeline: appsrc in, webrtcbin out."""
        cfg = self.stage.cfg

        # A pipeline of their own. Everything that can go wrong for this guest
        # now goes wrong in here, where it reaches nobody else.
        self.pipeline = Gst.Pipeline.new(f"guest_{self.id}")
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        self._connect(bus, "message::error", self._on_own_error)

        self.ice = make_ice_agent(cfg)
        if self.ice is not None:
            self.webrtc = Gst.ElementFactory.make_with_properties(
                "webrtcbin", ["ice-agent"], [self.ice])
        else:
            self.webrtc = None
        if self.webrtc is None:
            self.webrtc = Gst.ElementFactory.make("webrtcbin", None)
        self.webrtc.set_property("name", f"peer_{self.id}")
        self.webrtc.set_property("bundle-policy", "max-bundle")
        self.webrtc.set_property("latency", cfg.jitter_ms)
        if cfg.stun_server:
            self.webrtc.set_property("stun-server", cfg.stun_server)
        if cfg.turn_server:
            self.webrtc.set_property("turn-server", cfg.turn_server)
        self.pipeline.add(self.webrtc)

        self._connect(self.webrtc, "on-negotiation-needed", self._on_negotiation_needed)
        self._connect(self.webrtc, "on-ice-candidate", self._on_ice_candidate)
        self._connect(self.webrtc, "notify::ice-connection-state", self._on_ice_state)

        # Video first so the offer's m-lines come out in that order and
        # transceiver 0 is always the picture.
        self._feed("video", None)
        if self.stage.has_audio:
            self._feed("audio", None)

        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("this guest's pipeline would not start")

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

    def _rtp_caps(self, kind):
        """The caps this guest's appsrc announces, stated rather than waited for.

        webrtcbin builds the offer from whatever the source claims the moment
        it is asked, and a sample from the capture may not have arrived yet --
        so an appsrc left to learn its caps from the first buffer produced an
        offer with no video in it at all. The guest negotiated audio, took
        input, and never got a picture.

        These are known: this program built the pipeline that produces them.
        """
        if kind == "audio":
            # encoding-params is the channel count, and leaving it out is not
            # cosmetic: it becomes "a=rtpmap:97 OPUS/48000" in the offer, where
            # every browser expects "opus/48000/2". Stating the caps by hand
            # dropped it, and the sound went with it -- the GStreamer test
            # guest accepts either, so nothing caught it.
            return Gst.Caps.from_string(
                "application/x-rtp,media=(string)audio,encoding-name=(string)OPUS,"
                "payload=(int)97,clock-rate=(int)48000,encoding-params=(string)2")
        encoding = self.stage.encoding
        return Gst.Caps.from_string(
            f"application/x-rtp,media=(string)video,encoding-name=(string){encoding},"
            f"payload=(int)96,clock-rate=(int)90000")

    def _feed(self, kind, caps):
        """One appsrc carrying the encoded stream into this guest's webrtcbin."""
        caps = caps or self._rtp_caps(kind)
        src = Gst.ElementFactory.make("appsrc", f"{kind}src_{self.id}")
        src.set_property("is-live", True)
        src.set_property("format", Gst.Format.TIME)
        src.set_property("emit-signals", False)
        # Never block the capture thread, and never grow without limit: a guest
        # whose connection has stalled drops packets instead of holding the
        # encoder up or eating memory.
        src.set_property("block", False)
        # The buffers come from another pipeline with its own clock and base
        # time, so their timestamps mean nothing here. Let the source stamp
        # them on arrival instead of handing webrtcbin times it cannot place.
        src.set_property("do-timestamp", True)
        src.set_property("max-bytes", 2 * 1024 * 1024)
        try:
            src.set_property("leaky-type", 2)      # drop the oldest
        except Exception:
            pass                                    # older GStreamer: fine
        src.set_property("caps", caps)
        self._caps[kind] = caps
        if kind == "video":
            # A browser that has lost a frame asks for a new keyframe, and
            # webrtcbin turns that request into an upstream force-key-unit
            # event. It arrives here and stops: the encoder is in the capture
            # pipeline, not this one, so nothing was listening and the guest
            # waited for the next periodic keyframe -- two seconds at thirty
            # frames a second. That is the black screen after a blip.
            pad = src.get_static_pad("src")
            if pad is not None:
                pad.add_probe(Gst.PadProbeType.EVENT_UPSTREAM,
                              self._on_upstream)
        self.pipeline.add(src)
        src.link_pads("src", self.webrtc, "sink_%u")
        self._sources[kind] = src

    def _on_upstream(self, _pad, info):
        """Pass a guest's request for a keyframe across to the encoder."""
        event = info.get_event()
        if event is not None and event.type == Gst.EventType.CUSTOM_UPSTREAM:
            structure = event.get_structure()
            if structure is not None and structure.has_name("GstForceKeyUnit"):
                self.stage.request_keyframe(self.id)
        return Gst.PadProbeReturn.OK

    def push(self, kind, buffer, caps):
        """Take one encoded packet from the capture."""
        src = self._sources.get(kind)
        if src is None or self.webrtc is None:
            return
        # The caps were stated when the source was made and are not changed
        # here: renegotiating mid-stream on a cosmetic difference would
        # interrupt a picture that is working.
        #
        # A shallow copy per guest, with the capture's timestamps cleared: the
        # same buffer goes to several pipelines, so it must not be written to,
        # and the times on it belong to a clock this pipeline has never seen.
        outgoing = buffer.copy()
        outgoing.pts = Gst.CLOCK_TIME_NONE
        outgoing.dts = Gst.CLOCK_TIME_NONE
        outgoing.duration = Gst.CLOCK_TIME_NONE
        src.emit("push-buffer", outgoing)
        self.sent[f"{kind}_bytes"] += buffer.get_size()
        self.sent[f"{kind}_packets"] += 1

    def _on_own_error(self, _bus, message):
        """An error inside this guest's pipeline, and nobody else's."""
        err, debug = message.parse_error()
        log.warning("peer %s: %s (%s)", self.id, err.message, debug)
        if self.on_broken is not None:
            self.stage.loop.call_soon_threadsafe(self.on_broken, err.message)

    def detach(self):
        if self.pipeline is None:
            return
        started = time.monotonic()
        self._disconnect_all()
        self.on_dead = self.on_broken = self.on_input = None
        pipeline, self.pipeline = self.pipeline, None
        self.webrtc = self.channel = None
        self._sources = {}
        try:
            pipeline.set_state(Gst.State.NULL)
        except Exception as exc:
            log.warning("peer %s did not stop cleanly: %s", self.id, exc)
        took = time.monotonic() - started
        if took > 1.0:
            log.warning("peer %s took %.1fs to detach", self.id, took)

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
        text = with_fmtp(offer.sdp.as_text(), self.stage._fmtp)
        log.info("peer %s: offering %s", self.id, describe_sdp(text))
        self._emit("offer", {"sdp": text, "type": "offer"})

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
        # And the priority must be a real srflx priority, not a host one.
        # This used to subtract 100, which left it ranked level with the LAN
        # candidate it shadows -- so a guest in the house would nominate the
        # public address, and if anything on the way (a forward, hairpin NAT)
        # was not working they got a connected session with no picture, while
        # the LAN path that would have worked sat unused. ICE priority is
        # (2^24 * type preference) + ..., with host 126 and srflx 100, so a
        # genuine srflx sits exactly this far below its own base. Announcing
        # it at its true rank makes it what it was always described as: the
        # fallback for people who cannot reach the LAN address.
        try:
            fields[3] = str(max(1, int(fields[3]) - (126 - 100) * (1 << 24)))
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
        log.info("peer %s: answered with %s", self.id, describe_sdp(sdp_text))
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
