"""Pinch, and the page that opens zoomed the next time.

"Sometimes the page opens zoomed in, the top buttons are big and the
controller hangs off the right side." That reads like a broken layout and is
not one: every zoom-equivalent width, portrait and landscape, lays out with no
overflow at all. What is happening is that the page is genuinely zoomed --
everything sized in rem is drawn larger, and the picture becomes a window onto
a page wider than the screen.

It gets zoomed because the stage said `touch-action: manipulation`, which only
turns off double-tap zoom. A two-finger touch still pinches. This is a game
held in two hands with thumbs on glass, so a stray pinch is not a rare
accident -- and Safari remembers the zoom for that site, which is why it comes
back on the next visit rather than at the moment of the accident.

The stage refuses gestures now. That is safe because it never scrolls, but
touch-action is intersected down the ancestor chain, so every panel that does
scroll has to ask for the gesture it needs -- and a panel that scrolls without
saying so is a panel that silently stops scrolling on a phone. That has
happened here before, in landscape, and it is the reason this test lists them
rather than trusting the change.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


css = open(os.path.join(ROOT, "web", "style.css")).read()
app = open(os.path.join(ROOT, "web", "app.js")).read()


def declarations(text):
    """A rule body with its comments taken out.

    The prose explains why `manipulation` was wrong, and searching the prose
    for the word found it and called the fix a failure.
    """
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.S)


def rule(selector):
    """One rule's declarations, by name."""
    found = re.search(r"(?m)^%s\s*\{(.*?)^\}" % re.escape(selector), css, re.S)
    return declarations(found.group(1)) if found else ""


print("the picture refuses a pinch")
stage = rule(".stage")
check("touch-action: none" in stage,
      "the stage takes no gestures at all, so two fingers on the game cannot "
      "zoom the page")
check("manipulation" not in stage,
      "and not `manipulation`, which reads like it refuses zooming and only "
      "refuses the double-tap kind")

print("\nand every panel that scrolls still says how")
# Read out of the stylesheet rather than listed here, so a scroller added
# later is caught rather than quietly left out.
scrollers = []
for match in re.finditer(r"(?m)^([.#][\w.-]+)\s*\{(.*?)^\}", css, re.S):
    name, body = match.group(1), declarations(match.group(2))
    if re.search(r"overflow(-[xy])?:\s*auto", body):
        scrollers.append((name, body))
check(len(scrollers) >= 4, "found %d scrolling panels" % len(scrollers))
for name, body in scrollers:
    said = re.search(r"touch-action:\s*([\w-]+)", body)
    check(bool(said), "%s says which gesture it needs" % name)
    if said:
        sideways = "overflow-x: auto" in body
        want = "pan-x" if sideways else "pan-y"
        check(said.group(1) in (want, "auto", "manipulation"),
              "%s asks for %s, matching the way it scrolls; got %s"
              % (name, want, said.group(1)))

print("\nand a page that is zoomed anyway says so")
check("visualViewport" in app and "vv.scale" in app,
      "the zoom level is read from the browser rather than guessed at")
note = app[app.index("function noteZoom("):]
note = note[:note.index("\nfunction ")]
check("report(" in note,
      "and reported to the host, because a zoomed page and a broken layout "
      "look identical in a description")
check("scale === 1" in note and "return" in note,
      "with 100% passed over in silence -- this is a diagnosis, not a stream")
check("scale === zoomTold" in note,
      "and said once per change, not once per frame of a pinch")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_pinch: all ok")
