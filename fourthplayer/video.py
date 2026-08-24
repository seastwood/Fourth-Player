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

import logging
import threading

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst, GstWebRTC, GstSdp, GstVideo, GLib  # noqa: E402

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

        # A keyframe a second unless told otherwise, whatever the frame rate.
        keyint = cfg.keyframe_interval or max(1, cfg.fps)
        encoder = (
            f"vah264enc name=enc target-usage={cfg.target_usage} "
            f"bitrate={cfg.bitrate_kbps} key-int-max={keyint} b-frames=0"
            if cfg.hardware_encode else
            f"x264enc name=enc speed-preset=ultrafast tune=zerolatency "
            f"bitrate={cfg.bitrate_kbps} key-int-max={keyint}"
        )
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
            f"! h264parse config-interval=-1 "
            f"! rtph264pay pt=96 config-interval=-1 aggregate-mode=zero-latency "
            f"! application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000 "
            f"! tee name=vtee allow-not-linked=true"
        )
        log.debug("pipeline: %s", description)
        self.pipeline = Gst.parse_launch(description)
        self.tee = self.pipeline.get_by_name("vtee")
        self.encoder = self.pipeline.get_by_name("enc")

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        bus.connect("message::warning", self._on_warning)

    # -- lifecycle ----------------------------------------------------------

    def start(self):
        self._glib_loop = GLib.MainLoop()
        self._thread = threading.Thread(target=self._glib_loop.run,
                                        name="gst-mainloop", daemon=True)
        self._thread.start()
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
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
            raise RuntimeError(
                f"the capture pipeline stalled reaching PLAYING (got {state.value_nick})")
        log.info("capture running: %dx%d @%d, %d kb/s",
                 self.cfg.width, self.cfg.height, self.cfg.fps, self.cfg.bitrate_kbps)

    def stop(self):
        for peer_id in list(self.peers):
            self.remove_peer(peer_id)
        self.pipeline.set_state(Gst.State.NULL)
        if self._glib_loop:
            self._glib_loop.quit()

    # -- peers --------------------------------------------------------------

    def add_peer(self, peer_id, on_signal):
        """Attach one guest. `on_signal(kind, payload)` is called on the asyncio loop."""
        if peer_id in self.peers:
            raise KeyError(f"peer {peer_id} is already attached")
        peer = Peer(self, peer_id, on_signal)
        self.peers[peer_id] = peer
        peer.attach()
        # A guest who joins between keyframes sees nothing until the next one.
        # At a one-second interval that is a second of black, so ask for one now.
        self.force_keyframe()
        return peer

    def remove_peer(self, peer_id):
        peer = self.peers.pop(peer_id, None)
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
        self.queue = None
        self.webrtc = None
        self._tee_pad = None
        self._offered = False
        self._assembled = False

    # -- wiring -------------------------------------------------------------

    def attach(self):
        cfg = self.stage.cfg
        pipeline = self.stage.pipeline

        self.queue = Gst.ElementFactory.make("queue", f"q_{self.id}")
        # Drop the oldest frames rather than block. One slow guest must never
        # apply back-pressure to a tee that everybody else is reading from.
        self.queue.set_property("leaky", 2)
        self.queue.set_property("max-size-time", 200 * Gst.MSECOND)
        self.queue.set_property("max-size-bytes", 0)
        self.queue.set_property("max-size-buffers", 0)

        self.webrtc = Gst.ElementFactory.make("webrtcbin", f"peer_{self.id}")
        self.webrtc.set_property("bundle-policy", "max-bundle")
        self.webrtc.set_property("latency", cfg.jitter_ms)
        if cfg.stun_server:
            self.webrtc.set_property("stun-server", cfg.stun_server)
        if cfg.turn_server:
            self.webrtc.set_property("turn-server", cfg.turn_server)

        pipeline.add(self.queue)
        pipeline.add(self.webrtc)

        # Connect the signals BEFORE anything that can make webrtcbin want to
        # negotiate. Creating the data channel and following the pipeline into
        # PLAYING both emit on-negotiation-needed, and they can do it
        # synchronously -- so connecting afterwards loses the signal outright
        # whenever it wins the race. The symptom is the nastiest kind: the
        # guest joins successfully, no offer is ever made, no error is logged
        # anywhere, and they sit on a black screen. It reproduced roughly one
        # join in three.
        self.webrtc.connect("on-negotiation-needed", self._on_negotiation_needed)
        self.webrtc.connect("on-ice-candidate", self._on_ice_candidate)
        self.webrtc.connect("notify::ice-connection-state", self._on_ice_state)
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
        for element in (self.queue, self.webrtc):
            if not element.sync_state_with_parent():
                raise RuntimeError(
                    f"{element.get_name()} would not follow the pipeline into PLAYING")

        self._tee_pad = self.stage.tee.request_pad_simple("src_%u")
        self._tee_pad.link(self.queue.get_static_pad("sink"))
        self.queue.get_static_pad("src").link(
            self.webrtc.request_pad_simple("sink_%u"))

        transceiver = self.webrtc.emit("get-transceiver", 0)
        if transceiver:
            transceiver.set_property(
                "direction", GstWebRTC.WebRTCRTPTransceiverDirection.SENDONLY)

        # Unreliable and unordered on purpose: pad state is a snapshot, so a
        # retransmitted frame is always worse than the one behind it.
        options = Gst.Structure.new_from_string(
            "options, ordered=(boolean)false, max-retransmits=(int)0")
        self.channel = self.webrtc.emit("create-data-channel", "input", options)
        if self.channel is None:
            raise RuntimeError("webrtcbin would not create the input channel")
        self.channel.connect("on-message-data", self._on_channel_data)
        self.channel.connect("on-open", lambda _c: log.info("peer %s: input open", self.id))
        self.channel.connect("on-close", lambda _c: log.info("peer %s: input closed", self.id))

        self._assembled = True
        self._negotiate()

    def detach(self):
        if self.webrtc is None:
            return
        try:
            if self._tee_pad:
                self._tee_pad.unlink(self.queue.get_static_pad("sink"))
                self.stage.tee.release_request_pad(self._tee_pad)
            for element in (self.webrtc, self.queue):
                element.set_state(Gst.State.NULL)
                self.stage.pipeline.remove(element)
        except Exception as exc:
            log.warning("peer %s did not detach cleanly: %s", self.id, exc)
        finally:
            self.webrtc = self.queue = self._tee_pad = self.channel = None

    # -- negotiation --------------------------------------------------------

    def _on_negotiation_needed(self, _element):
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
        self._emit("offer", {"sdp": offer.sdp.as_text(), "type": "offer"})

    def _on_ice_candidate(self, _element, mline_index, candidate):
        self._emit("ice", {"candidate": candidate, "sdpMLineIndex": mline_index})

    def _on_ice_state(self, element, _param):
        state = element.get_property("ice-connection-state")
        log.info("peer %s: ice %s", self.id, state.value_nick)
        self._emit("ice-state", {"state": state.value_nick})

    def set_remote_answer(self, sdp_text):
        ok, message = GstSdp.SDPMessage.new()
        if ok != GstSdp.SDPResult.OK:
            raise RuntimeError("could not allocate an SDP message")
        GstSdp.sdp_message_parse_buffer(sdp_text.encode(), message)
        answer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.ANSWER, message)
        self.webrtc.emit("set-remote-description", answer, Gst.Promise.new())

    def add_ice_candidate(self, mline_index, candidate):
        self.webrtc.emit("add-ice-candidate", mline_index, candidate)

    # -- input --------------------------------------------------------------

    def _on_channel_data(self, _channel, glib_bytes):
        if self.on_input is None:
            return
        data = glib_bytes.get_data() if hasattr(glib_bytes, "get_data") else bytes(glib_bytes)
        self.stage.loop.call_soon_threadsafe(self.on_input, data)

    def _emit(self, kind, payload):
        self.stage.loop.call_soon_threadsafe(self._on_signal, kind, payload)
