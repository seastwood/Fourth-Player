"""Correcting a controller that is not this page's own.

"Fix my buttons" has always written its result under the pad's name, and the
extra seats have always ignored it: ExtraPlayer.send built its frame from the
raw pad. So the seats that are hardest to reach from the sofa were the only
ones whose buttons could not be corrected at all, and a guest whose second
controller reported its buttons in a strange order had no way to say so. The
panel could not help either -- everything in it spoke to whichever pad this
page had taken for itself.

Two halves, and this covers both: an extra seat now corrects its own pad by
its own name, borrowing nothing from any other controller, and the panel can
be pointed at any attached pad so the corrections can be taught in the first
place. Needs node, not Chrome.
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

result = subprocess.run([node, os.path.join(HERE, "browser", "padfix.mjs")],
                        cwd=os.path.join(HERE, "browser"), text=True)
sys.exit(result.returncode)
