"""The pad has to fit the screen that is on the phone, not the one the phone
reports.

A `position: fixed` element is laid out against the layout viewport, which is
the window as it would be with no address bar and no tab strip. `vh` measures
that same imaginary window. So on a phone showing 300 of its 390 points, both
say 390, everything anchored to the bottom is placed 90 points below the glass,
and nothing about it is visible from a desktop browser -- the two viewports are
identical there, which is how this shipped twice.

The stage is sized from `visualViewport` instead, and the pad sizes itself
against the stage. These checks are the cheap part: that no landscape rule has
gone back to `vh`, and that the measuring code is still wired up.
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


css = open(os.path.join(ROOT, "web", "style.css")).read()
js = open(os.path.join(ROOT, "web", "app.js")).read()
html = open(os.path.join(ROOT, "web", "index.html")).read()

print("the stage is measured, not assumed")
check("--vv-height" in css and "var(--vv-height" in css,
      "the stage takes its height from the measured viewport")
check("visualViewport" in js and "fitStage" in js,
      "app.js measures the visual viewport")
for event in ("resize", "scroll"):
    check(f'visualViewport.addEventListener("{event}"' in js,
          f"the measurement is refreshed on visual viewport {event}")
check("orientationchange" in js, "and on rotation")

print("the landscape pad sizes against the stage")
landscape = css[css.index("@media (orientation: landscape)"):]
landscape = landscape[:landscape.index("\n}\n\n")]
# Comments in here quote the old vh values on purpose, to say what went wrong.
bare = re.sub(r"/\*.*?\*/", "", landscape, flags=re.S)
offenders = [line.strip() for line in bare.splitlines()
             if re.search(r"\d(?:\.\d+)?vh\b", line)]
check(not offenders,
      "no landscape rule sizes itself in vh: " + (offenders[0] if offenders else "none"))
for rule in (".dpad", ".face"):
    match = re.search(re.escape(rule) + r" \{ width: min\(calc\(\d+ \* var\(--sh\)\)", landscape)
    check(match is not None, f"{rule} is a fraction of the stage")

print("a guest can see and fix their own controller")
check('id="pads"' in html and 'id="padtest"' in html,
      "there is a panel and a way to open it")
check("STANDARD_KEYS" in js and "remapped(" in js,
      "the buttons are named and the mapping can be overridden")
check("if (remapStep >= 0) return;" in js,
      "and presses made while remapping do not reach the game")
check("localStorage.setItem(mapKey()" in js,
      "a mapping is remembered per controller")

print("the page can be installed to escape the browser furniture")
check('name="apple-mobile-web-app-capable" content="yes"' in html,
      "added to the home screen, it opens without Safari's bars")
check('rel="manifest"' in html and 'rel="apple-touch-icon"' in html,
      "a manifest and a touch icon are linked")
check("start_url" not in open(
          os.path.join(ROOT, "web", "manifest.webmanifest")).read(),
      "no start_url, so an installed copy keeps the invite it was added with")
check(os.path.isfile(os.path.join(ROOT, "web", "icons", "pad-180.png")),
      "the icon it points at exists")

import json
json.load(open(os.path.join(ROOT, "web", "manifest.webmanifest")))
print("  ok   the manifest parses")

print(("FAILED: %d" % len(fails)) if fails else "test_viewport: all ok")
sys.exit(1 if fails else 0)
