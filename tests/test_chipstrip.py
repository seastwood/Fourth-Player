"""The strip of chips across the top, upright.

It was painted in --bg with a rule along the bottom, which on a phone reads as
a solid bar across the top of the screen. Reported as "the black background
along the top where the top chip buttons go should be transparent". The chips
carry their own backgrounds -- .chip at 90% and .hudbtn at 55% -- so the strip
never needed one, and a 2px line separating a transparent strip from the
picture is a stray mark rather than a separator.

What has to stay true, and is checked on the rendered page rather than in the
stylesheet: the strip paints nothing, the whole row is still laid out, and
every chip still has something behind its own text.
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.realpath(__file__))
PORT = 8740

node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the page cannot be loaded.")
    sys.exit(0)

chrome = next((p for p in ("/usr/bin/google-chrome", "/usr/bin/chromium",
                           "/usr/bin/chromium-browser", "/snap/bin/chromium")
               if os.path.exists(p)), None)
if not chrome:
    print("SKIPPED: no Chrome here, so the strip cannot be drawn.")
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
        [node, os.path.join(HERE, "browser", "chipstrip.mjs")],
        cwd=os.path.join(HERE, "browser"), text=True,
        env={**os.environ, "FP_CHROME": chrome,
             "FP_PAGE": "http://127.0.0.1:%d/index.html" % PORT})
finally:
    server.terminate()
    server.wait(timeout=5)

sys.exit(result.returncode)
