"""Every way the host tells a page about an account says whether it is primary.

The primary admin holds every capability there is, including ones that did
not exist when their account was made. The host has always known that; the
page draws itself from what it is told, so if one of these payloads leaves it
out, that page believes the primary admin may do less than they may -- and
the symptom is a control that is simply not there, with nothing in any log.

That is not hypothetical. `desk` was added, the one account on the console
predated it, and three of the four places said `primary` while the fourth --
the account carried in the `joined` message, which is the one used by a page
rejoining with a remembered device, so the common path -- did not. The button
did not appear for the one person who can always use it.

Read out of the source rather than exercised, because the point is that
*every* such payload has it, including ones added later.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


source = open(os.path.join(ROOT, "fourthplayer", "server.py"),
              encoding="utf-8").read()

# Anything that hands a page a capability list is telling it about an account.
# Found by the "can" it carries rather than by a list of names kept here, so a
# fifth one added tomorrow is caught rather than quietly left out.
payloads = []
for match in re.finditer(r'"can":\s*list\(', source):
    start = source.rfind("{", 0, match.start())
    depth, end = 0, start
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    payloads.append(source[start:end])

print("every account payload the page is given")
check(len(payloads) >= 4,
      "found %d of them; there were four when this was written" % len(payloads))
for body in payloads:
    line = " ".join(body.split())[:72]
    check('"primary"' in body, "says whether it is primary: %s..." % line)

print("\nand the page believes it")
app = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
check("account.primary" in app,
      "may() lets the primary admin through without consulting a list")
check("primary: !!message.primary" in app,
      "and loggedIn keeps what it was told")

print("\nthe bar is not painted from inside a panel that closes")
# paintSession returns early on a closed panel. The desk bar sits over the
# picture, and the panel is shut whenever somebody is actually watching it.
paint = app.split("function paintAccount")[1].split("\n}")[0]
check("deskPaintKeys()" in paint,
      "paintAccount paints it, and paintAccount is not gated on the panel")

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
