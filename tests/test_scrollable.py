"""That the panels inside the stage can be scrolled with a finger.

`touch-action` is intersected down the ancestor chain, so a descendant can
never re-enable a gesture an ancestor has forbidden. `.stage` said `none`,
which met `pan-y` on .tab-panel, .shelf and .chat-log and won -- so not one
panel inside the stage could be scrolled by touch, and the comment in the
stylesheet claimed the opposite was true. Reported as "currently I have no way
to scroll with a mobile touchscreen device".

Checked two ways, because a rule-level check is exactly what missed a broken
page once already: the effective value is computed down the live DOM chain,
and then a drag is dispatched and the scroll position looked at.

The second half is *not* a test of touch-action, and must not be read as one:
touches synthesised through CDP do not take the compositor path that enforces
it. Measured -- with `.stage` set back to `none` the chain check fails and the
drag still scrolls. So one says "a finger will be allowed to scroll here" and
the other says "there is something here that scrolls"; neither covers the
other.
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.realpath(__file__))
PORT = 8735

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
        [node, os.path.join(HERE, "browser", "scrollable.mjs")],
        cwd=os.path.join(HERE, "browser"), text=True,
        env={**os.environ, "FP_CHROME": chrome,
             "FP_PAGE": "http://127.0.0.1:%d/index.html" % PORT})
finally:
    server.terminate()
    server.wait(timeout=5)

sys.exit(result.returncode)
