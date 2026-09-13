"""Where things actually are, upright, on a phone-shaped screen.

A stylesheet that asserts correctly can still lay out wrongly, and that is not
a hypothetical: an earlier attempt at this layout passed every rule-level check
and shipped broken. The pad carried two `position` declarations, the later one
won, so it stayed in flow -- and with the stage no longer a flex column it
floated to the top of the screen, over the chips, with the picture behind it.
Reported as "the controller now sits too high, I see no video stream, and only
two of the top chip buttons are visible".

Every check here is a sentence from that report turned round, and each is a
measurement rather than a property: the picture is as tall as the stage and
reaches its bottom, the controller is at the bottom and below the chip row, the
whole chip row is laid out, and the pad paints no panel behind itself.

Needs node, puppeteer-core and a real Chrome, so it skips almost everywhere --
but where it runs it is the only thing here that would have caught that.
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.realpath(__file__))
PORT = 8734

node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the page cannot be loaded.")
    sys.exit(0)

chrome = next((p for p in ("/usr/bin/google-chrome", "/usr/bin/chromium",
                           "/usr/bin/chromium-browser", "/snap/bin/chromium")
               if os.path.exists(p)), None)
if not chrome:
    print("SKIPPED: no Chrome here, so the page cannot be laid out.")
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
        [node, os.path.join(HERE, "browser", "portraitgeom.mjs")],
        cwd=os.path.join(HERE, "browser"), text=True,
        env={**os.environ, "FP_CHROME": chrome,
             "FP_PAGE": "http://127.0.0.1:%d/index.html" % PORT})
finally:
    server.terminate()
    server.wait(timeout=5)

sys.exit(result.returncode)
