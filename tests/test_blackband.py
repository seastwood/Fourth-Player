"""Where the letterbox black stops, on a real page.

The black either side of a 16:9 stream on an upright phone used to run the
whole height of the screen, behind the chips and behind the keyboard's
buttons, so the page was one black field with controls floating in it. It
stops at both now -- and the point of measuring rather than reading the
stylesheet is the other half of the rule: the picture itself must not have
been made any smaller to achieve it.
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
PORT = 8736

node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the page cannot be loaded.")
    sys.exit(0)

chrome = next((p for p in ("/usr/bin/google-chrome", "/usr/bin/chromium",
                           "/usr/bin/chromium-browser", "/snap/bin/chromium")
               if os.path.exists(p)), None)
if not chrome:
    print("SKIPPED: no Chrome here, so the page cannot be loaded.")
    sys.exit(0)

# puppeteer-core is not vendored. It is a test-time convenience and no part of
# what runs on the console, so its absence is a skip rather than a failure.
modules = os.path.join(HERE, "browser", "node_modules", "puppeteer-core")
if not os.path.isdir(modules):
    print("SKIPPED: puppeteer-core is not installed here "
          "(cd tests/browser && npm install puppeteer-core).")
    sys.exit(0)

server = subprocess.Popen([sys.executable, os.path.join(HERE, "browser", "serve.py"),
                           str(PORT)], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)
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
        [node, os.path.join(HERE, "browser", "blackband.mjs")],
        cwd=os.path.join(HERE, "browser"), text=True,
        env={**os.environ, "FP_CHROME": chrome,
             "FP_PAGE": "http://127.0.0.1:%d/index.html" % PORT})
finally:
    server.terminate()
    server.wait(timeout=5)

sys.exit(result.returncode)
