"""A picture with no instrument in it.

Every experiment about the smoothness of this stream has had to reason around
something: a marker bit that arrives twice per frame, a timeline that is even
because it comes from a counter, a clock read from a Python probe the GIL can
delay. Each of those measured the measurer.

videotestsrc generates frames with exact contents at exact times. If the
picture is smooth with this on, everything from the encoder to the guest's
eye is sound and the fault is the capture; if it is not smooth, the capture
was never the problem. The judgement is somebody watching a ball move, and
there is nothing in between to be wrong about.
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
session = read("fourthplayer", "session.py")

print("the host can send one, and does not by default")
try:
    from fourthplayer.config import Config
    check(Config().test_pattern is False, "off out of the box")
except Exception as exc:
    check(False, "the config carries it (%s)" % exc)
check("videotestsrc name=capture" in video,
      "the source is replaced rather than the picture being drawn over")

print("and the pattern moves by the clock, not by frame number")
# The whole point. A ball that advances one step per frame looks perfectly
# smooth however unevenly the frames are timed, so it would agree with
# anything and prove nothing.
check("animation-mode=running-time" in video,
      "animation-mode=running-time, or this test cannot fail")

print("it reaches the encoder the same way the screen does")
spot = video.index("videotestsrc name=capture")
block = video[spot:spot + 700]
check("d3d11upload" in block and "cudaupload" in block,
      "put on the GPU where the converter expects it there")

print("and nothing is appended to it that only a screen understands")
guard = video[video.index("self._chosen_monitor = chosen"):
              video.index("self._chosen_monitor = chosen") + 500]
check("test_pattern" in guard and "chosen = None" in guard,
      "no monitor-handle= on a videotestsrc, which would not build at all")

print("both pages can turn it on")
for name, text, one in (("the client", read("web", "index.html"), "stream-testpattern"),
                        ("the setup page", read("web", "setup.html"), "set-testpattern")):
    check('id="%s"' % one in text, "%s has the switch" % name)
check("test_pattern:" in read("web", "app.js"), "the client sends it")
check("test_pattern:" in read("web", "setup.js"), "the setup page sends it")
check("Boolean(want.test_pattern) === Boolean(streamNow.test_pattern)"
      in read("web", "app.js"),
      "and Apply notices it changing, like the other two switches")

print("the host takes it from a page like every other on/off setting")
check('"test_pattern"' in session, "it is in the flag list and reported back")

print("FAILED" if fails else "PASSED")
sys.exit(1 if fails else 0)
