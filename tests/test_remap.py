"""Learning which physical button is which, one press at a time.

This shipped broken and cost somebody their controller settings: the rule ran
on every frame, so one press of A -- held for a tenth of a second, which is
sixty frames -- answered all ten prompts with A, and every other face button
stopped doing anything. The rule is a function now, and this is that function.
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


node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the browser half cannot be run.")
    sys.exit(0)

source = open(os.path.join(ROOT, "web", "app.js")).read()


def lift(name):
    """One function out of app.js, which touches the DOM at load."""
    start = source.index("function " + name + "(")
    depth, i = 0, source.index("{", start)
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[start:j + 1]
    raise AssertionError(name)


consts = re.search(r"const STANDARD_KEYS = \[.*?\];", source, re.S).group(0)
order = re.search(r"const REMAP_ORDER = \[.*?\];", source, re.S).group(0)

HARNESS = consts + "\n" + order + "\n" + lift("learnPress") + "\n" \
    + lift("promptFor") + "\n" + """
const steps = JSON.parse(require("fs").readFileSync(0, "utf8"));
let state = { map: STANDARD_KEYS.map(() => null), step: 0, armed: false };
const seen = [];
for (const hit of steps) {
  state = learnPress(state, hit);
  seen.push({ step: state.step, armed: state.armed, map: state.map.slice(0, 4),
              said: state.said });
}
process.stdout.write(JSON.stringify({ state, seen }));
"""


def run(presses):
    done = subprocess.run([node, "-e", HARNESS], input=json.dumps(presses),
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(done.stderr[:400])
    return json.loads(done.stdout)


print("a button held across many frames is one answer, not many")
# Six frames of A held down, which is what a real press looks like.
out = run([0, 0, 0, 0, 0, 0])
check(out["state"]["step"] == 0,
      "held from the start, before anything was released, nothing is learned")

out = run([-1, 0, 0, 0, 0, 0])
check(out["state"]["step"] == 1, "one press advances exactly one step")
check(out["state"]["map"][0] == 0, "and it is the button that was pressed")

print("and the next answer needs a new press")
out = run([-1, 0, 0, 0, 1, 1])
check(out["state"]["step"] == 1,
      "a second button pressed without letting go of the first is ignored")
out = run([-1, 0, 0, -1, 1, 1])
check(out["state"]["step"] == 2, "letting go and pressing again advances")
check(out["state"]["map"][1] == 1, "with the second button in the second place")

print("a button cannot do two jobs")
out = run([-1, 0, -1, 0])
check(out["state"]["step"] == 1, "pressing the same one twice does not advance")
check("already" in (out["seen"][-1]["said"] or ""),
      "and it says so: %r" % (out["seen"][-1]["said"],))

print("ten presses answer ten prompts, and no more")
presses = []
for i in range(10):
    presses += [-1, i]
out = run(presses)
check(out["state"]["step"] == 10, "all ten learned")
check(out["state"]["map"][:4] == [0, 1, 2, 3],
      "each in its own place: %s" % out["state"]["map"][:4])

print("the prompt counts up, so it is clear how many are left")
out = run([-1, 0])
check("2 of 10" in (out["seen"][-1]["said"] or ""),
      "after the first, it asks for the second: %r" % (out["seen"][-1]["said"],))



# -- rebinding one button, rather than all ten ------------------------------
#
# "Fix my buttons" walks the whole set, which is right the first time and
# heavy-handed when a single button is in the wrong place. Clicking that one
# and pressing what it should be is the small version -- and it has to leave
# the map a swap rather than growing a duplicate, because two entries reading
# the same physical button means one of them can never be pressed alone.
print("\nrebinding a single button")

ONE = consts + "\n" + lift("bindOne") + """
const job = JSON.parse(require("fs").readFileSync(0, "utf8"));
let padMap = job.map;
const stored = {};
const localStorage = { setItem: (k, v) => { stored[k] = v; } };
function mapKey() { return "k"; }
function report() {}
const said = [];
const nodes = { "pads-reset": { hidden: true },
                "pads-hint": { set textContent(v) { said.push(v); } } };
function el(id) { return nodes[id] || { hidden: true, textContent: "" }; }
bindOne(job.index, job.hit);
process.stdout.write(JSON.stringify({ map: padMap, said: said[0] || "",
                                      saved: stored.k !== undefined }));
"""


def bind(mapping, index, hit):
    done = subprocess.run([node, "-e", ONE],
                          input=json.dumps({"map": mapping, "index": index,
                                            "hit": hit}),
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(done.stderr[:400])
    return json.loads(done.stdout)


# How many standard buttons there are, read from the same table the page uses.
KEY_COUNT = len(re.findall(r'\["', consts.split("STANDARD_KEYS", 1)[1]
                           .split("];", 1)[0]))
check(KEY_COUNT >= 10, "the standard set was found: %d" % KEY_COUNT)
identity = list(range(KEY_COUNT))

got = bind(identity[:], 0, 7)
check(got["map"][0] == 7, "the button that was clicked takes the one pressed")
check(got["saved"], "and it is written down")

# Physical button 1 already belongs to entry 1, so giving it to entry 0 has to
# hand entry 1 the button entry 0 gave up.
got = bind(identity[:], 0, 1)
check(got["map"][0] == 1, "the clicked entry takes the pressed button")
check(got["map"][1] == 0, "and the entry that had it takes the one given up")
check(sorted(got["map"]) == sorted(identity),
      "so the map is still a swap, with no duplicates: %r" % got["map"])
check("took the button it gave up" in got["said"],
      "and it says so, rather than silently moving somebody else: %r"
      % got["said"])

got = bind(None, 2, 5)
check(got["map"] is not None and got["map"][2] == 5,
      "starting from no map at all works: %r" % got["map"])
check(sorted(got["map"]) == sorted(identity),
      "and still leaves a complete map: %r" % got["map"])

got = bind(identity[:], 3, 3)
check(got["map"] == identity,
      "rebinding a button to itself changes nothing: %r" % got["map"])

print(("FAILED: %d" % len(fails)) if fails else "test_remap: all ok")
sys.exit(1 if fails else 0)
