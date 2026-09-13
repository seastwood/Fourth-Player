"""Scrolling a window on the host from a touchscreen.

Reported as: with the keyboard and mouse enabled from a phone, there is no way
to scroll in Files or the browser on the console. Nothing was broken -- the
feature had never existed. The host has always understood a wheel
({"t":"w"} becomes REL_WHEEL and REL_HWHEEL on the virtual mouse) and the page
only ever sent one from a real `wheel` event, which a touchscreen does not
raise. So from a phone the pointer could be moved, clicked and typed at, and
anything needing a scroll could not be scrolled.

Two fingers dragged on the glass now send notches, on the pointer path and on
the gesture path Safari uses -- and only while the keyboard and mouse are
held, so two fingers still move the picture the rest of the time.

The signs are the easy thing to get backwards, so they are checked end to end:
a wheel's own deltaY is positive scrolling down and deskWheeled subtracts it,
while a finger dragged up is a negative dy meaning the same thing, so this
adds. Finger up gives a negative REL_WHEEL, and the content moves up.
Needs node, not Chrome.
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

result = subprocess.run([node, os.path.join(HERE, "browser", "deskscroll.mjs")],
                        cwd=os.path.join(HERE, "browser"), text=True)
sys.exit(result.returncode)
