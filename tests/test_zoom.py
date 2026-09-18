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

HARNESS = "\n".join(lift(name) for name in
                    ("panRoom", "panTowards", "streamSize", "pictureBox",
                     "applyZoom", "paintAfterZoom", "cursorFollow")) + """
const ZOOM_MIN = 1, ZOOM_MAX = 4;
let zoom = 1, panX = 0, panY = 0, cursorU = 0.5, cursorV = 0.5;
let DRIVING = false, INSET = 0;
let streamShape = null;
function cursorDriving() { return DRIVING; }
function bottomInset() { return INSET; }
function paintZoom() {}
function paintCanvas() { return null; }
const video = { offsetWidth: 0, offsetHeight: 0,
                videoWidth: 2560, videoHeight: 1440, style: {} };
function fitPainted() {}

/* Where the pointer actually lands on the glass, and where the picture's own
   edges end up, for a guest at (u, v) on a screen of a given shape.

   Worked out from the transform rather than from the numbers that produced
   it: `translate(pan) scale(zoom)` about the middle of the element puts a
   point at fraction u of the picture at `box/2 + (u - 0.5) * pic * zoom +
   pan`. Checking the pan against itself would pass however wrong the rule
   was. */
function place(ask) {
  video.offsetWidth = ask.box[0];
  video.offsetHeight = ask.box[1];
  // "decoding" means the page is drawing the picture itself: the <video>
  // element has been left holding the sound, so it knows no shape at all and
  // the decoder's own report is the only source there is.
  if (ask.decoding) {
    video.videoWidth = 0;
    video.videoHeight = 0;
    streamShape = { width: 2560, height: 1440 };
  } else {
    video.videoWidth = 2560;
    video.videoHeight = 1440;
    streamShape = null;
  }
  DRIVING = Boolean(ask.driving);
  INSET = ask.inset || 0;
  zoom = ask.zoom;
  cursorU = ask.u;
  cursorV = ask.v;
  panX = 0;
  panY = 0;
  cursorFollow();
  const pic = pictureBox();
  return {
    x: video.offsetWidth / 2 + (cursorU - 0.5) * pic.width * zoom + panX,
    y: video.offsetHeight / 2 + (cursorV - 0.5) * pic.height * zoom + panY,
    top: video.offsetHeight / 2 - pic.height * zoom / 2 + panY,
    bottom: video.offsetHeight / 2 + pic.height * zoom / 2 + panY,
    left: video.offsetWidth / 2 - pic.width * zoom / 2 + panX,
    right: video.offsetWidth / 2 + pic.width * zoom / 2 + panX,
  };
}

const ask = JSON.parse(require("fs").readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify({
  rooms: ask.rooms.map(([size, seen, level]) => panRoom(size, seen, level)),
  slid: (ask.slid || []).map(([size, seen, level]) => panRoom(size, seen, level, true)),
  pans: ask.pans.map(([pan, towards, ratio]) => panTowards(pan, towards, ratio)),
  places: (ask.places || []).map(place),
}));
"""


def run(rooms, pans, slid=(), places=()):
    done = subprocess.run([node, "-e", HARNESS],
                          input=json.dumps({"rooms": rooms, "pans": pans,
                                            "slid": list(slid),
                                            "places": list(places)}),
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(done.stderr[:500])
    return json.loads(done.stdout)


# A 16:9 picture 800 wide inside a screen 800 wide and 600 tall: 450 of
# picture, 600 of screen, so 75 of black above and below.
WIDE, TALL = 800.0, 600.0
PICTURE_H = 450.0

# An upright phone, where the bug this section is about lives: a 16:9 picture
# across the full width of a screen more than twice as tall as it is wide, so
# the picture is a strip with a great deal of black above and below it.
PHONE = [390.0, 844.0]

answer = run(
    rooms=[[WIDE, WIDE, 1], [WIDE, WIDE, 2], [WIDE, WIDE, 4],
           [PICTURE_H, TALL, 1], [PICTURE_H, TALL, 1.33], [PICTURE_H, TALL, 2]],
    slid=[[PICTURE_H, TALL, 1], [PICTURE_H, TALL, 2], [WIDE, WIDE, 2]],
    pans=[[0, 0, 2], [100, 0, 2], [100, 0, 0.5], [0, 200, 2], [50, -120, 1.5]],
    places=[{"box": PHONE, "zoom": 2, "u": u, "v": v, "driving": driving,
             "inset": inset}
            for (u, v, driving, inset) in
            ((0.25, 0.25, True, 0), (0.25, 0.75, True, 0),
             (0.5, 0.5, True, 0), (0.25, 0.0, True, 0), (0.25, 1.0, True, 0),
             (0.25, 0.25, False, 0), (0.25, 0.5, True, 400))]
           + [{"box": PHONE, "zoom": 1, "u": 0.25, "v": v, "driving": True,
               "inset": 0} for v in (0.25, 0.75)]
           # The same two again, with the page decoding the picture itself.
           + [{"box": PHONE, "zoom": 2, "u": 0.25, "v": v, "driving": True,
               "inset": 0, "decoding": True} for v in (0.25, 0.75)])
rooms = answer["rooms"]
pans = answer["pans"]
slid = answer["slid"]
places = answer["places"]

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

print("a picture being driven may slide inside its own letterboxing")
# "the cursor stays centered horizontally when zoomed in, but not vertically".
# An upright phone shows the picture full width and letterboxed, so sideways
# it overhangs the moment it is zoomed and up and down it does not overhang
# until about three and a half times. The view therefore followed the pointer
# across and sat still up and down. Half the slack is exactly the point at
# which the picture's own edge reaches the edge of the screen, so it can be
# given away without ever showing black where picture should be.
check(slid[0] == (TALL - PICTURE_H) / 2,
      "half the slack, where before there was nothing: %r" % slid[0])
check(slid[1] == rooms[5],
      "while a picture that overhangs is bounded by the overhang exactly as "
      "before -- the slack rule is for the case where there is no overhang at "
      "all: %r" % slid[1])
check(slid[2] == rooms[1],
      "and across, where an upright phone never had a problem: %r" % slid[2])

print("so the pointer sits in the middle both ways, not just across")
middleX, middleY = PHONE[0] / 2, PHONE[1] / 2
for (label, at) in (("above the middle", places[0]),
                    ("below it", places[1]),
                    ("in it", places[2])):
    check(abs(at["x"] - middleX) < 0.5,
          "across, with the pointer %s: %.1f against %.1f"
          % (label, at["x"], middleX))
    check(abs(at["y"] - middleY) < 0.5,
          "and up and down, which is the half that did not: %.1f against %.1f"
          % (at["y"], middleY))

print("and never far enough to show black where the picture should be")
for (label, at) in (("at the very top", places[3]),
                    ("at the very bottom", places[4])):
    check(at["top"] >= -0.5 and at["bottom"] <= PHONE[1] + 0.5,
          "the picture stays on the screen with the pointer %s: %.0f..%.0f of "
          "0..%.0f" % (label, at["top"], at["bottom"], PHONE[1]))

print("with the keyboard up it stays inside what is left of the screen")
seen = PHONE[1] - 400
at = places[6]
check(at["top"] >= -0.5 and at["bottom"] <= seen + 0.5,
      "inside the strip above the keyboard: %.0f..%.0f of 0..%.0f"
      % (at["top"], at["bottom"], seen))

print("nobody who is only watching has the picture move under them")
check(abs(places[5]["y"] - middleY) > 1,
      "somebody watching rather than driving gets the old behaviour, with the "
      "picture sitting still and the pointer wherever it is: %.1f against a "
      "middle of %.1f" % (places[5]["y"], middleY))
still = [places[7], places[8]]
check(abs(still[0]["top"] - still[1]["top"]) < 1e-9,
      "and at 1x the picture does not move however the pointer does: %.1f "
      "against %.1f" % (still[0]["top"], still[1]["top"]))

print("and it is the same picture whoever is drawing it")
# The <video> element is left holding the sound while the page decodes the
# picture itself, so videoWidth is zero and every piece of geometry measured
# from it fell back to the shape of the *element*. On an upright phone that is
# a screen more than twice as tall as it is wide standing in for a picture a
# quarter of its height -- wrong only up and down, because the picture fills
# the width either way. The pointer moved a quarter as far as the finger while
# the picture slid under it by the whole distance, which is what "I'm just
# trying to move the cursor and it's scrolling instead" looks like, and it is
# why none of this was ever as bad on the WebRTC path.
for (label, drawn, decoded) in (("above the middle", places[0], places[9]),
                                ("below it", places[1], places[10])):
    # Both numbers in the message, because the first version of it printed
    # only the pointer -- which matched -- and read "422.0 against 422.0" over
    # a failure that was entirely in where the picture had been dragged to.
    check(abs(drawn["y"] - decoded["y"]) < 0.5
          and abs(drawn["top"] - decoded["top"]) < 0.5,
          "the pointer and the picture land in the same place with the page "
          "decoding, %s: pointer %.1f against %.1f, picture top %.1f against "
          "%.1f" % (label, decoded["y"], drawn["y"], decoded["top"],
                    drawn["top"]))

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
for name in ("applyZoom", "zoomAbout", "paintAfterZoom", "paintZoom"):
    body = lift(name)
    check("send(" not in body and "socket" not in body,
          "%s changes this page and tells the host nothing" % name)
# paintAfterZoom, not applyZoom: the two were one function once, and naming the
# old one here meant the test went on passing right up until the split, then
# started reporting that the picture was no longer moved by a transform, which
# was never true. Follow the call rather than trusting the name.
check("return paintAfterZoom();" in lift("applyZoom"),
      "applyZoom settles the numbers and hands them on to be painted")
check("video.style.transform" in lift("paintAfterZoom"),
      "the picture is moved by a transform, so the stream is untouched")
check("videoWidth" in lift("pictureBox"),
      "and the picture's own shape is read from the stream, not assumed")

print("the chips stay on top of a picture that has been made bigger")
# An untransformed video is a plain block and paints underneath everything
# positioned over it without anyone saying so. A transformed one is its own
# stacking context, which browsers hand to the compositor -- and a composited
# layer can come up over siblings that were painting above it a moment before.
# What that looked like was a zoomed picture drawn over the row of chips.
css = open(os.path.join(ROOT, "web", "style.css")).read()


def layer(selector):
    """The z-index the first rule for a selector states, or None."""
    block = re.search(re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    if not block:
        return None
    found = re.search(r"z-index:\s*(-?\d+)", block.group(1))
    return int(found.group(1)) if found else None


video_rule = re.search(r"\nvideo(?:, #painted)? \{(.*?)\n\}", css, re.S)
check(bool(video_rule) and "position: relative" in video_rule.group(1),
      "the video is positioned, so its z-index means something")
check(layer("\nvideo, #painted") == 0,
      "and states where it paints: %s" % layer("\nvideo, #painted"))
for name, selector in (("the chips", ".hud"), ("the on-screen pad", ".touch"),
                       ("the game list", ".browser"),
                       ("the controls panel", ".pads")):
    above = layer(selector)
    check(above is not None and above > 0,
          "%s says it is above the picture: %s" % (name, above))
check((layer(".browser") or 0) > (layer(".hud") or 0)
      and (layer(".pads") or 0) > (layer(".hud") or 0),
      "and a panel that replaces the picture is above the chips too")

print(("FAILED: %d" % len(fails)) if fails else "test_zoom: all ok")
sys.exit(1 if fails else 0)
