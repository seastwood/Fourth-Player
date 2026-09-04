"""The failure explanation, and giving the prompt back afterwards.

"The video connection could not be rebuilt" appearing over a picture that is
plainly working, with no way to close it, so the only way out is to shut the
page and open it again.

mediaFailed borrows the prompt to explain itself -- the box that normally says
"press any button on your controller" -- because it is the only thing on the
page big enough for the explanation. Borrowing it is fine. Not giving it back
was the bug, twice over:

  * Nothing cleared it when video came back. Every other stale thing on the
    page is dropped the moment bytes arrive; this one was not, so a
    connection that failed once and recovered kept explaining the failure for
    the rest of the session.
  * It could not be dismissed. The click-to-close on this page is attached to
    the notice, and this is the prompt -- a different element -- so there was
    no way out of it at all.

And the hint's own words had been overwritten, so even after the panel was
hidden, anything that later asked for the controller hint got the failure
text instead.
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


def lift(name):
    start = source.index("function " + name + "(")
    # `async function x(` -- step back over the keyword if it is there.
    if source[max(0, start - 6):start] == "async ":
        start -= 6
    depth = 0
    for j in range(source.index("{", start), len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[start:j + 1]
    raise AssertionError(name)


print("the wiring, without needing a browser")
# The recovery branch: where bytes arriving makes everything stale.
recovery = source[source.index("if (bytes > lastBytes) {"):]
recovery = recovery[:recovery.index("return;")]
check("clearMediaFailure()" in recovery,
      "video arriving clears the failure explanation, the same place it "
      "clears the stale notice and the link chip")
check("prompt-close" in source,
      "and the explanation carries something to close it with")
check('el("prompt-close")' not in source,
      "found by class off the prompt rather than by a document-wide id, "
      "because it is written into the page rather than living in it")

if not shutil.which("node"):
    print("\nSKIPPED the behaviour: node is not installed")
    sys.exit(1 if fails else 0)

harness = """
'use strict';
let renewals = 4;
let promptHint = null;
let promptFailed = false;
const boxes = {
  prompt: { hidden: true, innerHTML: "<p>Press any button.</p>", handlers: {},
            querySelector: () => closeButton },
};
let closeButton = null;
global.el = (id) => {
  if (!boxes[id]) boxes[id] = { hidden: true, innerHTML: "" };
  return boxes[id];
};
global.setLink = () => {};
global.showHud = () => {};
global.report = () => {};
global.describeRoute = async () => "a route";

%(code)s

// The close button only exists once the explanation has been written, which
// is when mediaFailed attaches to it.
const original = boxes.prompt.innerHTML;
Object.defineProperty(boxes.prompt, "innerHTML", {
  get() { return this._html === undefined ? original : this._html; },
  set(v) {
    this._html = v;
    closeButton = /prompt-close/.test(v)
      ? { addEventListener: (_k, fn) => { closeButton.fire = fn; } }
      : null;
  },
});

(async () => {
  const out = {};
  await mediaFailed("The video connection could not be rebuilt.");
  out.afterFailure = {
    shown: boxes.prompt.hidden === false,
    says: /could not be rebuilt/.test(boxes.prompt.innerHTML),
    hasClose: closeButton !== null,
  };

  // The picture comes back.
  clearMediaFailure();
  out.afterRecovery = {
    hidden: boxes.prompt.hidden === true,
    hintBack: /Press any button/.test(boxes.prompt.innerHTML),
    stillSaysFailure: /could not be rebuilt/.test(boxes.prompt.innerHTML),
    renewals,
  };

  // A second failure, dismissed by hand this time.
  renewals = 6;
  await mediaFailed("The video connection could not be rebuilt.");
  const before = boxes.prompt.hidden;
  if (closeButton && closeButton.fire) closeButton.fire();
  out.afterDismiss = {
    wasShown: before === false,
    hidden: boxes.prompt.hidden === true,
    hintBack: /Press any button/.test(boxes.prompt.innerHTML),
    renewals,
  };

  // And clearing when nothing failed must not blank the hint.
  boxes.prompt.hidden = false;
  clearMediaFailure();
  out.harmless = { stillShown: boxes.prompt.hidden === false };
  console.log(JSON.stringify(out));
})();
""" % {"code": "\n\n".join([lift("clearMediaFailure"), lift("mediaFailed")])}

proc = subprocess.run([shutil.which("node"), "-e", harness],
                      capture_output=True, text=True)
if proc.returncode != 0:
    print("  FAIL  node could not run it:\n" + proc.stderr.strip()[-1500:])
    sys.exit(1)
out = json.loads(proc.stdout.strip().splitlines()[-1])

print("\nwhen the video really has failed")
one = out["afterFailure"]
check(one["shown"], "the explanation is shown")
check(one["says"], "and says what went wrong")
check(one["hasClose"], "and offers a way to close it")

print("\nand when the picture comes back")
two = out["afterRecovery"]
check(two["hidden"], "it goes away on its own, without anybody reloading")
check(not two["stillSaysFailure"] and two["hintBack"],
      "and the prompt is the controller hint again, not the failure text "
      "left behind in it")
check(two["renewals"] == 0,
      "the renewals spent failing are given back, or the next hiccup is over "
      "the limit before it has tried anything; got %s" % two["renewals"])

print("\nand when somebody closes it themselves")
three = out["afterDismiss"]
check(three["wasShown"] and three["hidden"], "it closes")
check(three["hintBack"], "and gives the prompt back the same way")

print("\nclearing when nothing failed leaves the prompt alone")
check(out["harmless"]["stillShown"],
      "the controller hint is not hidden by a clear that had nothing to do")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_prompt: all ok")
