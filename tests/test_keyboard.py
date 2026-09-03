"""Playing with a keyboard, when there is no controller in the house.

A key is a button and nothing else here: the map goes from a standard gamepad
button to a `KeyboardEvent.code`, the page merges the result into the same
frame the on-screen pad and a real controller feed, and what leaves the browser
is a pad frame exactly as it always was. Nothing about this puts a keystroke on
the wire -- the device each guest is wired to on the host declares gamepad
capabilities and cannot express one -- and `tests/test_pads.py` is what holds
that end down.

What is worth testing here is the part with rules in it: the defaults are the
arrangement anybody who has played an emulator on a keyboard already knows,
codes rather than characters so a French keyboard does not silently rearrange
itself, no sticks at all, and rebinding trades keys rather than duplicating one
-- two buttons on one key means one of them can never be pressed alone.
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
    """One function out of app.js, which touches the DOM at load."""
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


standard = re.search(r"const STANDARD_KEYS = \[.*?\];", source, re.S).group(0)
defaults = re.search(r"const KEY_DEFAULTS = .*?\}\);", source, re.S).group(0)

node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the browser half cannot be run.")
    sys.exit(0)

HARNESS = standard + "\n" + defaults + "\n" + lift("keyLabel") + "\n" \
    + lift("bindKeyInto") + "\n" + """
const ask = JSON.parse(require("fs").readFileSync(0, "utf8"));
const out = {
  defaults: KEY_DEFAULTS,
  labels: ask.labels.map(keyLabel),
  binds: ask.binds.map(([map, index, code]) => bindKeyInto(map, index, code)),
};
process.stdout.write(JSON.stringify(out));
"""


def run(labels, binds):
    done = subprocess.run([node, "-e", HARNESS],
                          input=json.dumps({"labels": labels, "binds": binds}),
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(done.stderr[:500])
    return json.loads(done.stdout)


# Standard gamepad order: A B X Y, LB RB, LT RT, back start, L3 R3, then the
# d-pad, then the guide button.
NAMES = ["A", "B", "X", "Y", "LB", "RB", "LT", "RT", "BACK", "START",
         "L3", "R3", "UP", "DOWN", "LEFT", "RIGHT", "GUIDE"]

answer = run(["KeyZ", "ArrowUp", "ShiftRight", "Enter", "Digit4", "Numpad7",
              "Semicolon", "Space", None, ""],
             [[["KeyZ", "KeyX"], 0, "KeyN"],          # a free key
              [["KeyZ", "KeyX"], 0, "KeyX"],          # one the other one has
              [[None, "KeyX"], 0, "KeyX"]])           # from nothing at all
defaults = answer["defaults"]

print("every button a keyboard can answer for has a key")
bound = {NAMES[i]: code for i, code in enumerate(defaults) if code}
check(len(defaults) == len(NAMES), "the map covers the standard pad")
check(bound.get("UP") == "ArrowUp" and bound.get("DOWN") == "ArrowDown"
      and bound.get("LEFT") == "ArrowLeft" and bound.get("RIGHT") == "ArrowRight",
      "the arrows are the d-pad: %s" % {k: bound.get(k) for k in
                                        ("UP", "DOWN", "LEFT", "RIGHT")})
check(all(bound.get(name) for name in ("A", "B", "X", "Y")),
      "the four face buttons are bound: %s"
      % {k: bound.get(k) for k in ("A", "B", "X", "Y")})
check(all(bound.get(name) for name in ("LB", "RB", "LT", "RT")),
      "and both shoulders and both triggers: %s"
      % {k: bound.get(k) for k in ("LB", "RB", "LT", "RT")})
check(bound.get("START") == "Enter" and bound.get("BACK") == "ShiftRight",
      "start and select are where an emulator puts them: %s"
      % {k: bound.get(k) for k in ("START", "BACK")})

print("the sticks are left alone, because a key cannot be a position")
check(defaults[NAMES.index("L3")] is None and defaults[NAMES.index("R3")] is None,
      "the stick buttons are unbound")
check(defaults[NAMES.index("GUIDE")] is None, "and so is the guide button")

print("nothing is bound twice, or one of them could never be pressed alone")
codes = [c for c in defaults if c]
check(len(codes) == len(set(codes)), "every default key is its own: %s" % codes)

print("the map is written in key positions, not letters")
check(all(c.startswith(("Key", "Arrow", "Digit", "Shift", "Enter", "Space"))
          for c in codes),
      "so a French or German keyboard keeps the same shape: %s" % codes)

print("a key is shown as the thing under somebody's finger")
labels = answer["labels"]
check(labels[0] == "Z", "KeyZ is Z")
check(labels[1] == "↑", "ArrowUp is an arrow, got %r" % labels[1])
check(labels[2] == "R Shift", "ShiftRight says which shift, got %r" % labels[2])
check(labels[3] == "Enter", "Enter is itself")
check(labels[4] == "4" and labels[5] == "Num 7",
      "digits and the number pad are told apart: %r" % labels[4:6])
check(labels[6] == ";" and labels[7] == "Space",
      "punctuation is printed, not spelled: %r" % labels[6:8])
check(labels[8] == "—" and labels[9] == "—",
      "and nothing bound is a dash rather than a blank: %r" % labels[8:10])

print("rebinding trades keys rather than making a duplicate")
free, taken, empty = answer["binds"]
check(free["map"] == ["KeyN", "KeyX"] and free["clash"] == -1,
      "a key nothing else wants is simply taken: %s" % free)
check(taken["map"] == ["KeyX", "KeyZ"] and taken["clash"] == 1,
      "and one that is spoken for swaps: %s" % taken)
check(empty["map"] == ["KeyX", None] and empty["clash"] == 1,
      "including when the button being given a key had none: %s" % empty)

print("the wire is unchanged: this is buttons, not keystrokes")
check("keyButtons" in source and "state.buttons | touchButtons | keyButtons"
      in source, "the keys are merged into the same pad frame")
check("FPFrame.buildRaw" in source and "keyCode" not in source,
      "and nothing else is sent")

print(("FAILED: %d" % len(fails)) if fails else "test_keyboard: all ok")
sys.exit(1 if fails else 0)
