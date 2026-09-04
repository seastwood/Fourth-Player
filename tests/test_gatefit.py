"""The join screen fits the screen, so the address bar has nothing to chase.

"Sometimes the PIN page will aggressively jitter when automatically logging
in."

The gate was min-height: 100dvh, so it grew when its contents did -- and
automatically rejoining is the state with the most in it: the link row, its
note and an error, all at once. Measured on a 393-wide phone: the document
came out 804 tall in a 660 viewport, 144 pixels over. On a shorter screen it
was 301 over.

A document taller than the window is exactly what a mobile address bar reacts
to. It hides to make room, the viewport grows, the layout crosses back under
the threshold, and it shows again -- and that oscillation is the jitter. It
only ever showed up while logging in automatically because that is when the
gate is tallest.

The gate is now exactly the visible height and anything taller scrolls inside
it, so the document never exceeds the window and there is nothing for the bar
to respond to. Measured again after the change: 0 over, at 660, 560 and 420.

This checks the two rules that make that true, because the measurement needs a
browser and the rules do not.
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


def rule(selector):
    found = re.search(r"(?m)^%s\s*\{(.*?)^\}" % re.escape(selector), css, re.S)
    body = found.group(1) if found else ""
    return re.sub(r"/\*.*?\*/", " ", body, flags=re.S)   # code, not prose


print("the gate is the size of the screen, not the size of its contents")
gate = rule(".gate")
check("height: var(--vv-height" in gate,
      "its height is the visible height, measured by the page")
check("100dvh" in gate,
      "with a dvh fallback for the moment before that is measured")
check(not re.search(r"min-height:\s*100(vh|dvh)", gate),
      "and nothing that lets it grow past the window: min-height is what made "
      "the document taller than the screen and set the address bar going")

print("\nand what does not fit scrolls inside it")
inner = rule(".gate-inner")
check("overflow-y: auto" in inner,
      "the column scrolls rather than pushing the page taller")
check("max-height: 100%" in inner,
      "and is bounded by the gate, or it would have nothing to scroll within")
check("margin: auto" in inner,
      "while still centred when it is short, which is the ordinary case")
check("touch-action: pan-y" in inner,
      "and it says which gesture it needs, like every other scroller here -- "
      "the stage above refuses gestures to keep pinch off the picture")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_gatefit: all ok")
