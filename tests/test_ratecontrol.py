"""The encoder's bitrate controller, driven rather than described.

This is here because the controller walked a 62 Mb/s link down to 400 kb/s on
a local network and left it there, and both streaming paths looked terrible
for it -- the encoder is shared, so a fault in this ruins the picture for
everybody including guests it knows nothing about. A controller that can do
that is worse than not having one.

Three separate faults produced it, and all three are the same shape: a number
compared against a number that does not mean the same thing.

  * The queue limit was worked out from the rate it exists to control.
    Lowering the rate lowered the limit, so the same backlog looked worse, so
    it lowered the rate again: 1708 -> 1281 -> 960 -> 720 -> 540 -> 405 -> 400
    inside one second, from one momentary backlog of 44843 bytes.

  * The arrival measure compared the browser's running total against the
    host's. The browser's worker is rebuilt whenever the painter restarts and
    its total goes back to zero, so the first report after a restart claimed
    thousands of frames missing at once: "the browser is 7836 frames behind
    what was sent, 7836 more than last time out of 60 sent (0% arriving)".

  * And the floor was 400 kb/s, which is not a picture at any size this
    streams. A floor should be the worst watchable answer, not the smallest
    number the arithmetic can reach.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# A clean skip rather than a traceback. video.py needs GstWebRTC, which is not
# on every machine this runs on, and a suite that dies on an import is
# indistinguishable in the summary from one that found something.
try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    from fourthplayer import video
except (ImportError, ValueError) as exc:
    print("SKIPPED: %s" % exc)
    sys.exit(0)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


Gst.init(None)


class FakeEncoder:
    """Something with a bitrate that remembers what it was told."""

    def __init__(self):
        self.bitrate = None
        self.sets = 0

    def set_property(self, name, value):
        assert name == "bitrate"
        self.bitrate = value
        self.sets += 1


class FakeConfig:
    def __init__(self, kbps):
        self.bitrate_kbps = kbps


class FakePeer:
    def __init__(self, frames=False):
        self.frames_wanted = frames
        self.frames_arriving = 1.0


def a_stage(kbps=62500, guests=None):
    """A Stage with nothing built, driven through the rate methods alone."""
    it = video.Stage.__new__(video.Stage)
    it.cfg = FakeConfig(kbps)
    it.encoder = FakeEncoder()
    it.peers = dict(enumerate(guests or [FakePeer(frames=True)]))
    it._rate_now = None
    it._rate_calm = 0
    it._rate_stepped = 0.0
    it._rate_broke = None
    return it


print("a momentary backlog does not cascade to the floor")
# The fault, exactly: one backlog over the limit, reported on every frame, as
# the appsink callback does.
stage = a_stage()
limit = stage.frame_queue_limit()
for _ in range(60):
    stage._ease_the_rate(limit + 1)
check(stage.encoder.sets == 1,
      "one step, not one per frame: %d" % stage.encoder.sets)
# Against the ceiling rather than the setting: a guest being sent whole frames
# caps the shared encoder at what a data channel can carry, and that cap is
# where the controller starts from.
check(stage.encoder.bitrate > stage._ceiling() * 0.7,
      "and a quarter off rather than everything: %d kb/s of a ceiling of %d"
      % (stage.encoder.bitrate, stage._ceiling()))

print("because the limit does not move with the rate it controls")
was = stage.frame_queue_limit()
stage._rate_now = 400
check(stage.frame_queue_limit() == was,
      "the limit is what was asked for, whatever is being sent right now: "
      "%d against %d" % (stage.frame_queue_limit(), was))

print("and the floor is a picture rather than the smallest number available")
stage = a_stage()
stage._rate_now = stage._ceiling()
for _ in range(100):
    stage._rate_stepped = 0.0            # let every step through
    stage._ease_the_rate(stage.frame_queue_limit() + 1)
check(stage._rate_now >= video.BITRATE_FLOOR_KBPS,
      "never below the absolute floor: %d" % stage._rate_now)
check(stage._rate_now >= stage._ceiling() * video.BITRATE_FLOOR_SHARE * 0.99,
      "nor below a share of the ceiling, because 1500 kb/s is fair at 720p "
      "and a smear at 1440p: %d against the 400 this used to reach"
      % stage._rate_now)

print("and the floor never meets the ceiling, or it could not back off at all")
# A share of the *setting* rather than of the ceiling made them equal: 15% of
# 62500 is 9375, above a data channel's ceiling of 8000, so the floor clamped
# to the ceiling and the rate could not be stepped down at all. A controller
# that cannot back off is how an association is driven into an error state,
# which is the fault the low ceiling exists to prevent.
tight = a_stage(kbps=62500)
check(tight._floor() < tight._ceiling(),
      "a high setting with a data channel's low ceiling still leaves room: "
      "floor %d, ceiling %d" % (tight._floor(), tight._ceiling()))
roomy = a_stage(kbps=62500, guests=[FakePeer(frames=False)])
check(roomy._floor() < roomy._ceiling(),
      "and so does a guest on the media track: floor %d, ceiling %d"
      % (roomy._floor(), roomy._ceiling()))

print("and it settles below where the channel broke rather than oscillating")
# Without this it climbs 15% at a time until the data channel backs up, stalls
# for a few seconds while the queue drains, falls 25%, and climbs straight
# back into the same wall -- every excursion over the top a freeze somebody is
# watching, every twenty seconds, for ever. Measured breaking at 13652 kb/s.
stage = a_stage()
# Below the ceiling, so the cap this learns is the thing being tested rather
# than the ceiling standing in for it.
# Inside the range the ceiling allows, so what is being tested is the cap this
# learns rather than the ceiling standing in for it.
broke = int(stage._ceiling() * 0.75)
stage._rate_now = broke
stage._rate_stepped = 0.0
stage._ease_the_rate(stage.frame_queue_limit() + 1)
check(stage._rate_broke == broke,
      "the rate that broke it is remembered: %s" % stage._rate_broke)
# A minute of nothing going wrong, which is far longer than the climb needs.
for _ in range(60):
    stage._rate_stepped = 0.0
    stage.note_arrivals()
check(stage._rate_now < broke * 1.05,
      "and a minute of calm does not walk back into it: %d against the %d "
      "that broke" % (stage._rate_now, broke))
check(stage._rate_now > stage._floor()
      and stage._rate_now > broke * 0.85,
      "while settling just under where it broke rather than near the floor: "
      "%d, with a floor of %d" % (stage._rate_now, stage._floor()))

print("but a link that has genuinely improved is tried again")
# The memory fades while things are going well, or one bad minute holds the
# picture down for the rest of the session.
stage = a_stage()
stage._rate_now = stage._ceiling()
stage._rate_stepped = 0.0
stage._ease_the_rate(stage.frame_queue_limit() + 1)
stage._rate_now = int(stage._ceiling() * 0.75)
stage._rate_stepped = 0.0
stage._ease_the_rate(stage.frame_queue_limit() + 1)
for _ in range(400):
    stage._rate_stepped = 0.0
    stage.note_arrivals()
check(stage._rate_broke is None,
      "the memory is gone after several minutes of nothing going wrong, "
      "because one bad patch must not hold the picture down all session")

print("a browser that restarts is not a browser losing frames")
peer = video.Peer.__new__(video.Peer)
peer.id = "slot0"
peer.stage = a_stage()
peer.frames_wanted = True
peer.frames_arriving = 1.0
peer._reports = 0
peer._reported_seq = None
peer._sent_frames = 0
# A normal run: the host's numbers, all arriving.
peer._take_picture_report('{"seq": 100, "got": 60}')
peer._take_picture_report('{"seq": 160, "got": 60}')
check(peer.frames_arriving == 1.0,
      "everything arriving reads as everything arriving: %.2f"
      % peer.frames_arriving)
# The painter restarts. The worker's own counters go back to zero, and the
# host's numbering does not -- which is the whole point of numbering here.
peer._take_picture_report('{"seq": 220, "got": 60}')
check(peer.frames_arriving == 1.0,
      "and the next report is still read against the host's own numbers")
# A sequence that went backwards is a restart of the host's side, and is
# anchored rather than believed.
peer.frames_arriving = 1.0
peer._take_picture_report('{"seq": 5, "got": 5}')
check(peer.frames_arriving == 1.0,
      "a sequence that went backwards is anchored, not read as total loss")

print("and real loss is still seen")
peer._take_picture_report('{"seq": 65, "got": 30}')
check(abs(peer.frames_arriving - 0.5) < 0.01,
      "half of them arriving reads as half: %.2f" % peer.frames_arriving)

print("the frame's number is on the wire for that to be possible")
source = open(os.path.join(ROOT, "fourthplayer", "video.py"),
              encoding="utf-8").read()
check('struct.pack("<BQHHI"' in source,
      "every piece carries the host's number for its frame")
worker = open(os.path.join(ROOT, "web", "frames.js"), encoding="utf-8").read()
check("view.getUint32(13, true)" in worker, "and the browser reads it")
check("seq: state.lastSeq" in worker, "and reports the last one it saw")

print("FAILED" if fails else "PASSED")
sys.exit(1 if fails else 0)
