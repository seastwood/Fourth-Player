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

The stage refuses the pinch, and only the pinch. It said `none` for a while,
which was wrong in a way this test used to enforce: touch-action is intersected
down the ancestor chain, and intersection only ever takes gestures away. A
descendant cannot re-enable what an ancestor forbade, so `none` on the stage
met `pan-y` on all eight panels below it and won -- not one of them could be
scrolled by a finger. It was reported as "I have no way to scroll with a mobile
touchscreen device", and the paragraph that used to be here had the rule
backwards.

`pan-y` is what the stage wants: it excludes pinch-zoom and double-tap zoom,
which is the whole of what this is for, while leaving the panels something to
intersect with. So there are two things to check and they pull in opposite
directions -- the stage must not permit a pinch, and it must not forbid
everything either. A panel that scrolls without saying which gesture it needs
is still a panel that silently stops scrolling, so those are listed too.
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
said = re.search(r"touch-action:\s*([\w -]+)", stage)
value = said.group(1).strip() if said else ""
check(bool(value), "the stage says what gestures it takes")
check("pinch-zoom" not in value and value not in ("auto", "manipulation"),
      "and refuses the pinch, so two fingers on the game cannot zoom the page; "
      "got %r" % value)
check("manipulation" not in stage,
      "and not `manipulation`, which reads like it refuses zooming and only "
      "refuses the double-tap kind")
# The other half of the same property, and the reason this test now asks for
# two things rather than one. `none` here is not a stricter version of the
# line above: it is what silently stopped every panel inside the stage from
# scrolling, because intersection down the chain only ever removes gestures.
check(value != "none",
      "but does not forbid panning outright, which would take scrolling away "
      "from every panel inside it -- a descendant cannot re-enable what an "
      "ancestor refused")

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
