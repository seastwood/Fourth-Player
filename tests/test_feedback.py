"""Asking for a lost packet back, instead of waiting for a whole new picture.

The offer named two kinds of feedback and not the third. Browsers were told
they could ask for a keyframe (`nack pli`, `ccm fir`), which webrtcbin writes
by itself, and were never told they could ask for a single packet again --
that line comes from the caps, and the caps did not carry it. So one packet
lost on a wifi hop cost the whole picture until the next keyframe: up to two
seconds at the interval this uses, from a loss lasting a millisecond. The
guest could not even be seen to complain, because asking for a keyframe is the
only complaint that reaches the host, and it arrives as a keyframe request
rather than as "I lost one packet".

Both halves are needed and neither is any use alone: the caps put
`a=rtcp-fb:96 nack` in the offer so the browser knows it may ask, and do-nack
on the video transceiver makes webrtcbin offer an rtx payload type and keep
what it sent long enough to send it again. Without the second half the browser
asks for packets nothing here can still produce.

What this cannot check is the offer that comes out the other end: a webrtcbin
built and torn down inside a test process segfaults often enough -- the same
hazard PipelineWorker exists for -- that the suite would fail for reasons that
have nothing to do with the code under test. The offer was read from the real
pipeline instead, and says:

    a=rtcp-fb:96 nack
    a=rtcp-fb:96 nack pli
    a=rtpmap:98 rtx/90000
    a=fmtp:98 apt=96

with 98 chosen around the sound, which is 97 in the same bundle.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    import gi
    gi.require_version("Gst", "1.0")
    gi.require_version("GstWebRTC", "1.0")
    gi.require_version("GstSdp", "1.0")
    # GstSdp is imported for its side effect: without it the offer's
    # description comes back as an opaque boxed value and reading .sdp
    # off it takes the interpreter down with it.
    from gi.repository import Gst, GstWebRTC, GstSdp, GLib  # noqa: F401
    from fourthplayer import video
except (ImportError, ValueError) as exc:
    print("SKIPPED: %s -- needs the GStreamer bindings, which live on the host"
          % exc)
    sys.exit(0)

Gst.init(None)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


class FakeStage:
    encoding = "H264"


class FakePeer:
    """Only what _rtp_caps reads."""

    def __init__(self):
        self.stage = FakeStage()

    _rtp_caps = video.Peer._rtp_caps


print("the video the host offers may be asked for again")
peer = FakePeer()
video_caps = peer._rtp_caps("video").to_string()
check("rtcp-fb-nack=(boolean)true" in video_caps,
      "the video caps ask for retransmission: %s" % video_caps)

print("...and the sound does not, which is deliberate")
# Opus carries its own forward error correction in the next packet, so a
# retransmitted one would arrive after the gap it was meant to fill.
audio_caps = peer._rtp_caps("audio").to_string()
check("rtcp-fb-nack" not in audio_caps,
      "the audio caps are left alone: %s" % audio_caps)


print("the transceiver is told the same thing from the other side")


class FakeTransceiver:
    def __init__(self):
        self.set = {}

    def set_property(self, name, value):
        self.set[name] = value


sending = FakeTransceiver()
video.Peer._one_way(peer, sending, video.VIDEO_TRANSCEIVER)
check(sending.set.get("do-nack") is True,
      "the video transceiver retransmits: %s" % sending.set)
check("direction" in sending.set, "and it still sends only")

sound = FakeTransceiver()
video.Peer._one_way(peer, sound, video.VIDEO_TRANSCEIVER + 1)
check("do-nack" not in sound.set,
      "the sound is left alone: %s" % sound.set)


class OldTransceiver(FakeTransceiver):
    """A webrtcbin from before do-nack existed."""

    def set_property(self, name, value):
        if name == "do-nack":
            raise TypeError("no property do-nack")
        FakeTransceiver.set_property(self, name, value)


old = OldTransceiver()
video.Peer._one_way(peer, old, video.VIDEO_TRANSCEIVER)
check(old.set.get("direction") is not None,
      "an older webrtcbin still gets a one-way stream rather than an exception")


print("the host notices when it is the one that stopped")


class Recorder:
    def __init__(self):
        self.warnings = []

    def warning(self, fmt, *args):
        self.warnings.append(fmt % args)

    def __getattr__(self, _name):
        return lambda *a, **k: None


class FakeVideoStage:
    _note_gap = video.Stage._note_gap

    def __init__(self):
        self._last_sample = {}
        self._stalls = {}
        self._said_stall = {}


was, video.log = video.log, Recorder()
try:
    stage = FakeVideoStage()
    stage._note_gap("video")                  # the first one has no gap to judge
    stage._note_gap("video")                  # and this one is immediate
    check(not video.log.warnings, "an ordinary frame says nothing")
    stage._last_sample["video"] = time.monotonic() - 1.0
    stage._note_gap("video")
    check(len(video.log.warnings) == 1 and "video stopped" in video.log.warnings[0],
          "a second with nothing sent is worth a line: %s" % video.log.warnings)
    # A host that is struggling stalls over and over, and this log is shared
    # with a source that has put four thousand lines in it in an afternoon.
    stage._last_sample["video"] = time.monotonic() - 1.0
    stage._note_gap("video")
    check(len(video.log.warnings) == 1,
          "the second gap in a row is counted rather than said again")
    stage._said_stall["video"] = time.monotonic() - 60
    stage._last_sample["video"] = time.monotonic() - 1.0
    stage._note_gap("video")
    check(len(video.log.warnings) == 2 and "3 gaps" in video.log.warnings[1],
          "and when it does speak again it says how many there were: %s"
          % video.log.warnings)
finally:
    video.log = was

print(("FAILED: %d" % len(fails)) if fails else "test_feedback: all ok")
sys.exit(1 if fails else 0)
