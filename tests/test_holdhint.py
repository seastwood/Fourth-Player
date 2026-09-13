"""Press and hold, and the half second before it becomes a right click.

Two things at once, from a laptop.

"I can't click-hold-drag, the cursor just sticks there then does a right
click." Press-and-hold is how a surface with no buttons asks for the right one
-- the touchscreen's answer, where two fingers is the trackpad's -- and it was
armed for a mouse as well. A mouse has a right button already, so all it did
there was make dragging impossible: the button went down, nothing moved for
half a second, and a right click arrived instead. Armed for a finger only now,
decided once at the press rather than inside the timer.

"It would be handy if we had some sort of indicator for a press/click hold
right click that's about to happen, like a little loading bar above the
cursor." A bar at the pointer, swept over exactly HOLD_MS -- written in from
the constant rather than copied into the stylesheet, because a bar that
finishes before the click or after it teaches the wrong moment. It takes no
taps, since it appears under a finger that is already pressing, and it is
removed on every way the press can end: moving, letting go, or firing.

Positioned at cursorClientPoint, the same guess about where the console's
pointer is that the zoom aims at. Needs node, not Chrome.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))

node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the page's rules cannot be run.")
    sys.exit(0)

result = subprocess.run([node, os.path.join(HERE, "browser", "holdhint.mjs")],
                        cwd=os.path.join(HERE, "browser"), text=True)
sys.exit(result.returncode)
