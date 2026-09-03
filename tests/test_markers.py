"""Classes the code puts on the controls panel to remember what it is showing.

One of them was `touch`, meaning "the controls are the on-screen pad". `.touch`
is also the on-screen pad itself, and sideways that is a transparent sheet
over the picture -- `pointer-events: none`, with only its clusters taking
taps. So the panel was handed a rule written for something else: in landscape
every tap went straight through it to the pad underneath, nothing on it could
be pressed, the close button included, and it would not scroll either. Upright
it was fine, because upright the pad is not a sheet over anything.

Reproduced in headless Chrome at the phone's own size before it was believed,
and again after it was fixed. What is held still here is the general form of
it rather than that one name: a marker class the code invents for one element
must not be a class the page already gives to another. There is no warning
for that -- CSS matches on names, not intentions -- and the failure is silent
and orientation-dependent, which is the worst kind to go looking for.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


app = open(os.path.join(ROOT, "web", "app.js")).read()
page = open(os.path.join(ROOT, "web", "index.html")).read()
style = open(os.path.join(ROOT, "web", "style.css")).read()

# Every class the page hands out in markup, which is every name a stylesheet
# rule may already be attached to.
worn = set()
for attr in re.findall(r'class="([^"]*)"', page):
    worn.update(attr.split())

# Every class the code puts on the panel. Both are written as
# `panel.classList.toggle("name", ...)`, the panel being el("pads").
markers = set(re.findall(r'panel\.classList\.toggle\("([\w-]+)"', app))

print("the panel's markers are its own")
check(markers, "there are markers to check: %s" % sorted(markers))
for marker in sorted(markers):
    check(marker not in worn,
          "`%s` is not a class the page gives to some other element" % marker)
    # And the stylesheet must only ever mention it through the panel, or the
    # same collision is one rule away from coming back.
    for hit in re.findall(r'^[^{}\n]*\.%s\b[^{}\n]*(?=\{)' % re.escape(marker),
                          style, re.M):
        check(".pads" in hit,
              "every rule naming .%s goes through the panel: %s"
              % (marker, hit.strip()))

print("and the panel can still be touched")
check("pointer-events" not in style.split(".pads {")[1].split("}")[0],
      "nothing in the panel's own rule takes its taps away")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("all good")
