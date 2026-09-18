"""Pacing and oversampling, and the pair of them that means something else.

Two switches, three useful shapes:

  neither    the capture's own rate, named by a caps filter and kept by
             nothing -- what this did before any of it
  pacing     a videorate behind the capture, dropping whatever arrived too
             early for its slot
  both       the capture asked for twice the rate, the videorate choosing the
             fresher of each pair

The fourth combination -- oversampling with nothing to bring the rate back
down -- is not a shape, it is twice as many frames sent. So oversampling turns
pacing on, in the host and in both pages, rather than being quietly wrong.
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


def read(*bits):
    return open(os.path.join(ROOT, *bits), encoding="utf-8").read()


video = read("fourthplayer", "video.py")
app = read("web", "app.js")
page = read("web", "index.html")
setupjs = read("web", "setup.js")
setuphtml = read("web", "setup.html")

print("the host has both settings and a default for each")
try:
    from fourthplayer.config import Config
    cfg = Config()
    check(cfg.pace_frames is True, "pacing is on out of the box")
    check(cfg.oversample is False,
          "oversampling is not, since it costs a second capture per frame")
except Exception as exc:
    check(False, "the config carries them (%s)" % exc)

print("and oversampling turns pacing on rather than meaning something else")
spot = video.index("oversample = bool(")
block = video[spot:spot + 1400]
check("or oversample" in block,
      "pace is true whenever oversample is still wanted, in the host")
check('framerate={cfg.fps * 2}/1' in video,
      "the capture is asked for twice the rate when oversampling")
check('"! videorate drop-only=true " if pace else ""' in video,
      "and the videorate is there only when something wants it")

print("and two settings that cannot both be had do not both happen")
# Oversampling captures at twice the rate and needs the videorate to bring it
# back down; true time exists to stop a videorate putting the timestamps back
# on a grid. Both set produced "! caps(120) ! caps(60)" with nothing between,
# which gstreamer will not build -- and a host with no capture cannot give
# anybody video. It was a dead host reached by ticking two boxes.
check("if self._true_time and oversample:" in video,
      "the pair is noticed")
check("oversample = False" in video,
      "and one of them gives way, with a line saying which and why")
check("if (oversample and pace) else \"\"" in video,
      "and the caps are never emitted without the element that follows them")

print("a description that will not build says what it was")
check("would not build, so there is no capture" in video,
      "at a level somebody will see, with the whole description")

print("the pipeline still names the sent rate last, whatever the switches say")
order = [video.find("{oversampling}"), video.find("{pacing}"),
         video.find("framerate={cfg.fps}/1")]
check(all(o > 0 for o in order) and order == sorted(order),
      "capture, then oversample caps, then videorate, then the sent rate")

print("both pages offer both switches")
for name, text, ids in (("the client", page, ("stream-pace", "stream-oversample")),
                        ("the setup page", setuphtml, ("set-pace", "set-oversample"))):
    for one in ids:
        check('id="%s"' % one in text, "%s has %s" % (name, one))

print("both pages send them")
check("pace_frames:" in app and "oversample:" in app,
      "the client puts them in what Apply sends")
check("pace_frames:" in setupjs and "oversample:" in setupjs,
      "and so does the setup page")

print("both pages show that oversampling implies pacing")
for name, text in (("the client", app), ("the setup page", setupjs)):
    check(re.search(r"pace\.disabled = ", text) is not None,
          "%s holds the pacing switch when oversampling is on" % name)

print("and Apply notices a change to either")
check("Boolean(want.pace_frames) === Boolean(streamNow.pace_frames)" in app,
      "pacing is compared, so Apply does not say there is nothing to apply")
check("Boolean(want.oversample) === Boolean(streamNow.oversample)" in app,
      "and so is oversampling -- the trap the virtual display switch fell in")

print("the host accepts them from a page")
session = read("fourthplayer", "session.py")
check('"pace_frames", "oversample"' in session,
      "both are in the list of flags a page may set")
check('"pace_frames": bool(' in session and '"oversample": bool(' in session,
      "and both are reported back, or the pages cannot draw them")

print("FAILED" if fails else "PASSED")
sys.exit(1 if fails else 0)
