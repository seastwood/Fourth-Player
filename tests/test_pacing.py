"""What left this host, and how evenly.

"The frames just do not arrive smoothly at a steady rate" is a real complaint
and an unfalsifiable one from the guest's end: they see a capture, an encoder,
a fan-out and a network, and any of them could be the uneven part. The browser
reports what it received; this reports what was sent. Between the two there is
nowhere left for the unevenness to hide.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []
lines = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


try:
    from fourthplayer import video
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)


class FakeBuffer:
    """One RTP packet. Every packet of a frame carries that frame's PTS."""

    def __init__(self, pts):
        self.pts = pts


class FakeCfg:
    fps = 60


stage = video.Stage.__new__(video.Stage)
stage.cfg = FakeCfg()
stage._last_frame = 0.0
stage._last_pts = video.Gst.CLOCK_TIME_NONE
stage._gaps = []
stage._stamps = []
stage._grabbed = []
stage._grab_last = 0.0

clock = [1000.0]
real = video.time.monotonic
video.time.monotonic = lambda: clock[0]
said = []
real_log = video.log.info
video.log.info = lambda *a: said.append(a[0] % a[1:] if len(a) > 1 else a[0])

try:
    print("the packets of one frame are one frame")
    # The real failure this replaced: the marker bit arrives twice per frame
    # through this host's H.265 chain, so counting markers doubled the rate.
    for _ in range(50):
        clock[0] += 0.0001
        stage._note_pace(FakeBuffer(7_000_000_000))
    check(len(stage._gaps) == 0 and stage._last_frame > 0,
          "fifty packets sharing a timestamp are one frame, not fifty")

    print("a steady stream reports itself as steady")
    # A clean start: the section above left a frame counted at its own clock.
    stage._gaps = []
    stage._stamps = []
    stage._last_frame = 0.0
    stamp = 7_000_000_000
    for _ in range(video.PACE_SAMPLE + 1):
        clock[0] += 1 / 60.0
        stamp += int(1e9 / 60)              # nanoseconds, one frame at 60fps
        # Several packets per frame, as the payloader really produces.
        for _ in range(3):
            stage._note_pace(FakeBuffer(stamp))
    check(len(said) == 1, "one report per %d frames" % video.PACE_SAMPLE)
    report = said[0] if said else ""
    check("60.0/s" in report, "the rate is right: %s" % report)
    check("worst 17ms" in report, "and the worst gap is a frame, not more")
    check("0 late" in report, "with nothing late")

    print("and an uneven one is caught rather than averaged away")
    said.clear()
    stage._gaps = []
    stage._stamps = []
    stage._last_frame = 0.0
    stamp = 500_000_000_000
    for i in range(video.PACE_SAMPLE + 1):
        # Every tenth frame arrives a frame and a half late, which is exactly
        # the case that averages out to the right rate and looks wrong.
        clock[0] += (1 / 60.0) * (3.0 if i % 10 == 0 else 0.78)
        stamp += int(1e9 / 60)
        for _ in range(3):
            stage._note_pace(FakeBuffer(stamp))
    report = said[0] if said else ""
    check("late by more than half a frame" in report, "it says how many were late")
    late = int(re.search(r"(\d+) late by", report).group(1))
    check(late >= video.PACE_SAMPLE // 10 - 1,
          "and counts them: %d of %d" % (late, video.PACE_SAMPLE))
    check("60." in report.split("(")[1][:6] or "59." in report.split("(")[1][:6],
          "while the average rate still looks fine, which is the point: %s"
          % report)
finally:
    video.time.monotonic = real
    video.log.info = real_log

print("FAILED" if fails else "PASSED")
sys.exit(1 if fails else 0)
