"""Choosing which buttons latch, on the real page.

padtoggle.py covers the logic against the lifted source. This is the other
half: that the mode button is there, that clicking a cell in the grid while it
is on marks that button and writes it down, that the mark is actually drawn,
that a click in that mode does not start the learning walk instead, and that
"Use defaults" takes it all away again. Every one of those would pass a
rule-level check on a page nobody could use.
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.realpath(__file__))
PORT = 8747

node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the page cannot be loaded.")
    sys.exit(0)

chrome = next((p for p in ("/usr/bin/google-chrome", "/usr/bin/chromium",
                           "/usr/bin/chromium-browser", "/snap/bin/chromium")
               if os.path.exists(p)), None)
if not chrome:
    print("SKIPPED: no Chrome here, so nothing can be scrolled.")
    sys.exit(0)

modules = os.path.join(HERE, "browser", "node_modules", "puppeteer-core")
if not os.path.isdir(modules):
    print("SKIPPED: puppeteer-core is not installed here "
          "(cd tests/browser && npm install puppeteer-core).")
    sys.exit(0)

server = subprocess.Popen([sys.executable,
                           os.path.join(HERE, "browser", "serve.py"), str(PORT)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(50):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/mode" % PORT, timeout=0.2)
            break
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    else:
        print("SKIPPED: the test server would not start.")
        sys.exit(0)

    result = subprocess.run(
        [node, os.path.join(HERE, "browser", "toggleui.mjs")],
        cwd=os.path.join(HERE, "browser"), text=True,
        env={**os.environ, "FP_CHROME": chrome,
             "FP_PAGE": "http://127.0.0.1:%d/index.html" % PORT})
finally:
    server.terminate()
    server.wait(timeout=5)

sys.exit(result.returncode)
