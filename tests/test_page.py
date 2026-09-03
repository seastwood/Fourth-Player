"""Every element the page reaches for has to be in the page.

`el("name")` is document.getElementById, and a name that is not in index.html
comes back null. What that does depends on where it is: a guarded one does
nothing at all, and an unguarded one throws at load time and takes every
listener defined after it with it -- so a typo in one button's id can silently
cost the controller, the volume and the game list, which is a symptom nobody
would trace back to a button.

Nothing here runs the page. It reads both files as text, which is enough for
the one mistake this catches and is why it costs nothing to run.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(os.path.dirname(HERE), "web")

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


page = open(os.path.join(WEB, "index.html")).read()
script = open(os.path.join(WEB, "app.js")).read()

have = set(re.findall(r'\bid="([^"]+)"', page))
wanted = set(re.findall(r'\bel\("([^"]+)"\)', script))

print("the ids app.js asks for")
missing = sorted(wanted - have)
check(not missing,
      "every one of the %d is in index.html%s"
      % (len(wanted), "" if not missing else ": missing " + ", ".join(missing)))

# The other direction is not a fault -- the page holds plenty the script never
# names -- so it is reported rather than failed.
spare = sorted(have - wanted)
print("   (%d ids in the page that app.js never names, which is fine)"
      % len(spare))

print("and the reconnect button is wired to something that exists")
check("revive" in have, "index.html has the button")
check('el("revive")' in script, "app.js reaches for it")
check("reviveNow(" in script, "and it calls the one path back")

print(("FAILED: %d" % len(fails)) if fails else "test_page: all ok")
sys.exit(1 if fails else 0)
