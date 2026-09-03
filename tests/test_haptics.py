"""The buzz under a thumb on glass, and the switch that turns it off.

A finger on a physical button knows it went down before the game shows
anything; a finger on a picture of a button knows nothing until the picture
changes. The buzz is what stands in for that, and it is a preference because
it costs battery, because it is audible in a quiet room, and because a
direction held for a minute must not buzz for a minute.

Four things are worth holding still here. A page that has never been asked
buzzes, so the switch cannot be a silent regression for somebody who had the
feedback before it existed. Nothing calls `navigator.vibrate` except the one
helper, or the switch would only turn off some of it. The d-pad buzzes on the
direction changing rather than on the touch continuing -- pad state goes out
125 times a second, and a buzz per frame is a phone that shakes for as long as
a thumb is down.

And the switch is offered on a browser with no vibrate() at all, which is
every iPhone. That is not a detail: it went out hidden there the first time,
on the one phone that most needed it, and somebody went looking for the option
and found an empty row. Where vibrate() is missing the buzz goes through the
off-screen switch control instead, which is the one haptic Safari still hands
a web page.
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


def lift_text(name):
    """One function's source, by brace counting. Used before node is looked for
    so the markup half of this suite runs on a machine without it."""
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
page = open(os.path.join(ROOT, "web", "index.html")).read()
style = open(os.path.join(ROOT, "web", "style.css")).read()

print("the buzz is asked for in one place")
# Every call site goes through the helper, so the switch governs all of them.
# Two mentions are the helper's own: the support test and the call itself.
# Prose about it is fine; a second place that calls it is not.
check(source.count("navigator.vibrate(") == 1,
      "navigator.vibrate is called from exactly one place")
check(source.count("typeof navigator.vibrate") == 1,
      "and asked about in exactly one place")
check('id="haptic-tap"' in page and "checkbox\" switch" in page,
      "the page carries a switch control for the phone with no vibrate()")
check('for="haptic-tap"' in page,
      "with a label pointing at it, which is the half that plays the haptic")
check("label.click()" in source and ".checked = !" not in lift_text("tapSwitch"),
      "and the tap goes through the label, without the box being flipped twice")
check(".haptic-switch" in style and "display: none" not in
      style.split(".haptic-switch")[1].split("}")[0],
      "and it is laid out rather than hidden, or it makes no feeling")
check("if (navigator.vibrate) navigator.vibrate" not in source,
      "no button reaches for the buzz on its own")

print("the switch is on the panel about how the controls feel")
check('id="pads-buzz"' in page, "the panel carries the switch")
check('<input id="pads-buzz" type="checkbox"' in page,
      "and it is a switch rather than another button: a checkbox, drawn as one")
# The row it sits in. The top bar had four buttons in it already and portrait
# squeezed them to a couple of letters each, which is what put this on its own
# line -- so the line is the fix, and it is checked rather than remembered.
bar = page.split('class="browser-bar"')[1].split("</div>")[0]
check('id="pads-buzz"' not in bar,
      "it is not crammed into the bar at the top, which was already full")
row = page.split('class="browse-note pads-feel')[1].split("</p>")[0]
check('id="pads-buzz"' in row, "it has a line of its own")
check("touch-only" in page.split('class="browse-note pads-feel')[1].split(">")[0],
      "marked as belonging to the on-screen pad")
check(".pads:not(.touch) .touch-only { display: none; }" in style,
      "and out of the way when the controls are not the on-screen pad")
check(".switch-track" in style and ".switch-knob" in style,
      "and it is drawn as a track and a knob, not as a tickbox")

node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the browser half cannot be run.")
    sys.exit(1 if fails else 0)


lift = lift_text


def constant(name):
    return re.search(r"^const " + name + r" = .*$", source, re.M).group(0)


HARNESS = "\n".join(
    [constant("HAPTICS_KEY"), constant("BUZZ_MS")]
    + [lift(n) for n in ("tapSwitch", "savedHaptics", "buzz", "paintBuzz",
                         "setHaptics", "applyDpad", "clearDpad")]) + """
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
const canVibrate = typeof navigator.vibrate === "function";

/* The off-screen control, as the two halves iOS distinguishes: clicking the
   label is what plays the haptic, and clicking the input is what does not, so
   the test counts them apart rather than counting "a click". */
const flips = [];
const deadClicks = [];
const tapBox = { checked: false, click() { deadClicks.push(1); } };
const tapLabel = { click() { tapBox.checked = !tapBox.checked; flips.push(1); } };

let hapticsOn = savedHaptics();
// The visible switch is a checkbox, and its state is the checkbox's state.
const box = { checked: null };
const note = { textContent: "" };
// The panel remembers the last class it was told about, which is how the test
// sees whether the switch is offered at all.
let offered = null;
const panel = { classList: { toggle: (name, on) => { offered = on; } } };
const touch = { hidden: !!job.padHidden };
const el = (id) => (id === "pads-buzz" ? box
                    : id === "pads-buzz-note" ? note
                    : id === "touch" ? touch
                    : id === "haptic-label" ? (job.noSwitch ? null : tapLabel)
                    : id === "haptic-tap" ? tapBox
                    : panel);
const canBuzz = canVibrate || !!el("haptic-label");

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
  buzzes, flips, deadClicks, stored: store, on: hapticsOn,
  checked: box.checked, note: note.textContent, flipped: tapBox.checked,
  offered }));
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
check(out["checked"] is True, "and the switch is drawn on")

print("a page that was told no stays told")
out = run(stored={"fp:haptics": "0"}, taps=3)
check(out["on"] is False, "the stored off is read back as off")
check(out["buzzes"] == [], "and nothing buzzes")
check(out["checked"] is False and "quiet" in out["note"],
      "the switch is drawn off, and says so in words underneath")

print("the switch is remembered")
out = run(set=False, taps=2)
check(out["stored"].get("fp:haptics") == "0", "turning it off is written down")
check(out["buzzes"] == [], "and takes effect at once")
out = run(stored={"fp:haptics": "0"}, set=True, taps=1)
check("fp:haptics" not in out["stored"],
      "turning it back on clears the key rather than storing the default")
# Turning it on answers with the thing it turns on: a switch for a feeling is
# hard to check any other way.
check(out["buzzes"] == [30, 8], "and answers with a buzz of its own")

print("a phone with no vibrate() is offered the switch, and taps another way")
# The regression this suite exists for: hidden on the iPhone, which is the
# phone that cannot buzz without the trick and so needs the switch most.
out = run(canVibrate=False, taps=3)
check(out["offered"] is True, "the switch is offered on a browser with no vibrate")
check(out["buzzes"] == [], "nothing is called that is not there")
check(len(out["flips"]) == 3, "and three taps flip the switch control three times")
check(out["deadClicks"] == [],
      "through the label, never the input -- WebKit plays nothing for the input")
check(out["flipped"] is True,
      "and the box is left flipped, not put back where it started")
out = run(canVibrate=False, stored={"fp:haptics": "0"}, taps=3)
check(out["flips"] == [], "the off switch governs that path too")
out = run(taps=2)
check(out["flips"] == [] and out["buzzes"] == [8, 8],
      "a phone with vibrate() uses it and leaves the trick alone")

print("a phone that taps a different way is told how")
out = run(canVibrate=False)
check("as you let go" in out["note"] and "buttons" in out["note"],
      "the note says the tap lands on release, and only on the buttons")
out = run()
check("as you let go" not in out["note"],
      "and says nothing of the sort where vibrate() exists")

print("the switch is only offered where it means something")
out = run()
check(out["offered"] is True, "with the pad on the screen, it is offered")
out = run(padHidden=True)
check(out["offered"] is False,
      "a guest playing on a controller or a keyboard is not offered it")
out = run(canVibrate=False, noSwitch=True)
check(out["offered"] is False,
      "and neither is a browser with no way to tap at all")

print("a button is a label with a switch in it")
wired = lift_text("wireTouch")
check("const bySwitch = hapticsOn && !canVibrate;" in wired,
      "the press knows which of the two ways it is making a feeling")
check(wired.count("if (!bySwitch) {") == 1
      and "event.preventDefault();" in wired.split("if (!bySwitch) {")[1],
      "and leaves the browser's own handling alone when the switch is it --"
      " a prevented pointerdown is a click that never happens")
check("setPointerCapture" in wired.split("if (!bySwitch) {")[1].split("}")[0],
      "capture too, which would retarget the click away from the label")
check(wired.count('window.addEventListener("pointerup"') == 1,
      "and a release that does not need capture to be heard")
built = lift_text("makeButton")
check('createElement("label")' in built and '"switch"' in built,
      "the button itself is a label around a switch, not a <button>")
check("interactive content nested in a" in source,
      "with the reason it cannot be a <button> written down beside it")

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
