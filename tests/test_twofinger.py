"""Telling a pinch from a two-finger drag on the picture.

Reported from a phone: "when I try to scroll by keeping one finger stationary
while sliding the other it zooms. Same if I slide with two fingers. Currently
I have no way to scroll with a mobile touchscreen device."

Every two-finger move zoomed by the ratio of the finger gap and panned by the
middle, both at once, so there was no way to move a zoomed picture with two
fingers without also resizing it -- and holding one finger still while sliding
the other changes the gap by the whole distance travelled, which zoomed hard.

The gesture is now asked what it is, once there is enough of it to be sure,
and then believed until the fingers lift. Taking `d` as the distance the moving
finger travels: both apart is gap 2d and middle 0; one still is gap d and
middle d/2; both together is gap 0 and middle d. A bias of two puts the middle
case on the drag side, which is what was asked for, and leaves a real pinch
nowhere near the line. Needs node, not Chrome.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))

node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the gesture rule cannot be run.")
    sys.exit(0)

result = subprocess.run([node, os.path.join(HERE, "browser", "twofinger.mjs")],
                        cwd=os.path.join(HERE, "browser"), text=True)
sys.exit(result.returncode)
