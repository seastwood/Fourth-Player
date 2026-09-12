"""A second controller that dropped, letting itself back in.

Reported as: the seat says "disconnected" and the only way back is Remove and
then Add player. Both halves of that were bad. The seat sat in the list for
ever once its socket or pad channel went, because nothing ever retried it --
the page's own seat has had reconnectSoon all along and the second, third and
fourth had nothing. And Remove *declines* the pad, so the obvious next move,
pressing a button on it, did nothing either: somebody holding a working
controller had to go and operate a menu with it, which is the exact thing
auto-seating exists to spare them.

So a dropped seat now comes back on a capped backoff, and one that will not
come back leaves the list rather than staying as a dead row -- not declined,
because nobody pressed anything, so the next press seats it again through the
ordinary path. Needs node, not Chrome.
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

result = subprocess.run(
    [node, os.path.join(HERE, "browser", "extrarecover.mjs")],
    cwd=os.path.join(HERE, "browser"), text=True)
sys.exit(result.returncode)
