"""The buzz under a thumb on glass, and the switch that turns it off.

A finger on a physical button knows it went down before the game shows
anything; a finger on a picture of a button knows nothing until the picture
changes. The buzz is what stands in for that, and it is a preference because
it costs battery, because it is audible in a quiet room, and because a
direction held for a minute must not buzz for a minute.

Three things are worth holding still here. A page that has never been asked
buzzes, so the switch cannot be a silent regression for somebody who had the
feedback before it existed. Nothing calls `navigator.vibrate` except the one
helper, or the switch would only turn off some of it. And the d-pad buzzes on
the direction changing rather than on the touch continuing -- pad state goes
out 125 times a second, and a buzz per frame is a phone that shakes for as
long as a thumb is down.
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
style = open(os.path.join(ROOT, "web", "style.css")).read()

print("the buzz is asked for in one place")
# Every call site goes through the helper, so the switch governs all of them.
# Two mentions are the helper's own: the support test and the call itself.
check(source.count("navigator.vibrate") == 2,
      "navigator.vibrate is only named by buzz() and its support test")
check("if (navigator.vibrate) navigator.vibrate" not in source,
      "no button reaches for the buzz on its own")

print("the switch is on the panel about how the controls feel")
check('id="pads-buzz"' in page, "the panel carries the switch")
tag = page.split('id="pads-buzz"')[1].split(">")[0]
check("touch-only" in tag,
      "the switch is marked as belonging to the on-screen pad")
check(".pads:not(.touch) .touch-only { display: none; }" in style,
      "and is out of the way when the controls are not the on-screen pad")

node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the browser half cannot be run.")
    sys.exit(1 if fails else 0)


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


def constant(name):
    return re.search(r"^const " + name + r" = .*$", source, re.M).group(0)


HARNESS = "\n".join(
    [constant("HAPTICS_KEY"), constant("BUZZ_MS")]
    + [lift(n) for n in ("savedHaptics", "buzz", "paintBuzz", "setHaptics",
                         "applyDpad", "clearDpad")]) + """
const job = JSON.parse(require("fs").readFileSync(0, "utf8"));

// A localStorage that starts where the job says it starts, and remembers what
// was done to it -- the point being what the *next* visit would read.
const store = Object.assign({}, job.stored);
const localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};

const buzzes = [];
const navigator = job.canVibrate
  ? { vibrate: (ms) => { buzzes.push(ms); return true; } }
  : {};
const canBuzz = typeof navigator.vibrate === "function";

let hapticsOn = savedHaptics();
const button = {
  textContent: "", classList: { toggle() {} },
  setAttribute(name, value) { this[name] = value; },
};
// The panel remembers the last class it was told about, which is how the test
// sees whether the switch is offered at all.
let offered = null;
const panel = { classList: { toggle: (name, on) => { offered = on; } } };
const touch = { hidden: !!job.padHidden };
const el = (id) => (id === "pads-buzz" ? button
                    : id === "touch" ? touch : panel);

// Enough of the d-pad to press: the bits and the lights are somebody else's
// test, and what this one watches is the buzzing.
const DPAD = { up: 12, down: 13, left: 14, right: 15 };
let dpadLive = "";
function setBit() {}
function paintDpad() {}
function dpadDirections(event) { return event.dirs; }

if (job.set !== undefined) setHaptics(job.set);
for (const dirs of (job.presses || [])) {
  if (dirs === null) clearDpad(); else applyDpad({ dirs });
}
for (let i = 0; i < (job.taps || 0); i++) buzz();
paintBuzz();
process.stdout.write(JSON.stringify({
  buzzes, stored: store, on: hapticsOn, label: button.textContent,
  pressed: button["aria-pressed"], offered }));
"""


def run(stored=None, canVibrate=True, **job):
    job.update(stored=stored or {}, canVibrate=canVibrate)
    done = subprocess.run([node, "-e", HARNESS], input=json.dumps(job),
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(done.stderr[:600])
    return json.loads(done.stdout)


print("a page that has never been asked buzzes")
out = run(taps=3)
check(out["on"] is True, "with nothing stored, the buzz is on")
check(out["buzzes"] == [8, 8, 8], "and three taps are three buzzes")
check(out["label"] == "Buzz on tap: on" and out["pressed"] == "true",
      "the switch says so, and says it to a screen reader too")

print("a page that was told no stays told")
out = run(stored={"fp:haptics": "0"}, taps=3)
check(out["on"] is False, "the stored off is read back as off")
check(out["buzzes"] == [], "and nothing buzzes")
check(out["label"] == "Buzz on tap: off" and out["pressed"] == "false",
      "the switch says so")

print("the switch is remembered")
out = run(set=False, taps=2)
check(out["stored"].get("fp:haptics") == "0", "turning it off is written down")
check(out["buzzes"] == [], "and takes effect at once")
out = run(stored={"fp:haptics": "0"}, set=True, taps=1)
check("fp:haptics" not in out["stored"],
      "turning it back on clears the key rather than storing the default")
# Turning it on answers with the thing it turns on: a switch for a feeling is
# hard to check any other way.
check(out["buzzes"] == [24, 8], "and answers with a buzz of its own")

print("the switch is only offered where it means something")
out = run()
check(out["offered"] is True, "with the pad on the screen, it is offered")
out = run(canVibrate=False)
check(out["offered"] is False, "a browser with no vibrate is not offered it")
out = run(padHidden=True)
check(out["offered"] is False,
      "and neither is a guest playing on a controller or a keyboard")
out = run(canVibrate=False, taps=3)
check(out["buzzes"] == [], "nothing is called that is not there")

print("the d-pad buzzes on the direction, not on the frame")
out = run(presses=[["left"], ["left"], ["left"]])
check(out["buzzes"] == [8], "a thumb held on one arm buzzes once")
out = run(presses=[["left"], ["left", "up"], ["up"]])
check(out["buzzes"] == [8, 8, 8],
      "sliding through a diagonal buzzes for each direction it becomes")
out = run(presses=[["left"], None, ["left"]])
check(out["buzzes"] == [8, 8], "and letting go and pressing again is two")
out = run(presses=[[]])
check(out["buzzes"] == [], "the dead middle of the pad is not a press")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("all good")
