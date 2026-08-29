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

print(("FAILED: %d" % len(fails)) if fails else "test_remap: all ok")
sys.exit(1 if fails else 0)
