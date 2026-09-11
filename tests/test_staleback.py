"""The bar over the picture, in a real browser.

Here rather than at the host because none of it is a host question: whether a
tap on a button does what the button says, with the on-screen controller
underneath it, is answered by loading the page and tapping.

It exists because of a fault that no amount of reading would have found. The
page cancels the second of two quick taps, to stop iOS zooming; cancelling
touchend is also what stops a click being made at all, so every button on this
bar did nothing whatsoever from the second tap onwards. A control that
misbehaves is noticed. One that silently does nothing is reported as "I cannot
click them", which is exactly how it was reported.
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
        [node, os.path.join(HERE, "browser", "staleback.mjs")],
        cwd=os.path.join(HERE, "browser"), text=True,
        env={**os.environ, "FP_CHROME": chrome,
             "FP_PAGE": "http://127.0.0.1:%d/index.html" % PORT})
finally:
    server.terminate()
    server.wait(timeout=5)

sys.exit(result.returncode)
