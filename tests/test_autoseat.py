"""Seating a controller because somebody pressed a button on it.

The panel's "Add player" button is the explicit way to give a second
controller its own seat, and nobody should have to find it. A guest plugs in a
pad for the person next to them, they press A, and they are a player -- for the
second controller, the third, the fourth.

Presses rather than presence, which is the whole of the judgement here. The
comment on #pad-seats in index.html is right that a machine with three pads
plugged in is usually one person and two spares, and seating everything
connected would claim slots nobody asked for out of a pool shared with the
rest of the house. A pad nobody touches is a spare; a pad somebody presses is
a pad somebody is holding.

No browser and no host: the two functions are cut out of app.js and run
against stubs, because what is worth testing is the judgement rather than the
plumbing. Needs node, not Chrome.
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

result = subprocess.run([node, os.path.join(HERE, "browser", "autoseat.mjs")],
                        cwd=os.path.join(HERE, "browser"), text=True)
sys.exit(result.returncode)
