"""Backspace from a phone keyboard, in the web UI's keyboard and cursor.

Reported from iOS: backspace does nothing. It was dead on every phone, and
only on phones.

The capture field was deliberately left empty after each keystroke, because
text surviving in it becomes context for the next autocorrect. But an empty
field is the one state in which Safari raises no `beforeinput` at all for a
deletion -- there is nothing to delete, so no event exists, so there was
nothing to map to a Backspace. A desktop never saw it because the global
keydown listener catches Backspace there, and a phone keyboard produces no
keydown worth reading.

So the field now always holds exactly one zero-width space with the caret
after it. The caret matters as much as the character: a sentinel with the
caret in front of it has nothing behind the cursor, which is the same dead key
by a subtler route. Nothing read out of the field ever contains it. Needs
node, not Chrome.
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
    [node, os.path.join(HERE, "browser", "deskbackspace.mjs")],
    cwd=os.path.join(HERE, "browser"), text=True)
sys.exit(result.returncode)
