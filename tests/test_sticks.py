"""Swapping the two sticks, for the games that have them the wrong way round.

Some games put the camera on the left stick and the character on the right,
which is backwards for anybody who did not grow up with it. The game will not
be told: the pad this page sends is built here, so the two pairs of axes are
simply traded on the way out.

The interesting case is a controller whose buttons are all correct. remapped()
used to return the browser's pad untouched whenever there was no button map,
which meant the only way to swap your sticks was to break your buttons first.
"""
import json
import os
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


import re                                                        # noqa: E402
keys = re.search(r"const STANDARD_KEYS = \[.*?\];", source, re.S).group(0)

HARNESS = keys + "\n" + lift("shapeStick") + "\n" + lift("shapeAxes") \
    + "\n" + lift("swapSticks") + "\n" + lift("remapped") + """
const job = JSON.parse(require("fs").readFileSync(0, "utf8"));
let padMap = job.map === undefined ? null : job.map;
let sticksSwapped = !!job.swapped;
let deadzone = job.deadzone === undefined ? 0 : job.deadzone;
let sensitivity = job.sensitivity === undefined ? 1 : job.sensitivity;
const pad = job.pad === null ? null : {
  buttons: (job.buttons || []).map((v) => ({ pressed: !!v, value: v ? 1 : 0 })),
  axes: job.axes, id: "test", index: 0, connected: true, mapping: "standard",
};
const out = remapped(pad);
process.stdout.write(JSON.stringify(out === null ? null : {
  axes: out.axes,
  pressed: (out.buttons || []).map((b) => (b && b.pressed) ? 1 : 0),
  same: out === pad,
}));
"""


def run(job):
    done = subprocess.run([node, "-e", HARNESS], input=json.dumps(job),
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(done.stderr[:500])
    return json.loads(done.stdout)


AXES = [0.1, 0.2, 0.8, 0.9]

print("swapped, the left stick becomes the right one")
got = run({"swapped": True, "axes": AXES})
check(got["axes"] == [0.8, 0.9, 0.1, 0.2],
      "the two pairs are traded: %r" % got["axes"])

print("and not swapped, nothing is touched")
check(run({"swapped": False, "axes": AXES})["axes"] == AXES,
      "the axes arrive as the browser reported them")

print("a controller with correct buttons can still swap its sticks")
# This is the whole point. remapped() returned early on a null button map, so
# the only route to swapped sticks was to remap a button you did not want to.
plain = run({"swapped": True, "axes": AXES})
check(plain["same"] is False, "the pad is rebuilt rather than passed through")
check(plain["axes"] == [0.8, 0.9, 0.1, 0.2], "and its sticks are swapped")

print("and a button map still works, with or without the swap")
# STANDARD_KEYS index 0 is A and 1 is B; this map trades them.
swapped_ab = {"map": [1, 0, 2, 3], "buttons": [1, 0, 0, 0]}
check(run(dict(swapped_ab, swapped=False))["pressed"][:2] == [0, 1],
      "A pressed arrives as B")
both = run(dict(swapped_ab, swapped=True, axes=AXES))
check(both["pressed"][:2] == [0, 1], "buttons still remapped with sticks swapped")
check(both["axes"] == [0.8, 0.9, 0.1, 0.2], "and the sticks are swapped too")

print("half a swap is worse than none, so a short pad is left alone")
for axes in ([], [0.1], [0.1, 0.2], [0.1, 0.2, 0.3]):
    got = run({"swapped": True, "axes": axes})
    check(got["axes"] == axes,
          "%d axes are untouched: %r" % (len(axes), got["axes"]))

print("no pad is still no pad")
check(run({"swapped": True, "pad": None}) is None, "nothing to swap, nothing returned")

print("\na dead zone is a circle, not a cross")
# Per-axis is the easy version and it is wrong in a way people feel: it carves
# a cross out of the middle, so a stick pushed diagonally answers while the
# same stick pushed straight up does not.
# Two pushes the same distance from centre, one straight and one diagonal.
# A circular boundary treats them alike; a per-axis one lets the diagonal
# through while stopping the straight push, which is the bug being avoided.
import math                                                      # noqa: E402
STRAIGHT = [0.20, 0.0, 0, 0]
DIAGONAL = [0.20 / math.sqrt(2), 0.20 / math.sqrt(2), 0, 0]

inside_s = run({"deadzone": 0.25, "axes": STRAIGHT})["axes"][:2]
inside_d = run({"deadzone": 0.25, "axes": DIAGONAL})["axes"][:2]
check(inside_s == [0, 0], "inside the zone, straight is ignored: %r" % inside_s)
check(inside_d == [0, 0], "and so is diagonal, at the same distance: %r" % inside_d)

out_s = run({"deadzone": 0.15, "axes": STRAIGHT})["axes"][:2]
out_d = run({"deadzone": 0.15, "axes": DIAGONAL})["axes"][:2]
check(out_s[0] > 0 and out_d[0] > 0, "outside it, both register")
check(abs(math.hypot(*out_s) - math.hypot(*out_d)) < 1e-9,
      "by the same amount: %r vs %r" % (math.hypot(*out_s), math.hypot(*out_d)))

print("and past the edge it starts from nothing, not from a jump")
edge = run({"deadzone": 0.30, "axes": [0.31, 0, 0, 0]})
check(0 < edge["axes"][0] < 0.05,
      "a hair past the dead zone is a hair of movement: %r" % edge["axes"][0])
full = run({"deadzone": 0.30, "axes": [1.0, 0, 0, 0]})
check(abs(full["axes"][0] - 1.0) < 1e-9,
      "and all the way over is still all the way: %r" % full["axes"][0])

print("sensitivity reaches full tilt sooner, and never past it")
plain = run({"axes": [0.5, 0, 0, 0]})["axes"][0]
keen = run({"sensitivity": 2.0, "axes": [0.5, 0, 0, 0]})["axes"][0]
check(keen > plain, "twice as sensitive is further over: %r vs %r" % (keen, plain))
check(run({"sensitivity": 2.5, "axes": [1.0, 0, 0, 0]})["axes"][0] <= 1.0,
      "and full tilt is still the limit")
check(run({"sensitivity": 0.5, "axes": [1.0, 0, 0, 0]})["axes"][0] < 1.0,
      "below 100% never quite gets there, which is the point of it")

print("a diagonal keeps its direction while it is being shaped")
d = run({"deadzone": 0.1, "sensitivity": 1.5, "axes": [0.6, 0.6, 0, 0]})["axes"]
check(abs(d[0] - d[1]) < 1e-9, "equal in, equal out: %r" % d[:2])

print("with nothing to do, the axes are handed back untouched")
same = run({"deadzone": 0, "sensitivity": 1, "axes": AXES})
check(same["axes"] == AXES, "no dead zone and no gain changes nothing")

print("\nthe sliders do not run the width of a desktop")
# A slider is a distance the thumb has to travel. Stretched across a wide
# screen it takes a swipe of the whole display to move the dead zone a few per
# cent, and every value in the middle is a pixel wide.
css = open(os.path.join(ROOT, "web", "style.css")).read()
row = re.search(r"\n\.tune \{([^}]*)\}", css).group(1)
check("max-width" in row, "the row is capped: %r" % row.strip()[:60])
rng = re.search(r"\.tune \.pad-range \{([^}]*)\}", css).group(1)
check("max-width" in rng, "and so is the slider itself: %r" % rng.strip())
check("flex: 1 1 auto" in rng,
      "while still filling what it is given below that")

print("\nthe two settings are stored apart, so neither erases the other")
check('"fp-sticks:"' in source and '"fp-padmap:"' in source,
      "different keys for the button map and the sticks")
reset = source[source.index('el("pads-reset").addEventListener'):]
reset = reset[:reset.index("});")]
check("sticksKey()" in reset and "mapKey()" in reset,
      "and 'use defaults' clears both: %r" % ("sticksKey" in reset))

print()
if fails:
    print("FAILURES: %d" % len(fails))
    sys.exit(1)
print("test_sticks: all ok")
