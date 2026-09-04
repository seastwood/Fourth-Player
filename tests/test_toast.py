"""Somebody joining, said quietly.

An arrival used the notice, and the notice is built for "there is no video":
it opens the chips and takes the stage out of the stripped-back view to make
room for itself. That is the right trade when the picture is gone and quite
the wrong one for "Ada joined" arriving in the middle of a game -- the
picture shrinks, and the person playing has just been interrupted by news
they did not need.

So arrivals get a line over the corner of the picture that changes no layout
at all. What is worth holding still is exactly that: that this path does not
reach for the notice, does not ask for the chips, and does not leave
immersive mode. Any of the three brings the shrinking back.
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


source = open(os.path.join(ROOT, "web", "app.js")).read()
style = open(os.path.join(ROOT, "web", "style.css")).read()
page = open(os.path.join(ROOT, "web", "index.html")).read()


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


print("it is out of the way by construction")
check('id="toast"' in page, "the toast is in the page")
rule = re.search(r"\.toast \{([^}]*)\}", style)
check(bool(rule), "and has a rule of its own")
if rule:
    body = rule.group(1)
    check("position: absolute" in body,
          "out of flow, so nothing it says can resize the picture")
    check("pointer-events: none" in body,
          "and never in the way of a thumb on the game")

print("\nand an arrival does not reach for the things that shrink the stage")
arrived = lift("somebodyArrived")
check("showNotice" not in arrived,
      "not the notice, which opens the chips to fit itself in")
check("showHud" not in arrived,
      "and not the chips directly either")
check("immersive" not in arrived,
      "and it does not leave the stripped-back view")
check("showToast" in arrived, "it uses the toast")

if not shutil.which("node"):
    print("\nSKIPPED the behaviour: node is not installed")
    sys.exit(1 if fails else 0)

harness = """
'use strict';
const called = [];
let toastTimer = null;
const boxes = {};
function node(tag) {
  return { tagName: tag, className: "", children: [], _text: "",
           classes: {}, hidden: true, offsetWidth: 1,
           set textContent(v) { this._text = v; },
           get textContent() {
             return this._text + this.children.map((c) => c.textContent).join(" ");
           },
           set innerHTML(v) { if (!v) this.children = []; },
           get innerHTML() { return ""; },
           appendChild(c) { this.children.push(c); return c; },
           classList: {
             add(c) { called.push("class+" + c); },
             remove(c) { called.push("class-" + c); },
             contains() { return false; },
           } };
}
global.document = { createElement: node };
boxes.toast = node("div");
boxes.slot = node("span");
boxes.slot.textContent = "Me";
global.el = (id) => boxes[id] || (boxes[id] = node("div"));
global.showNotice = () => called.push("showNotice");
global.showHud = () => called.push("showHud");
global.escapeText = (t) => t;
global.setTimeout = (fn, ms) => { called.push("timer:" + ms); return 1; };
global.clearTimeout = () => {};

%(code)s

const out = {};
somebodyArrived({ label: "Ada", guests: 3, slots: 4 });
out.afterOther = {
  called: called.slice(),
  shown: boxes.toast.hidden === false,
  says: boxes.toast.textContent,
};

// The page's own arrival is its own business, and it is told about itself.
called.length = 0;
boxes.toast.hidden = true;
somebodyArrived({ label: "Me", guests: 1, slots: 4 });
out.afterSelf = { called: called.slice(), shown: boxes.toast.hidden === false };
console.log(JSON.stringify(out));
""" % {"code": "\n\n".join(
    [next(l for l in source.split("\n") if l.startswith("const TOAST_MS")),
     lift("showToast"), lift("somebodyArrived")])}

proc = subprocess.run([shutil.which("node"), "-e", harness],
                      capture_output=True, text=True)
if proc.returncode != 0:
    print("  FAIL  node could not run it:\n" + proc.stderr.strip()[-1200:])
    sys.exit(1)
out = json.loads(proc.stdout.strip().splitlines()[-1])

print("\nwhen somebody else joins")
one = out["afterOther"]
check(one["shown"], "the toast is shown")
check("Ada" in one["says"] and "3 of 4" in one["says"],
      "saying who, and how many are playing; got %r" % one["says"])
check("showNotice" not in one["called"],
      "the notice is never opened, which is what used to shrink the picture")
check("showHud" not in one["called"], "and neither are the chips")
check(any(c.startswith("timer:") for c in one["called"]),
      "it takes itself away again rather than staying until something else "
      "clears it")

print("\nand when the page is told about itself")
two = out["afterSelf"]
check(not two["shown"] and not two["called"].count("showNotice"),
      "nothing is said: everybody is told about every arrival including "
      "their own, and 'you joined' is not news")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_toast: all ok")
