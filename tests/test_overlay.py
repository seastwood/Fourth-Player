"""The picture fills the stage; the controls float on top of it.

Reported from a phone: "there's still a background behind the controller area
and the top chip button area ... what I would like is for the video to clearly
fill these spaces when zoomed in, the controller buttons would still overlay,
and the top chip buttons would still overlay, just without the background over
the video stream."

Landscape had always worked that way. Upright was a flex column instead: the
video took whatever was left after the pad, so the pad sat on the stage's own
black rather than on the game, and the chip row had a gradient scrim behind it
for good measure. Two different shapes for the same screen, and the upright one
put a band of furniture where the picture should have been.

The invariants here keep both orientations the same shape: the picture covers
the stage, the pad is pinned to the bottom over it so rows grow upwards and
none can be pushed off the edge, the pad's empty space lets taps through to the
picture underneath -- otherwise panning a zoomed picture dies across the lower
half of the screen -- and every cluster of controls still takes its own taps.
Needs node, not Chrome.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))

node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the stylesheet cannot be checked.")
    sys.exit(0)

result = subprocess.run([node, os.path.join(HERE, "browser", "overlay.mjs")],
                        cwd=os.path.join(HERE, "browser"), text=True)
sys.exit(result.returncode)
