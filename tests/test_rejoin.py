"""What a resume that goes unanswered is allowed to conclude.

A guest who is already in gets back after a dropped socket with their guest
token, and never sees the PIN again. That held until a resume was merely slow:
`armRejoinTimer` waited twenty seconds and then called askForPin, which threw
the token away -- the one thing that could have got them back, since
reconnectSoon does nothing without one. The host had refused nothing.

Reported on 2026-09-12 as being kicked out and asked for the PIN on pressing
"Repick player slots" at the television. That is exactly the shape: repicking
closes the game, closing it changes the screen, the screen changing re-offers
to every guest, and a resume landing inside that rebuild can outlast one
deadline on a busy host. Nothing in the host's log kicked anybody.

So silence is evidence about the network and a refusal is evidence about the
credential, and only the second one is allowed to destroy it. Needs node, not
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

result = subprocess.run([node, os.path.join(HERE, "browser", "rejoin.mjs")],
                        cwd=os.path.join(HERE, "browser"), text=True)
sys.exit(result.returncode)
