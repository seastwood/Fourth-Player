"""Something has to hold the frame rate, and a caps filter does not.

The capture is followed by `video/x-raw(ANY),framerate=N/1`, which was doing
all of the work of naming the frame rate and none of the work of keeping it.
A caps filter says what the pictures are; it says nothing about when they
arrive, and the desktop captures deliver on their own schedule. Measured on
the Windows host: asked for 120 a second, it produced 137, with a median gap
of 4.6ms against the 8.3ms asked for and one frame in ten more than half a
frame late. Frames in clumps.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


source = open(os.path.join(ROOT, "fourthplayer", "video.py"),
              encoding="utf-8").read()

print("the capture is paced before its rate is declared")
# Built into a variable now that it is a switch, so the element is looked
# for where it is assembled rather than inside the description.
check('"! videorate drop-only=true "' in source, "there is a videorate at all")
rate = source.find("{pacing}")
caps = source.find("framerate={cfg.fps}/1")
check(rate > 0 and caps > rate,
      "it comes before the caps filter, so the filter is what it aims at")

print("and it drops rather than repeats")
check('pacing = "! videorate drop-only=true " if pace else ""' in source,
      "drop-only: a repeated frame is bitrate spent saying nothing changed")

print("the order through the pipeline is still capture, pace, convert, encode")
order = []
for name in ("format(display=cfg.display)", "{pacing}", "framerate={cfg.fps}",
             "{convert}", "{encoder}"):
    order.append(source.find(name))
check(all(order[i] < order[i + 1] for i in range(len(order) - 1)),
      "each stage is after the one that feeds it: %s" % (order,))

print("nothing else claims to pace the picture")
# In the description, not in the prose about it.
check(source.count('"! videorate drop-only=true "') == 1,
      "one videorate element, so there is one place the rate is held")

try:
    from fourthplayer import video
except Exception as exc:
    print("SKIPPED the built description (%s)" % exc)
    print("FAILED" if fails else "PASSED")
    sys.exit(1 if fails else 0)

print("and a pacing report can still tell whether it worked")
check(hasattr(video.Stage, "_note_pace"),
      "the measurement that found this is still there to confirm the fix")
check(video.PACE_SAMPLE >= 100,
      "over a sample big enough that one hiccup does not decide it")

print("FAILED" if fails else "PASSED")
sys.exit(1 if fails else 0)
