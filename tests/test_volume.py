"""The guest's own loudness, and an icon that says what it is.

The level belongs to the person holding the phone, not to the television: a
guest turning their sound down must not silence the room everybody else is
playing in. And the icon has to agree with what is coming out of the speaker,
which means muted and zero cannot be allowed to drift apart.
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


HARNESS = "\n".join([lift("volume"), lift("paintVolume"), lift("setVolume"),
                     lift("savedVolume")]) + """
const VOLUME_KEY = "fp:volume";
const store = {};
const localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = v; },
};
const nodes = {};
function el(id) {
  if (!nodes[id]) nodes[id] = {
    value: "100", title: "", hidden: false, style: { props: {} },
    classes: new Set(), attrs: {},
    classList: {
      toggle(c, on) { on ? nodes[id].classes.add(c) : nodes[id].classes.delete(c); },
      contains: (c) => nodes[id].classes.has(c),
      remove(c) { nodes[id].classes.delete(c); },
    },
    setAttribute(k, v) { this.attrs[k] = v; },
  };
  if (!nodes[id].style.setProperty) {
    nodes[id].style.setProperty = (k, v) => { nodes[id].style.props[k] = v; };
  }
  return nodes[id];
}
const video = { volume: 1, muted: false, plays: 0,
                play() { this.plays++; return Promise.resolve(); } };

const job = JSON.parse(require("fs").readFileSync(0, "utf8"));
if (job.saved !== undefined) store[VOLUME_KEY] = String(job.saved);
if (job.set !== undefined) setVolume(job.set, job.remember !== false);
process.stdout.write(JSON.stringify({
  video: { volume: video.volume, muted: video.muted, plays: video.plays },
  range: nodes["vol-range"] ? nodes["vol-range"].value : null,
  fill: nodes["vol-range"] ? nodes["vol-range"].style.props["--fill"] : null,
  classes: nodes["vol"] ? [...nodes["vol"].classes].sort() : [],
  said: nodes["vol-btn"] ? nodes["vol-btn"].title : null,
  unmuteHidden: nodes["unmute"] ? nodes["unmute"].hidden : null,
  stored: store[VOLUME_KEY] === undefined ? null : store[VOLUME_KEY],
}));
"""


def run(job):
    done = subprocess.run([node, "-e", HARNESS], input=json.dumps(job),
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(done.stderr[:500])
    return json.loads(done.stdout)


print("the icon says the level without anybody opening the slider")
loud = run({"set": 1})
check("is-high" in loud["classes"], "full is two waves: %r" % loud["classes"])
quiet = run({"set": 0.3})
check(quiet["classes"] == ["is-low"], "a third is one wave: %r" % quiet["classes"])
off = run({"set": 0})
check(off["classes"] == ["is-off"], "nothing is crossed out: %r" % off["classes"])
check("is-low" not in loud["classes"] and "is-high" not in quiet["classes"],
      "and exactly one reading shows at a time")

print("zero and muted are the same thing, so they cannot disagree")
check(off["video"]["muted"] is True and off["video"]["volume"] == 0,
      "silence is both: %r" % off["video"])
check(loud["video"]["muted"] is False, "and sound is neither")

print("the bar is filled to the level")
check(run({"set": 0.4})["fill"] == "40%", "40%% fills 40%%")
check(off["fill"] == "0%", "and none at zero")

print("raising it counts as the gesture a browser wants")
up = run({"set": 0.8})
check(up["video"]["plays"] == 1, "playback is asked for again")
check(up["unmuteHidden"] is True, "and the 'sound off' prompt goes away")
check(run({"set": 0})["video"]["plays"] == 0,
      "turning it off asks for nothing")

print("it is remembered, and out-of-range values are not")
check(run({"set": 0.55})["stored"] == "0.55", "the level is stored")
check(run({"set": 5})["video"]["volume"] == 1, "above one clamps to one")
check(run({"set": -3})["video"]["volume"] == 0, "below zero clamps to zero")
check(run({"set": 0.5, "remember": False})["stored"] is None,
      "and restoring a saved level does not re-save it")

print("what was chosen last time is what comes back")
check(run({"saved": 0.25, "set": 0.25, "remember": False})["range"] == "25",
      "the slider shows it")

print("the number is announced, not only drawn")
check("30" in quiet["said"] and "%" in quiet["said"],
      "with a percentage: %r" % quiet["said"])
check("off" in off["said"].lower(), "and silence says so: %r" % off["said"])

print("\nthe level is this guest's own")
# Nothing here may reach for the host. A volume that travelled would silence
# the television for everybody in the room.
block = source[source.index("const VOLUME_KEY"):]
block = block[:block.index("setVolume(savedVolume(), false);")]
check("send(" not in block,
      "the volume code sends nothing to the host: %r"
      % [l.strip() for l in block.splitlines() if "send(" in l])

print("\nand it takes no room at all when it is closed")
# A flex item's min-width is auto, which for a form control resolves to its own
# intrinsic width -- so width:0 alone left a slider's worth of empty space
# between the speaker and the chip after it.
css = open(os.path.join(ROOT, "web", "style.css")).read()
collapsed = re.search(r"\.vol-range \{([^}]*)\}", css).group(1)
check("width: 0" in collapsed, "the closed slider is zero wide")
check("min-width: 0" in collapsed,
      "and is allowed to be, which width alone does not achieve")
opened = re.search(r"\.vol\.open \.vol-range \{([^}]*)\}", css).group(1)
check("width:" in opened, "opening gives it a width back")

print()
if fails:
    print("FAILURES: %d" % len(fails))
    sys.exit(1)
print("test_volume: all ok")
