"""On-screen thumbsticks.

Glass has neither a spring nor a centre, so both have to be built. The knob
follows the thumb only to the edge of the well, and it returns to the middle
the moment the thumb leaves -- a character that keeps walking after you let go
is the one failure nobody forgives.
"""
import json
import math
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


node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the browser half cannot be run.")
    sys.exit(0)

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


HARNESS = "\n".join(lift(n) for n in
                    ("stickAxesOf", "moveStick", "releaseStick")) + """
const job = JSON.parse(require("fs").readFileSync(0, "utf8"));
let touchAxes = [0, 0, 0, 0];
const knob = { style: { transform: "" } };
const well = {
  dataset: { axes: job.axes },
  classList: { remove() {}, add() {} },
  querySelector: () => knob,
  // A 200x200 well whose centre is at (200, 300).
  getBoundingClientRect: () => ({ left: 100, top: 200, width: 200, height: 200 }),
};
function centreKnob(w) { knob.style.transform = "translate(-50%, -50%)"; }
for (const point of job.moves) moveStick(well, { clientX: point[0], clientY: point[1] });
if (job.release) releaseStick(well);
process.stdout.write(JSON.stringify({ axes: touchAxes, knob: knob.style.transform }));
"""


def run(moves, axes="0,1", release=False):
    done = subprocess.run([node, "-e", HARNESS],
                          input=json.dumps({"moves": moves, "axes": axes,
                                            "release": release}),
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(done.stderr[:400])
    return json.loads(done.stdout)


CENTRE = (200, 300)

print("the centre of the well is the centre of the stick")
got = run([CENTRE])
check(got["axes"] == [0, 0, 0, 0], "no tilt: %r" % got["axes"])

print("and the edges are full tilt, in the right direction")
right = run([(300, 300)])
check(abs(right["axes"][0] - 1) < 1e-9 and right["axes"][1] == 0,
      "right edge is +1 on x: %r" % right["axes"][:2])
down = run([(200, 400)])
check(down["axes"][1] == 1, "bottom edge is +1 on y, screen-style: %r" % down["axes"][:2])
left = run([(100, 300)])
check(left["axes"][0] == -1, "left edge is -1: %r" % left["axes"][:2])

print("past the edge is still full tilt, not more")
far = run([(1000, 300)])
check(abs(far["axes"][0] - 1) < 1e-9, "a thumb outside the well reads 1: %r" % far["axes"][0])

print("and the corner is clamped to the circle, not the square")
# The corner of a square well is 1.41 from the middle; a stick cannot do that.
corner = run([(300, 400)])
reach = math.hypot(corner["axes"][0], corner["axes"][1])
check(abs(reach - 1) < 1e-9, "a corner reads exactly full tilt: %.4f" % reach)
check(abs(corner["axes"][0] - corner["axes"][1]) < 1e-9,
      "and keeps its direction: %r" % corner["axes"][:2])

print("letting go returns it to the middle")
after = run([(300, 400)], release=True)
check(after["axes"] == [0, 0, 0, 0], "every axis back to nothing: %r" % after["axes"])
check("-50%, -50%" in after["knob"], "and the knob is centred: %r" % after["knob"])

print("the right-hand stick drives the other pair of axes")
r = run([(300, 300)], axes="2,3")
check(r["axes"] == [0, 0, 1, 0],
      "axes 2 and 3, leaving the left stick alone: %r" % r["axes"])

print("\nthe sticks send what the physical pad sends, in the same units")
# The conversion to wire units is frame.js's job, not a second copy in app.js.
check("FPFrame.toAxis(" in source, "app.js asks frame.js to convert")
frame = open(os.path.join(ROOT, "web", "frame.js")).read()
check(re.search(r"const api = \{[^}]*\btoAxis\b", frame),
      "and frame.js exports it rather than keeping it private")

print("\nboth orientations size the sticks explicitly")
css = open(os.path.join(ROOT, "web", "style.css")).read()
portrait = css[css.index("@media (orientation: portrait)"):]
portrait = portrait[:portrait.index("@media (orientation: landscape)")] \
    if "@media (orientation: landscape)" in portrait else portrait
landscape = css[css.index("@media (orientation: landscape)"):]
for name, block in (("portrait", portrait), ("landscape", landscape)):
    check(".touch.has-sticks .stick" in block,
          "%s gives the stick a size of its own" % name)
    check(".touch.has-sticks .dpad" in block and ".touch.has-sticks .face" in block,
          "%s shrinks the clusters to make room" % name)

print()
if fails:
    print("FAILURES: %d" % len(fails))
    sys.exit(1)
print("test_touchsticks: all ok")
