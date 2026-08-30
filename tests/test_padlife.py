"""A seat nobody is sitting in costs nothing.

The pads used to be made when a session opened, one per slot, and destroyed
when it closed. A virtual pad that exists is a virtual pad the emulator gives
a player port to, so four empty seats sat on ports one to four from the moment
a session opened -- and a real controller plugged in afterwards was
autoconfigured into port five, which no game here uses. Measured, with a real
controller that the picker had given player one:

    Remote player 1..4 configured in ports 1..4    (nobody holding them)
    Xbox One S Controller configured in port 5     (the one that claimed P1)

So the devices follow the guests now, and the ports go to whoever is holding
something.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


try:
    import evdev
    from fourthplayer import pads as padlib
except Exception as exc:                       # noqa: BLE001
    print("SKIPPED: %s" % exc)
    sys.exit(0)

LABEL = "Padlife Test"


def present():
    """The devices this test has made, as the kernel sees them."""
    out = []
    for path in evdev.list_devices():
        try:
            name = evdev.InputDevice(path).name
        except OSError:
            continue
        if name.startswith(LABEL):
            out.append(name)
    return sorted(out)


try:
    pads = padlib.PadSet(4, label=LABEL)
except Exception as exc:                       # noqa: BLE001
    print("SKIPPED: cannot create virtual pads here: %s" % exc)
    sys.exit(0)

try:
    print("an open session with nobody in it plugs nothing in")
    time.sleep(0.5)
    check(present() == [], "no devices exist yet: %r" % present())
    check(len(pads) == 4, "but there are still four seats")
    check(pads.names == ["%s %d" % (LABEL, i) for i in (1, 2, 3, 4)],
          "each with the name it will have: %r" % pads.names)

    print("a name can be read without conjuring a controller")
    check(pads.name_for(2) == "%s 3" % LABEL, "the third seat is named")
    time.sleep(0.4)
    check(present() == [], "and reading it plugged nothing in: %r" % present())

    print("somebody sitting down gets a controller, and only theirs")
    pad = pads[1]
    time.sleep(0.8)
    check(present() == ["%s 2" % LABEL],
          "one device, for the seat they took: %r" % present())
    check(pad is pads[1], "and asking again is the same controller")

    print("and letting go unplugs it")
    check(pads.release(1) is True, "the seat is released")
    time.sleep(0.8)
    check(present() == [], "the device is gone: %r" % present())
    check(pads.release(1) is False, "releasing an empty seat does nothing")

    print("a second guest does not disturb the first")
    a, b = pads[0], pads[3]
    time.sleep(0.8)
    check(present() == ["%s 1" % LABEL, "%s 4" % LABEL],
          "two devices, for the two seats taken: %r" % present())
    pads.release(0)
    time.sleep(0.8)
    check(present() == ["%s 4" % LABEL],
          "one leaves, the other stays: %r" % present())

    print("the sweep only looks at controllers that exist")
    # A pad nobody has pressed is already released, so it is not swept. Only
    # one seat has a device at all here, and that is the point: the sweep must
    # not go looking at the three that are empty.
    check(pads.sweep(timeout=-1) == [],
          "nothing to release when nothing is being held")
    b.released = False                          # as if a guest were holding it
    opened = pads.sweep(timeout=-1)             # and had gone quiet
    check(opened == [b],
          "the one live pad is released and no empty seat is touched: %r"
          % opened)
finally:
    pads.close()
    time.sleep(0.6)

check(present() == [], "closing the session leaves nothing behind: %r" % present())

print()
if fails:
    print("FAILURES: %d" % len(fails))
    sys.exit(1)
print("test_padlife: all ok")
