"""A synthetic media guest, so the leak can be reproduced on demand.

Not in run.sh: it needs a session that is open, and it takes a real slot for
as long as it runs.

It joins a running host over the same websocket protocol a browser uses,
answers the offer with a real webrtcbin, receives the video into a fakesink,
and answers on the pad channel every 50 ms the way a page does whether
anything moved or not. From the host's side it is an ordinary guest: it
reaches `ice completed`, it is sent encoded video, and its pad frames are
counted as input.

    python3 tests/live/leakprobe.py <pin> [seconds]

Built on 2026-09-12 to chase a leak of roughly 300 MB per minute that showed
itself only with real guests. What it has ruled out so far, each measured with
server RSS flat to the megabyte across 75 seconds:

  * video fan-out to a genuine WebRTC peer (10,000+ packets, 13 MB)
  * the same with pad frames arriving at 20 a second (470+ inputs registered)

So the leak is not the plain act of encoding, fanning out, or receiving input.
What a real browser still does that this does not: send periodic stats
reports, open the desk channel, request keyframes over RTCP when it loses
packets ("asked back" in its reports), and renegotiate. The next thing to add
here is keyframe requests, since a leaking session showed 30-47 of them.

Measure from outside rather than trusting any number this prints:

    P=$(systemctl --user show fourth-player -p MainPID --value)
    awk '/VmRSS/{print $2/1024" MB"}' /proc/$P/status
"""
import asyncio, json, os, ssl, struct, sys, gi
gi.require_version("Gst", "1.0"); gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")
from gi.repository import Gst, GstWebRTC, GstSdp, GLib
import websockets

PIN = sys.argv[1]
RUN = float(sys.argv[2]) if len(sys.argv) > 2 else 90.0
Gst.init(None)
LAX = ssl.create_default_context(); LAX.check_hostname = False
LAX.verify_mode = ssl.CERT_NONE

def server_rss():
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as h:
                if b"fourthplayer" not in h.read() or pid == str(os.getpid()):
                    continue
            with open("/proc/%s/status" % pid) as h:
                for line in h:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) // 1024
        except OSError:
            pass
    return 0

class Probe:
    def __init__(self):
        self.pipe = Gst.Pipeline.new("probe")
        self.webrtc = Gst.ElementFactory.make("webrtcbin", "recv")
        self.webrtc.set_property("bundle-policy", 3)   # max-bundle
        self.pipe.add(self.webrtc)
        self.webrtc.connect("pad-added", self.on_pad)
        self.webrtc.connect("on-data-channel", self.on_channel)
        self.input = None
        self.seq = 0
        self.sent = 0
        self.webrtc.connect("on-ice-candidate", self.on_ice)
        self.ws = None
        self.loop = asyncio.get_event_loop()
        self.pipe.set_state(Gst.State.PLAYING)

    def on_channel(self, _el, channel):
        # The host creates the pad channel; a real page answers on it every
        # 50 ms whether anything moved or not.
        if channel.get_property("label") != "input":
            return
        self.input = channel
        self.loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self.pump()))

    async def pump(self):
        FRAME = struct.Struct("<BBHI6h")
        while True:
            await asyncio.sleep(0.05)
            if self.input is None:
                continue
            try:
                # A snapshot with nothing pressed, exactly like a page whose
                # guest is holding still. Buttons alternate so it is not
                # deduplicated anywhere as an unchanged frame.
                buttons = 1 if (self.seq // 20) % 2 else 0
                data = FRAME.pack(1, 0, self.seq & 0xffff, buttons,
                                  0, 0, 0, 0, 0, 0)
                self.input.emit("send-data", GLib.Bytes.new(data))
                self.seq += 1
                self.sent += 1
            except Exception as exc:
                print("pad send failed:", exc, flush=True)
                return

    def on_pad(self, _el, pad):
        if pad.direction != Gst.PadDirection.SRC:
            return
        sink = Gst.ElementFactory.make("fakesink")
        sink.set_property("sync", False)
        self.pipe.add(sink); sink.sync_state_with_parent()
        pad.link(sink.get_static_pad("sink"))

    def on_ice(self, _el, mline, candidate):
        asyncio.run_coroutine_threadsafe(self.ws.send(json.dumps(
            {"t": "ice", "candidate": candidate, "sdpMLineIndex": mline})),
            self.loop)

    def answer(self, sdp_text):
        ok, sdp = GstSdp.SDPMessage.new_from_text(sdp_text)
        offer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.OFFER, sdp)
        self.webrtc.emit("set-remote-description", offer, None)
        promise = Gst.Promise.new()
        self.webrtc.emit("create-answer", None, promise)
        promise.wait()
        reply = promise.get_reply()
        ans = reply.get_value("answer")
        self.webrtc.emit("set-local-description", ans, Gst.Promise.new())
        return ans.sdp.as_text()

async def main():
    probe = Probe()
    async with websockets.connect("wss://127.0.0.1:8443/ws", ssl=LAX,
                                  max_size=2**22) as ws:
        probe.ws = ws
        await ws.send(json.dumps({"t": "join", "pin": PIN, "name": "leakprobe",
                                  "codecs": ["h264"],
                                  "h264_profiles": ["42e0"]}))
        start = asyncio.get_event_loop().time()
        first = None
        while asyncio.get_event_loop().time() - start < RUN:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                now = server_rss()
                if first is None: first = now
                print("  t=%3.0fs pad frames sent=%5d" % (
                    asyncio.get_event_loop().time() - start,
                    probe.sent), flush=True)
                continue
            msg = json.loads(raw)
            kind = msg.get("t")
            if kind == "joined":
                print("joined as slot", msg.get("slot"), flush=True)
            elif kind == "error":
                print("REFUSED:", msg.get("message")); return
            elif kind == "offer":
                sdp = probe.answer(msg["sdp"])
                await ws.send(json.dumps({"t": "answer", "sdp": sdp}))
                print("answered the offer", flush=True)
            elif kind == "ice" and msg.get("candidate"):
                probe.webrtc.emit("add-ice-candidate",
                                  msg.get("sdpMLineIndex", 0), msg["candidate"])
        print("done; server rss=%d MB (was %s)" % (server_rss(), first))

asyncio.get_event_loop().run_until_complete(main())
