"""A controller is called one thing, everywhere.

"We have a controller 0 that we created, but I can only select down to
controller 1."

Both were true and they were the same controller. Seats are an array and count
from zero; every name a person sees counts from one -- the picker, the panel,
RetroArch's profiles, the roster. The logs printed both at once:

    plugged in Fourth Player 1 (seat 0)

which reads as two controllers, one of which the page refuses to offer. There
is no sensible other way to read it.

So nothing user-facing says an index any more. The name already carries a
number and it is the one everything else uses.
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


sys.path.insert(0, REPO)
from fourthplayer import pads                                 # noqa: E402

print("-- the name counts from one, because people do --")
seats = pads.PadSet.__new__(pads.PadSet)
seats.names = ["Fourth Player %d" % (i + 1) for i in range(4)]
check(seats.name_for(0) == "Fourth Player 1",
      "the first seat is called 1, not 0: %r" % seats.name_for(0))
check(seats.name_for(3) == "Fourth Player 4", "and the last is 4")

print("\n-- and nothing tells anybody about the index --")
# Read from the source, because these are log lines: there is no return value
# to assert, and the fault was entirely in what they said.
for name in ("pads.py", "session.py"):
    body = open(os.path.join(REPO, "fourthplayer", name), encoding="utf-8").read()
    # Every logged message, with the arguments that follow it.
    for call in re.findall(r"log\.(?:info|warning|error)\(([^;]*?)\)\n", body):
        flat = re.sub(r"\s+", " ", call)
        if "seat %d" in flat or "pad %d" in flat:
            check(False, "%s still logs a raw index: %s" % (name, flat[:90]))
check(not fails, "no log line in pads.py or session.py prints a seat index")

print("\n-- the places that do say a number add one first --")
# Two of them legitimately name a controller by number, to somebody choosing
# one. Both count from one, which is the whole point.
body = open(os.path.join(REPO, "fourthplayer", "session.py"),
            encoding="utf-8").read()
check("there is no controller %d\" % (index + 1)" in body,
      "refusing a controller that does not exist names it the way it was asked "
      "for")
cli = open(os.path.join(REPO, "fourthplayer", "cli.py"), encoding="utf-8").read()
check('"controller %d is player %s" % (int(i) + 1' in cli,
      "and the command line lists them from one")

print()
if fails:
    print("FAILURES: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_padnames: all ok")
