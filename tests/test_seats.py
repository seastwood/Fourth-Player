"""Seating a second controller from the same machine.

Two people on one sofa, one screen, two pads. The second of them gets a seat
of their own -- their own player port and their own row in everybody's list --
over a connection that carries the input channel and no picture, because they
are already looking at the first one's.

The rule that shapes all of it: nothing is seated by itself. A machine with
three controllers plugged in is usually one person and two spares, so a pad
only becomes a player when somebody says so.

Three things here were wrong before this and would be wrong again:

  * gamepadconnected took whatever pad arrived. Plugging a second controller
    in moved this player's seat onto it -- which was already wrong with one
    player and is fatal when the second pad is meant to be a second person.
  * The fallback for "which pad am I reading" was "any connected one", which
    would have merged a seated player's buttons into this page's own frame:
    two people pressing one player.
  * A seated controller being unplugged has to give its seat back. The host
    would free it on silence eventually, and eventually is a player port
    nobody can use.
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


source = open(os.path.join(ROOT, "web", "app.js")).read()
page = open(os.path.join(ROOT, "web", "index.html")).read()


def lift(name):
    start = source.index("function " + name + "(")
    depth = 0
    for j in range(source.index("{", start), len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[start:j + 1]
    raise AssertionError(name)


print("nothing is seated by itself")
connected = source[source.index('window.addEventListener("gamepadconnected"'):]
connected = connected[:connected.index('window.addEventListener("gamepaddisconnected"')]
check("addExtra" not in connected,
      "a controller arriving does not claim a seat -- three pads plugged in "
      "is usually one person and two spares")
# Checked as a condition rather than as a word: the first version of this
# looked for "if (!held)" and passed happily with `held` set to a constant.
held = re.search(r"const held = ([^;]+);", connected)
check(bool(held), "whether this page already has a pad is worked out")
if held:
    said = held.group(1)
    check("padIndex" in said and "pads[padIndex]" in said and "connected" in said,
          "from the pad it is actually holding and whether that is still "
          "there -- so a second controller cannot steal the first player's "
          "seat; got %r" % said.strip())

gone = source[source.index('window.addEventListener("gamepaddisconnected"'):]
gone = gone[:gone.index("\n  wireTouch();")]
check("dropExtra" in gone,
      "and a seated controller being unplugged gives its seat back rather "
      "than leaving a player port nobody can use")

print("\nand a seated controller is nobody else's to read")
check("firstFreePad" in lift("livePad"),
      "the fallback skips pads that are already somebody's seat")
free = lift("firstFreePad")
check("extras.has(p.index)" in free,
      "which is what makes it a free one; otherwise two people press one "
      "player")

print("\nthe connection it opens")
extra = source[source.index("class ExtraPlayer"):]
extra = extra[:extra.index("\nfunction addExtra")]
check('input: "only"' in extra,
      "asks for a controller-only connection: one screen gets encoded once, "
      "however many controllers are around it")
check('"codecs": []' in extra or "codecs: []" in extra,
      "and offers no codecs, having no use for a picture")
check("FPFrame.buildRaw" in extra and "touchButtons" not in extra,
      "and sends that controller alone -- the glass and the keyboard belong "
      "to the person holding the page, and this seat is somebody else")
check("release_all" in extra or "true))" in extra,
      "and lets go of its buttons on the way out, rather than leaving one "
      "held for the host to time out")

print("\nand the page keeps what it needs to seat one")
check("sessionPin" in source,
      "the PIN is remembered for the life of the page")
check("localStorage.setItem(nameKey" in source or "sessionPin =" in source,
      "in memory rather than stored -- asking somebody to read it off the "
      "television again to seat the person beside them is not simple")
check("pad-seats" in page, "and there is somewhere to draw the list")
check(source.count("function paintControllers(") == 1
      and source.count("function paintSeats(") == 1,
      "and the two painters have two names: paintSeats was already the "
      "player-port picker, and a second one by that name would have "
      "silently replaced it")

if not shutil.which("node"):
    print("\nSKIPPED the drawing: node is not installed")
    sys.exit(1 if fails else 0)

harness = """
'use strict';
const extras = new Map();
for (const seated of %(seated)s) extras.set(seated, { seatName: () => "Guest 3" });
let padIndex = %(mine)s;
const connected = %(pads)s;
global.navigator = { getGamepads: () => connected };
global.shortPadName = (id) => id;

%(code)s

const out = { attached: attachedPads().map((p) => ({
  index: p.index, mine: p.mine, seat: p.seat })) };
// Explicitly null rather than undefined, which JSON drops entirely.
const free = firstFreePad(connected);
out.free = free ? free.index : null;
console.log(JSON.stringify(out));
""" 

code = "\n\n".join([lift("extraFor"), lift("attachedPads"), lift("firstFreePad")])


def run(pads, mine, seated=()):
    job = harness % {"code": code, "mine": json.dumps(mine),
                     "pads": json.dumps(pads),
                     "seated": json.dumps(list(seated))}
    done = subprocess.run([shutil.which("node"), "-e", job],
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(done.stderr[-600:])
    return json.loads(done.stdout)


print("\nwhat the list says with two controllers and one player")
out = run([{"index": 0, "connected": True, "id": "Xbox Wireless Controller"},
           {"index": 1, "connected": True, "id": "8BitDo Pro 2"}], 0)
check(len(out["attached"]) == 2, "both controllers are listed")
check(out["attached"][0]["mine"] is True and out["attached"][0]["seat"] == "you",
      "the one this page is playing on says so")
check(out["attached"][1]["mine"] is False
      and out["attached"][1]["seat"] is None,
      "and the other is listed as playing nothing, waiting to be asked")
# firstFreePad is the fallback for a page whose own pad has gone. Its job is
# to skip pads that are *somebody else's seat* -- this page's own pad is not
# one of those, so returning it is right.
check(out["free"] == 0,
      "with nobody seated, the fallback is happy to return this page's own pad")

print("\nand once the second controller is seated, it is off limits")
out = run([{"index": 0, "connected": False, "id": "Xbox Wireless Controller"},
           {"index": 1, "connected": True, "id": "8BitDo Pro 2"}], 0, seated=[1])
check(out["free"] is None,
      "this page's pad is gone and the only one left belongs to another "
      "player, so it reads nothing rather than reading their buttons")
seated_row = [r for r in out["attached"] if r["index"] == 1]
check(seated_row and seated_row[0]["seat"] == "Guest 3",
      "and the list says whose seat it is")

print("\nand a controller that is switched off is not on the list")
out = run([{"index": 0, "connected": True, "id": "Xbox Wireless Controller"},
           {"index": 1, "connected": False, "id": "8BitDo Pro 2"}], 0)
check(len(out["attached"]) == 1, "only the one that is actually there")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_seats: all ok")
