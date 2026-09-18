"""Stamp a frame with when it arrived, not with when it was due.

At 60fps this host grabs the desktop about 676 times in ten seconds, at
intervals 168 of which are nowhere near 16.7ms -- and stamps every one of
them as though it had arrived on the beat. A browser draws by those stamps,
so it draws evenly spaced frames whose contents advanced by uneven amounts.
Motion speeds up and slows down while every counter at both ends reads
perfect, which is exactly what was happening: 60.0 sent, 60.0 arrived, 60.0
decoded, none thrown away, and still not smooth.

do-timestamp=true does not fix it; the capture element does its own
timestamping. A pad probe can, and that was checked on the machine before
this was written, because PyGObject exposes no make_writable and a buffer
that silently refused the change would leave the grid in place with nothing
saying so.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def read(*bits):
    return open(os.path.join(ROOT, *bits), encoding="utf-8").read()


video = read("fourthplayer", "video.py")
app = read("web", "app.js")

print("the host can stamp on arrival, and does not by default")
try:
    from fourthplayer.config import Config
    check(Config().true_time is False, "off out of the box")
except Exception as exc:
    check(False, "the config carries it (%s)" % exc)

print("and it writes the stamp where the frame is first seen")
check("buffer.pts = when" in video, "the probe sets the timestamp")
check("buffer.dts = Gst.CLOCK_TIME_NONE" in video,
      "and clears the decode stamp, which no longer means anything")
check("_running_time" in video, "from the pipeline's own clock")

print("the clock is asked safely, because it is not always there")
spot = video.index("def _running_time")
block = video[spot:spot + 700]
for guard in ("CLOCK_TIME_NONE", "return None", "except Exception"):
    check(guard in block, "a pipeline not yet running returns None (%s)" % guard)

print("and it turns the pacing off, because they undo each other")
spot = video.index("self._true_time = bool(")
block = video[spot:spot + 1200]
check("not self._true_time" in block,
      "pace is false whenever true_time is: a videorate would put the "
      "stamps straight back on a grid")

print("both pages offer it and show that it disables the pacing")
check('id="stream-truetime"' in read("web", "index.html"), "the client has it")
check('id="set-truetime"' in read("web", "setup.html"), "the setup page has it")
check("true_time:" in app and "true_time:" in read("web", "setup.js"),
      "both send it")
check("Boolean(want.true_time) === Boolean(streamNow.true_time)" in app,
      "and Apply notices it changing")
for name, text in (("the client", app), ("the setup page", read("web", "setup.js"))):
    spot = text.find("true_time")
    check("pace.disabled = true" in text,
          "%s shows the pacing going off with it" % name)

print("the host takes it from a page like the others")
check('"true_time"' in read("fourthplayer", "session.py"),
      "it is in the flag list and reported back")

print("FAILED" if fails else "PASSED")
sys.exit(1 if fails else 0)
