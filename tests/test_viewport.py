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
import json
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
check("if (padsOpen) return;" in js,
      "nothing pressed while the panel is open reaches the game")
check("learnPress(" in js and "armed" in js,
      "and a button is learned once per press, not once per frame")
check("padIndex === null && !touchOn" in js,
      "closing the panel only brings back the prompt when there is no other "
      "way to play")
check("localStorage.setItem(mapKey()" in js,
      "a mapping is remembered per controller")

print("the page can be installed to escape the browser furniture")
check('name="apple-mobile-web-app-capable" content="yes"' in html,
      "added to the home screen, it opens without Safari's bars")
check('rel="manifest"' in html and 'rel="apple-touch-icon"' in html,
      "a manifest and a touch icon are linked")
# It used to have no start_url on purpose, so that an installed copy kept the
# invite it was added with -- which worked until the session it belonged to
# ended, and then never again. The plain address is the one that keeps working:
# the page remembers the key that got somebody in and asks for a new one only
# when the host has opened a new session.
manifest = json.load(open(os.path.join(ROOT, "web", "manifest.webmanifest")))
check(manifest.get("start_url") == "/",
      "an installed copy starts on the plain address, not on a dead invite")
check(os.path.isfile(os.path.join(ROOT, "web", "icons", "pad-180.png")),
      "the icon it points at exists")

json.load(open(os.path.join(ROOT, "web", "manifest.webmanifest")))
print("  ok   the manifest parses")

print("\nthe home-screen copy keeps clear of the clock and the battery")
css = open(os.path.join(ROOT, "web", "style.css")).read()
js = open(os.path.join(ROOT, "web", "app.js")).read()

# Added to the home screen the page is drawn under the status bar on purpose,
# and the safe-area inset only describes that gap on a phone with a notch. On
# one without it is zero, the status bar is still twenty points tall, and the
# chips at the top ended up behind the time and the battery.
check("--top-safe" in css, "there is one value for the top gap")
raw = [l for l in css.splitlines()
       if "safe-area-inset-top" in l and "--top-safe" not in l]
check(not raw, "and nothing measures the top for itself: %r" % raw)
check("max(env(safe-area-inset-top, 0px), 20px)" in css,
      "with a floor for the phones that report no inset")
check("(display-mode: standalone) and (orientation: portrait)" in css,
      "applied only where the status bar is actually shown")
check("navigator.standalone" in js,
      "and the page flags the home-screen copy itself as well")
check("html.standalone" in css, "by a class the stylesheet keys on")

# The bug the first attempt at this missed entirely. Upright, the chip strip
# is laid out in the flow rather than floated over the picture -- so `top`,
# which is what the floating version uses to clear the status bar, does
# nothing at all. Upright is also the only orientation in which iOS shows the
# status bar over a home-screen app. The strip has to carry the gap in its
# own padding.
rules = [b for b in re.findall(r"\.hud\s*\{([^}]*)\}", css)
         if "position: static" in b]
check(len(rules) == 1,
      "there is one rule that lays the strip out in the flow")
flow = rules[0]
check("padding-top" in flow and "--top-safe" in flow,
      "and it pads itself past the status bar: %r"
      % [l.strip() for l in flow.splitlines() if "padding" in l])

print("\nnothing that grows to fill a row does so without a limit")
# A search field or a slider given flex-grow and no ceiling takes every spare
# pixel, which on a desktop is most of the panel. Both were reported that way.
css = open(os.path.join(ROOT, "web", "style.css")).read()
for name, rule in (("the search field", r"\.browser-bar\.searching \.find \{([^}]*)\}"),
                   ("the stick sliders", r"\.tune \.pad-range \{([^}]*)\}")):
    block = re.search(rule, css)
    check(block is not None, "%s rule is present" % name)
    if block:
        body = block.group(1)
        check("flex: 1 1" in body, "%s grows to fill the row" % name)
        check("max-width" in body,
              "%s stops somewhere: %r" % (name, body.strip()[:70]))

print(("FAILED: %d" % len(fails)) if fails else "test_viewport: all ok")
sys.exit(1 if fails else 0)
