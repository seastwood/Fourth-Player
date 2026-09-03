"""The four buttons in the diamond, and which letter arrives at the game.

The complaint, and it is a real one: the on-screen pad is drawn as a Nintendo
diamond -- A on the right, B at the bottom -- and the host presents it as an
Xbox pad, where the right-hand button *is* B. Everything is sent by position,
which is correct and is not the whole story: press the button marked A and a
game that names its buttons says you pressed B, and says X when you press Y.

Two ways out, and neither is a substitute for the other:

  * the Xbox layout moves the letters to where the wire puts them, which is
    what somebody wants when the game is telling them which button to press;
  * the swap moves what is sent to where the letters are, which is what
    somebody wants when the letters on the glass should match the letters in a
    Super Nintendo game's own menus.

Held still here: both layouts send the same four indices by position, so
neither is a secret remapping of the other; the swap trades exactly two pairs
and touches nothing else; and it survives being read back, because a guest
sets it once and expects it every evening after.
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
page = open(os.path.join(ROOT, "web", "index.html")).read()

node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the browser half cannot be run.")
    sys.exit(0)

table = source[source.index("const LAYOUTS = {"):]
table = table[:table.index("\n};") + 3]
swap = re.search(r"^const FACE_SWAP = .*$", source, re.M).group(0)
key = re.search(r"^const FACESWAP_KEY = .*$", source, re.M).group(0)


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


HARNESS = "\n".join([table, swap, key, lift("faceSwapped"), lift("sentAs")]) + """
const job = JSON.parse(require("fs").readFileSync(0, "utf8"));
const store = job.swapped ? { "fp:faceswap": "1" } : {};
const localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};
const layout = LAYOUTS[job.layout];
const out = {};
for (const spec of layout.face) out[spec.id] = sentAs(spec.button);
// Where each button sits, so "the letters moved" can be told apart from "the
// buttons moved".
const where = {};
for (const spec of layout.face) {
  where[spec.y > 50 ? "bottom" : spec.y < 20 ? "top" : (spec.x > 50 ? "right" : "left")]
    = spec.id;
}
process.stdout.write(JSON.stringify({ sends: out, where }));
"""


def run(layout, swapped=False):
    done = subprocess.run([node, "-e", HARNESS],
                          input=json.dumps({"layout": layout, "swapped": swapped}),
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(done.stderr[:500])
    return json.loads(done.stdout)


# The standard mapping, which is what the host turns into an Xbox pad.
SOUTH, EAST, WEST, NORTH = 0, 1, 2, 3

print("the Nintendo diamond, as it always was")
out = run("nintendo")
check(out["where"] == {"top": "X", "left": "Y", "right": "A", "bottom": "B"},
      "the letters sit where a Super Nintendo prints them")
check(out["sends"]["A"] == EAST and out["sends"]["B"] == SOUTH,
      "and A, on the right, is sent as east -- which a game calls B. That is "
      "the complaint, and it is what the layout means rather than a fault")

print("the Xbox layout")
out = run("xbox")
check(out["where"] == {"top": "Y", "left": "X", "right": "B", "bottom": "A"},
      "the letters sit where an Xbox pad prints them")
check(out["sends"]["A"] == SOUTH and out["sends"]["B"] == EAST,
      "so the letter on the key is the letter the game is told")
check(out["sends"]["X"] == WEST and out["sends"]["Y"] == NORTH,
      "X and Y with them")

print("both layouts send the same four positions")
nin, xb = run("nintendo"), run("xbox")
check(sorted(nin["sends"].values()) == sorted(xb["sends"].values()),
      "nothing is remapped by choosing one: the same four indices go out, "
      "and only the printing differs")
for corner in ("top", "left", "right", "bottom"):
    a = nin["sends"][nin["where"][corner]]
    b = xb["sends"][xb["where"][corner]]
    check(a == b, "the %s button sends the same thing in both: %d" % (corner, a))

print("the swap, on the Nintendo diamond")
out = run("nintendo", swapped=True)
check(out["sends"]["A"] == SOUTH and out["sends"]["B"] == EAST,
      "now the button marked A is sent as A, which is what the letters promise")
check(out["sends"]["X"] == WEST and out["sends"]["Y"] == NORTH,
      "and X and Y trade with each other, not with anything else")
check(out["where"] == {"top": "X", "left": "Y", "right": "A", "bottom": "B"},
      "the letters have not moved: this trades what is sent, not what is drawn")

print("and on the Xbox layout, where it is the other way about")
out = run("xbox", swapped=True)
check(out["sends"]["A"] == EAST and out["sends"]["B"] == SOUTH,
      "it still trades the pairs, which is what somebody asking for it wants")

print("it is a switch on the panel, and it is remembered")
check('id="pads-faceswap"' in page, "the panel carries the switch")
check("keys-hidden" in page.split('id="pads-faceswap"')[0].rsplit("<p", 1)[-1],
      "shown for a controller as well as the on-screen pad: it was two "
      "controls doing one job, and the one in the panel bar has gone")
check("pads-swap" not in page and "pads-swap" not in source,
      "the old Swap A and B button is gone rather than left beside it")
check("if (faceSwapped())" in source.split("function remapped")[1][:1600],
      "a controller gets the same swap, applied as it is read rather than "
      "written into the map -- so Fix my buttons is left alone by it")
check('localStorage.setItem(FACESWAP_KEY' in source
      and "buildTouchPad" in lift("setFaceSwap"),
      "written down, and the pad rebuilt so one place decides what a key sends")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("all good")
