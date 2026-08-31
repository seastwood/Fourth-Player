"""On-screen controller layouts: what each button actually sends.

The letters printed on a pad and the numbers the Gamepad API uses disagree, and
they disagree differently per manufacturer -- the standard mapping is
Xbox-shaped, so index 1 is the *right* face button, which Nintendo prints as A
and Sega prints as C. Getting this wrong is silent: the guest presses a button,
a different one happens, and nothing anywhere reports a fault.

So the table is checked against the mapping rather than trusted.
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from fourthplayer import protocol as P

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the layout table cannot be read.")
    sys.exit(0)

# LAYOUTS lives in app.js, which touches the DOM at load; pull just the literal.
source = open(os.path.join(ROOT, "web", "app.js")).read()
start = source.index("const LAYOUTS = {")
end = source.index("const DEFAULT_LAYOUT")
literal = source[start:end].strip().rstrip(";")

out = subprocess.run(
    [node, "-e", literal + "; process.stdout.write(JSON.stringify(LAYOUTS));"],
    capture_output=True, text=True)
if out.returncode != 0:
    print("  FAIL  could not read LAYOUTS:\n" + out.stderr)
    sys.exit(1)
layouts = json.loads(out.stdout)

print("both controllers are present and named")
check(set(layouts) == {"genesis", "nintendo", "nintendo_sticks"},
      "genesis, nintendo and nintendo_sticks, got %r" % sorted(layouts))
for key, layout in layouts.items():
    check(bool(layout.get("name")), "%s has a name for the picker" % key)

print("\nevery button sends something the protocol knows")
for key, layout in layouts.items():
    buttons = ([b["button"] for b in layout.get("face", [])]
               + [b["button"] for b in layout.get("shoulders", [])]
               + [b["button"] for b in layout.get("centre", [])])
    check(all(0 <= b < P.BUTTON_COUNT for b in buttons),
          "%s: every index is inside the standard mapping" % key)
    check(len(buttons) == len(set(buttons)),
          "%s: no index is used twice, got %r" % (key, buttons))

print("\nthe Nintendo diamond sends what its letters promise")
nin = {b["id"]: b for b in layouts["nintendo"]["face"]}
check(nin["B"]["button"] == P.BTN_A, "B is the bottom button (index 0)")
check(nin["A"]["button"] == P.BTN_B, "A is the right button (index 1)")
check(nin["Y"]["button"] == P.BTN_X, "Y is the left button (index 2)")
check(nin["X"]["button"] == P.BTN_Y, "X is the top button (index 3)")

print("\nand its letters are where a hand expects to find them")
check(nin["X"]["y"] < nin["B"]["y"], "X sits above B")
check(nin["Y"]["x"] < nin["A"]["x"], "Y sits left of A")
check(abs(nin["X"]["x"] - nin["B"]["x"]) < 5, "X and B share a column")
check(abs(nin["Y"]["y"] - nin["A"]["y"]) < 5, "Y and A share a row")

print("\nshoulders and centre buttons are the standard ones")
sh = {b["id"]: b for b in layouts["nintendo"]["shoulders"]}
check(sh["LB"]["button"] == 4 and sh["RB"]["button"] == 5, "bumpers are 4 and 5")
check(sh["LT"]["button"] == 6 and sh["RT"]["button"] == 7, "triggers are 6 and 7")
check(sh["LT"]["row"] == 0 and sh["LB"]["row"] == 1,
      "triggers sit further out than bumpers")
for key, layout in layouts.items():
    centre = {b["id"]: b["button"] for b in layout["centre"]}
    check(centre.get("START") == P.BTN_START, "%s: START is index 9" % key)
    if "SELECT" in centre:
        check(centre["SELECT"] == P.BTN_BACK, "%s: SELECT is index 8" % key)

print("\nthe Mega Drive keeps its own three across")
gen = {b["id"]: b for b in layouts["genesis"]["face"]}
check(list("ABC") == sorted(gen), "it has A, B and C")
check(gen["A"]["x"] < gen["B"]["x"] < gen["C"]["x"], "in that order, left to right")

print("\nface buttons fit the box they are drawn in")
for key, layout in layouts.items():
    aspect = layout.get("faceAspect", 1.55)
    lowest = max(b["y"] for b in layout["face"])
    # A button is 30% of the width across, and the box is `aspect` times wider
    # than it is tall, so that is 30 * aspect percent of the height.
    check(lowest + 30 * aspect <= 101,
          "%s: the lowest button ends inside the box (%.0f%%)"
          % (key, lowest + 30 * aspect))

print("\nthe on-screen pad starts as the one people can name the buttons of")
# A phone is where the buttons have to be guessable without being told, and
# the three-button Mega Drive pad has nowhere to put shoulders or Select.
import re                                                     # noqa: E402
default = re.search(r'const DEFAULT_LAYOUT = "([a-z]+)"', source).group(1)
check(default == "nintendo", "the default layout is the SNES pad: %r" % default)
check(default in layouts, "and it is a layout that exists")
check(layouts[default]["name"] == "Super Nintendo",
      "named for what it is: %r" % layouts[default]["name"])
check(any(b["id"] == "SELECT" for b in layouts[default]["centre"]),
      "with a Select button, which the old default had nowhere to put")

print("\nthe dropdown is filled in before anybody looks at it")
# It was built inside showTouch(), which a desktop with a real controller and
# no touchscreen never calls -- while the select is in the page from the start
# and visible. An empty select is drawn as a small empty box, which is exactly
# what it looked like.
app = source
start = app.index("function init")  if "function init" in app else 0
check(app.count("buildLayoutPicker()") >= 2,
      "it is built somewhere other than showTouch alone")
startup = app[app.index("wireTouch();"):]
startup = startup[:startup.index("ticker = setInterval")]
check("buildLayoutPicker()" in startup,
      "and one of those is at startup: %r" % startup.strip()[:80])

builder = app[app.index("function buildLayoutPicker"):]
builder = builder[:builder.index("\n}")]
check("touchOn ? chosenLayout() : \"off\"" in builder,
      "and it starts on the choice that matches what is on the screen")
check("paintPicker()" in builder,
      "with the label painted, since 'off' is renamed to the real controller")

print("\nthe pad is symmetrical and does not eat the picture's height")
# Select and start used to have a row of their own across the bottom. Upright
# that row cost height the picture wanted -- the video keeps its shape, so
# height it cannot have is width it cannot have either -- and it made the pad
# asymmetric: two clusters above, one bar below.
css = open(os.path.join(ROOT, "web", "style.css")).read()
areas = re.findall(r'grid-template-areas:\s*((?:\s*"[^"]*")+)\s*;', css)
grids = [" / ".join(re.findall(r'"([^"]*)"', a)) for a in areas]
check(len(grids) == 2, "there are two pad grids, one per orientation: %r" % grids)
for grid in grids:
    rows = [r.split() for r in grid.split(" / ")]
    check(len(rows) == 2, "two rows, not three: %r" % grid)
    check(rows[0] == ["lsh", "mid", "rsh"],
          "shoulders with select and start between them: %r" % rows[0])
    check(rows[1][0] == "left" and rows[1][-1] == "right",
          "and the clusters mirrored below: %r" % rows[1])
    check("mid" not in rows[1], "the middle does not reach the lower row")

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
