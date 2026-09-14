"""Buttons that latch: pressed once to hold down, pressed again to let go.

Asked for as "the ability for the client to make certain buttons toggle" -- a
run button held for a whole level, a trigger somebody cannot comfortably keep
down.

Entirely the page's business: the frame already says which buttons are down,
so a latched button is one this page keeps saying is down, and the host, the
protocol and the game are unchanged and cannot tell the difference. Which is
why all of this is here and none of it is on the host.

The edge semantics are what goes subtly wrong, so most of it is about them: a
latch moves on the rising edge only, and the state may be advanced exactly
once per frame -- the panel paints the same pad it sends. Needs node, not
Chrome.
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

result = subprocess.run([node, os.path.join(HERE, "browser", "padtoggle.mjs")],
                        cwd=os.path.join(HERE, "browser"), text=True)
sys.exit(result.returncode)
