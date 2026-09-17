"""Drawing the page sideways when the browser will not turn. Needs node."""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))

node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the page's code cannot be run.")
    sys.exit(0)

result = subprocess.run([node, os.path.join(HERE, "browser", "turned.mjs")],
                        cwd=os.path.join(HERE, "browser"), text=True)
sys.exit(result.returncode)
