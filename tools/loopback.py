#!/usr/bin/env python3
"""A guest with no browser: joins a live session and proves the path works.

Chrome is not available on a headless box and cannot be scripted to hold a
gamepad anyway, so this stands in for one. It speaks the same WebSocket
signalling a browser speaks, answers the same offer, receives the same RTP and
sends the same 20-byte frames on the same data channel -- using a second
webrtcbin as the peer.

What it therefore proves: signalling, ICE, DTLS, SRTP, the video actually
decoding into H.264 access units, the data channel, and a guest's frames
arriving at a real uinput device. What it cannot prove is the JavaScript, which
is what `tests/test_webframe.py` and a human with a controller are for.

    python3 tools/loopback.py --seconds 10
"""

import argparse
import asyncio
import json
import os
import socket
import ssl
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")
from gi.repository import Gst, GstWebRTC, GstSdp, GLib  # noqa: E402

import websockets  # noqa: E402

from fourthplayer.config import Config  # noqa: E402
from fourthplayer.server import CONTROL_SOCKET  # noqa: E402
from fourthplayer import protocol as P  # noqa: E402

counts = {"video_buffers": 0, "video_bytes": 0, "keyframes": 0,
          "audio_buffers": 0, "audio_bytes": 0}


def control(request):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(10)
        sock.connect(CONTROL_SOCKET)
        sock.sendall((json.dumps(request) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data or b"{}")


class Guest:
    def __init__(self, loop):
        self.loop = loop
        self.pipeline = Gst.Pipeline.new("guest")
        self.webrtc = Gst.ElementFactory.make("webrtcbin", "guest")
        self.webrtc.set_property("bundle-policy", "max-bundle")
        self.pipeline.add(self.webrtc)
        self.channel = None
        self.socket = None
        self.frames_sent = 0
        self.webrtc.connect("pad-added", self._on_pad)
        self.webrtc.connect("on-ice-candidate", self._on_ice)
        self.webrtc.connect("on-data-channel", self._on_data_channel)
        self.webrtc.connect("notify::ice-connection-state", self._on_ice_state)
        self.ice_state = "new"

    def start(self):
        self.pipeline.set_state(Gst.State.PLAYING)

    def stop(self):
        self.pipeline.set_state(Gst.State.NULL)

    # -- receiving media ----------------------------------------------------

    def _on_pad(self, _element, pad):
        if pad.direction != Gst.PadDirection.SRC:
            return
        caps = pad.get_current_caps() or pad.query_caps(None)
        encoding = ""
        if caps and caps.get_size():
            encoding = (caps.get_structure(0).get_string("encoding-name") or "").upper()

        if encoding == "OPUS":
            chain = ["rtpopusdepay", "opusparse"]
            counter = self._count_audio
        else:
            chain = ["rtph264depay", "h264parse"]
            counter = self._count_video

        elements = [Gst.ElementFactory.make(name) for name in chain]
        sink = Gst.ElementFactory.make("fakesink")
        sink.set_property("sync", False)
        elements.append(sink)
        if any(e is None for e in elements):
            print(f"  cannot handle a {encoding or 'video'} track here")
            return
        for element in elements:
            self.pipeline.add(element)
            element.sync_state_with_parent()
        for a, b in zip(elements, elements[1:]):
            a.link(b)
        pad.link(elements[0].get_static_pad("sink"))
        elements[-2].get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, counter)
        print(f"  receiving {encoding or 'H264'}")

    def _count_video(self, _pad, info):
        buffer = info.get_buffer()
        counts["video_buffers"] += 1
        counts["video_bytes"] += buffer.get_size()
        if not buffer.has_flags(Gst.BufferFlags.DELTA_UNIT):
            counts["keyframes"] += 1
        return Gst.PadProbeReturn.OK

    def _count_audio(self, _pad, info):
        counts["audio_buffers"] += 1
        counts["audio_bytes"] += info.get_buffer().get_size()
        return Gst.PadProbeReturn.OK

    # -- signalling ---------------------------------------------------------

    def _on_ice(self, _element, mline_index, candidate):
        self._send({"t": "ice", "candidate": candidate, "sdpMLineIndex": mline_index})

    def _on_ice_state(self, element, _param):
        self.ice_state = element.get_property("ice-connection-state").value_nick

    def _on_data_channel(self, _element, channel):
        self.channel = channel
        channel.connect("on-open", lambda _c: print("  data channel open"))

    def _send(self, message):
        if self.socket:
            self.loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self.socket.send(json.dumps(message))))

    def accept_offer(self, sdp_text):
        ok, message = GstSdp.SDPMessage.new()
        GstSdp.sdp_message_parse_buffer(sdp_text.encode(), message)
        offer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.OFFER, message)
        promise = Gst.Promise.new_with_change_func(self._offer_set, None, None)
        self.webrtc.emit("set-remote-description", offer, promise)

    def _offer_set(self, promise, _a, _b):
        promise.wait()
        answer_promise = Gst.Promise.new_with_change_func(self._answer_made, None, None)
        self.webrtc.emit("create-answer", None, answer_promise)

    def _answer_made(self, promise, _a, _b):
        promise.wait()
        reply = promise.get_reply()
        if reply is None:
            print("  create-answer returned nothing")
            return
        answer = reply.get_value("answer")
        if answer is None or answer.sdp is None:
            print("  create-answer produced an empty description")
            return
        self.webrtc.emit("set-local-description", answer, Gst.Promise.new())
        self._send({"t": "answer", "sdp": answer.sdp.as_text()})

    def add_ice(self, mline_index, candidate):
        self.webrtc.emit("add-ice-candidate", mline_index, candidate)

    # -- sending input ------------------------------------------------------

    def press(self, seq, buttons=0, axes=None):
        if self.channel is None:
            return False
        state = P.PadState(seq=seq, buttons=buttons, axes=axes or [0] * 6)
        self.channel.emit("send-data", GLib.Bytes.new(P.encode(state)))
        self.frames_sent += 1
        return True


async def run(args):
    Gst.init(None)
    loop = asyncio.get_running_loop()

    status = control({"cmd": "status"})
    if not status.get("open"):
        print("no session is open -- start one first:\n"
              "    python3 -m fourthplayer start --minutes 5", file=sys.stderr)
        return 2
    url, pin = status["url"], status["pin"]
    print(f"joining {url}")

    token = url.rsplit("/j/", 1)[1]
    cfg = Config.load()
    scheme = "ws" if cfg.behind_proxy else "wss"
    context = None
    if scheme == "wss":
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    guest = Guest(loop)
    glib_loop = GLib.MainLoop()
    import threading
    threading.Thread(target=glib_loop.run, daemon=True).start()
    guest.start()

    endpoint = f"{scheme}://127.0.0.1:{cfg.port}/ws"
    async with websockets.connect(endpoint, ssl=context) as socket_:
        guest.socket = socket_
        if args.resume:
            with open(args.token_file) as handle:
                saved = handle.read().strip()
            print("  resuming as a returning guest")
            await socket_.send(json.dumps({"t": "resume", "guest": saved}))
        else:
            await socket_.send(json.dumps({"t": "join", "token": token, "pin": pin}))

        async def pump():
            async for raw in socket_:
                message = json.loads(raw)
                kind = message.get("t")
                if kind == "joined":
                    print(f"  joined as {message['label']} (slot {message['slot']})")
                    if message.get("guest") and args.token_file:
                        with open(args.token_file, "w") as handle:
                            handle.write(message["guest"])
                elif kind == "offer":
                    guest.accept_offer(message["sdp"])
                elif kind == "ice":
                    guest.add_ice(int(message.get("sdpMLineIndex") or 0),
                                  message["candidate"])
                elif kind == "error":
                    print("  refused:", message.get("message"))
                    return

        pump_task = loop.create_task(pump())

        # Hold a direction so the host's pad has something visible to show,
        # then let go, so the last thing this proves is that release works.
        seq = 0
        deadline = loop.time() + args.seconds
        while loop.time() < deadline:
            held = (loop.time() < deadline - 2)
            guest.press(seq, buttons=(1 << P.BTN_RIGHT) | (1 << P.BTN_A) if held else 0,
                        axes=[20000, 0, 0, 0, 0, 0] if held else [0] * 6)
            seq = (seq + 1) & 0xFFFF
            await asyncio.sleep(1 / 125)

        pump_task.cancel()

    guest.stop()
    glib_loop.quit()

    print("\nresults")
    print(f"  ice state        {guest.ice_state}")
    print(f"  video buffers    {counts['video_buffers']}")
    print(f"  keyframes        {counts['keyframes']}")
    print(f"  video received   {counts['video_bytes'] / 1024:.0f} kB "
          f"({counts['video_bytes'] * 8 / args.seconds / 1e6:.1f} Mb/s)")
    print(f"  audio buffers    {counts['audio_buffers']}")
    print(f"  audio received   {counts['audio_bytes'] / 1024:.0f} kB")
    print(f"  input frames     {guest.frames_sent}")

    after = control({"cmd": "status"})
    for entry in after.get("guests", []):
        print(f"  host saw slot {entry['slot']}: {entry['frames']} frames on {entry['pad']}")

    ok = counts["video_buffers"] > 0 and guest.frames_sent > 0
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--token-file", default="/tmp/fp-guest-token",
                        help="where to keep the guest credential between runs")
    parser.add_argument("--resume", action="store_true",
                        help="come back as the guest from the last run, the way a "
                             "reloaded browser does, instead of spending the PIN")
    sys.exit(asyncio.run(run(parser.parse_args())))
