"""Getting closer to a corner of somebody else's television.

A guest watches a whole television through a phone, and what they want is
often a corner of it: a health bar, a lap counter, the map in the top right.
The picture is moved and scaled in the page -- a transform on the video
element -- so nothing is asked of the host and nothing else on the screen
changes size with it.

Two pieces of arithmetic decide whether that feels right, and both are easy to
get subtly wrong in a way no error ever reports:

  * how far the picture may be dragged, which is half of however much it
    overhangs the screen -- and *nothing* when it does not overhang, because a
    16:9 stream inside a taller phone is letterbox black above and below, and
    being able to drag the game off into that black is a way to lose it;

  * where the picture has to sit for the point between two fingers to stay
    between them, which is what makes a pinch grow what is being pinched
    rather than whatever happens to be in the middle.

Both are lifted out of app.js and run under node, the way test_remap.py and
test_keyboard.py do.
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


source = open(os.path.join(ROOT, "web", "app.js")).read()


def lift(name):
    start = source.index("function " + name + "(")
    depth = 0
    for j in range(source.index("{", start), len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[start:j + 1]
    raise AssertionError(name)


node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the browser half cannot be run.")
    sys.exit(0)

HARNESS = lift("panRoom") + "\n" + lift("panTowards") + "\n" + """
const ask = JSON.parse(require("fs").readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify({
  rooms: ask.rooms.map(([size, seen, level]) => panRoom(size, seen, level)),
  pans: ask.pans.map(([pan, towards, ratio]) => panTowards(pan, towards, ratio)),
}));
"""


def run(rooms, pans):
    done = subprocess.run([node, "-e", HARNESS],
                          input=json.dumps({"rooms": rooms, "pans": pans}),
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(done.stderr[:500])
    return json.loads(done.stdout)


# A 16:9 picture 800 wide inside a screen 800 wide and 600 tall: 450 of
# picture, 600 of screen, so 75 of black above and below.
WIDE, TALL = 800.0, 600.0
PICTURE_H = 450.0

answer = run(
    rooms=[[WIDE, WIDE, 1], [WIDE, WIDE, 2], [WIDE, WIDE, 4],
           [PICTURE_H, TALL, 1], [PICTURE_H, TALL, 1.33], [PICTURE_H, TALL, 2]],
    pans=[[0, 0, 2], [100, 0, 2], [100, 0, 0.5], [0, 200, 2], [50, -120, 1.5]])
rooms = answer["rooms"]
pans = answer["pans"]

print("a picture that fits cannot be dragged at all")
check(rooms[0] == 0, "at 1x across, there is no slack: %r" % rooms[0])
check(rooms[3] == 0, "and none up and down either, though there is black there")

print("...and one that overhangs may be moved by half the overhang")
check(rooms[1] == WIDE / 2, "twice as wide is half a screen each way: %r" % rooms[1])
check(rooms[2] == WIDE * 3 / 2, "four times is one and a half: %r" % rooms[2])

print("the black bars are not part of the picture")
# 450 of picture at 1.33 is 600, exactly the height of the screen: still
# nothing to drag, even though the element has been that tall all along.
check(rooms[4] == 0,
      "a letterboxed picture grown to the screen still has no slack: %r" % rooms[4])
check(abs(rooms[5] - (PICTURE_H * 2 - TALL) / 2) < 1e-9,
      "and past that, only what actually hangs over: %r" % rooms[5])

print("zooming towards the middle keeps the middle where it is")
check(pans[0] == 0, "nothing offset stays nothing: %r" % pans[0])
check(pans[1] == 200, "and an offset grows with the picture: %r" % pans[1])
check(pans[2] == 50, "shrinking takes it back down: %r" % pans[2])

print("zooming towards a point keeps that point under the fingers")
# The picture sits at `pan + u * level`; a point on screen at p is looking at
# u = (p - pan) / level. Doubling the level and moving the pan to what
# panTowards says must leave p looking at the same u.
def still_there(pan, towards, level, ratio, moved):
    before = (towards - pan) / level
    after = (towards - moved) / (level * ratio)
    return abs(before - after) < 1e-9

check(still_there(0, 200, 1, 2, pans[3]),
      "a point 200px right of centre, doubled: %r" % pans[3])
check(still_there(50, -120, 2, 1.5, pans[4]),
      "and one to the left of an already-moved picture: %r" % pans[4])

print("the bounds are what the page says they are")
limits = re.search(r"const ZOOM_MIN = ([\d.]+), ZOOM_MAX = ([\d.]+);", source)
check(bool(limits), "the zoom has stated limits")
if limits:
    check(float(limits.group(1)) == 1.0,
          "the smallest is the whole picture, not smaller: %s" % limits.group(1))
    check(2.0 <= float(limits.group(2)) <= 8.0,
          "and the largest is worth having without being a mosaic: %s"
          % limits.group(2))
check('min="100"' in open(os.path.join(ROOT, "web", "index.html")).read()
      and 'id="zoom-range"' in open(os.path.join(ROOT, "web", "index.html")).read(),
      "and the slider starts where the picture fits")

print("nothing about this asks the host for anything")
for name in ("applyZoom", "zoomAbout", "paintZoom"):
    body = lift(name)
    check("send(" not in body and "socket" not in body,
          "%s changes this page and tells the host nothing" % name)
check("video.style.transform" in lift("applyZoom"),
      "the picture is moved by a transform, so the stream is untouched")
check("videoWidth" in lift("pictureBox"),
      "and the picture's own shape is read from the stream, not assumed")

print(("FAILED: %d" % len(fails)) if fails else "test_zoom: all ok")
sys.exit(1 if fails else 0)
