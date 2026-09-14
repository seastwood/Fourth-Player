"""The input-event vocabulary, and that it means the same thing off Linux.

This package names buttons and keys the way evdev does -- KEY_A, BTN_SOUTH,
ABS_HAT0X -- everywhere, including on a machine with no evdev to ask. A second
vocabulary for Windows would mean every module that names a button knowing
which platform it is on, and two sets of names to keep in agreement for ever;
far better that the names are one thing and the backends translate at the edge.

That only works while the generated copy and the kernel agree. These are a
stable ABI so it should never drift -- but "should never" is exactly the kind
of claim worth a test, because the failure would be a guest pressing A and the
game seeing something else, on one platform only, with nothing to point at.

Where evdev is installed this compares the two directly. Where it is not, it
checks the copy is complete enough for the modules that read it.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


from fourthplayer import codes as codeslib
from fourthplayer._codes import CODES, KEY

print("the generated table")
check(len(CODES) > 400, "carries the whole vocabulary, not a hand-picked few: %d"
      % len(CODES))
check(len(KEY) > 300, "and the reverse map for naming a key: %d" % len(KEY))

print("\nthe names the code actually asks for")
# keymap.py builds these with getattr, so a missing one is an AttributeError
# at import rather than anything easy to read.
wanted = ([("KEY_" + c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
          + [("KEY_" + d) for d in "0123456789"]
          + [("KEY_F%d" % n) for n in range(1, 13)]
          + [("KEY_KP%d" % d) for d in range(10)]
          + ["EV_KEY", "EV_ABS", "EV_REL", "EV_SYN",
             "BTN_A", "BTN_B", "BTN_X", "BTN_Y", "BTN_MODE",
             "BTN_TL", "BTN_TR", "BTN_TL2", "BTN_TR2",
             "BTN_THUMBL", "BTN_THUMBR", "BTN_START", "BTN_SELECT",
             "BTN_LEFT", "BTN_RIGHT", "BTN_MIDDLE", "BTN_SIDE", "BTN_EXTRA",
             "ABS_X", "ABS_Y", "ABS_RX", "ABS_RY", "ABS_Z", "ABS_RZ",
             "ABS_HAT0X", "ABS_HAT0Y", "REL_X", "REL_Y",
             "REL_WHEEL", "REL_HWHEEL"])
missing = [n for n in wanted if n not in CODES]
check(not missing, "every name the modules reach for is here%s"
      % ("" if not missing else ": missing " + ", ".join(missing[:6])))

print("\nand the shim behaves like the module it stands in for")
e = codeslib.ecodes
check(getattr(e, "KEY_A") == 30, "a plain attribute: KEY_A = %d" % e.KEY_A)
check(getattr(e, "KEY_F7") == CODES["KEY_F7"], "one fetched by name, as keymap does")
check(e.KEY[30] == "KEY_A", "the reverse map: %r" % e.KEY[30])
try:
    e.KEY_DEFINITELY_NOT_A_KEY
    check(False, "a name that does not exist raises AttributeError")
except AttributeError:
    check(True, "a name that does not exist raises AttributeError, as evdev does")

print("\nagainst the kernel's own numbers")
try:
    from evdev import ecodes as real
except ImportError:
    # Deliberately not the word run.sh looks for: the rest of this suite did
    # run, and reporting the whole thing as skipped would understate it. This
    # one comparison is what is missing, and it says so.
    print("  ----   not compared: no evdev on this machine. Run on a Linux")
    print("         host to prove the copy matches the kernel's own numbers.")
else:
    wrong = [(n, v, getattr(real, n, None))
             for n, v in CODES.items() if getattr(real, n, None) != v]
    check(not wrong, "every constant matches evdev%s"
          % ("" if not wrong else ": %d differ, e.g. %r" % (len(wrong), wrong[0])))
    # evdev gives a list where two names share a code; the table keeps a tuple.
    def same(a, b):
        return (a if isinstance(a, str) else tuple(a)) == b
    wrongk = [c for c, n in KEY.items() if not same(real.KEY[c], n)]
    check(not wrongk, "and every key name matches%s"
          % ("" if not wrongk else ": %d differ" % len(wrongk)))
    check(codeslib.HAVE_EVDEV,
          "and where evdev exists it is used directly rather than the copy")

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
