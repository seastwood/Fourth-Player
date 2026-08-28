"""Saying yes from the sofa, with a game already fullscreen on the television.

This is the only way the owner can answer a request without quitting whatever
they are playing: Kodi is behind the game and the overlay is click-through by
design. So the gesture has to work, has to be hard to make by accident, and
must not be makeable by the person asking.

Real uinput devices, because the whole question is which devices get watched
and what the kernel reports when buttons are held.
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
    from evdev import UInput, ecodes as e
except ImportError:
    print("SKIPPED: evdev is not installed, so no pad can be made to test with.")
    sys.exit(0)

from fourthplayer.approve import Shoulders
from fourthplayer import pads as padlib

try:
    theirs = UInput(padlib.capabilities(), name="Fourth Player 1",
                    vendor=padlib.VENDOR, product=padlib.PRODUCT)
    mine = UInput(padlib.capabilities(), name="Synthetic Test Pad",
                  vendor=padlib.VENDOR, product=padlib.PRODUCT)
except Exception as exc:                       # no /dev/uinput on this machine
    print("SKIPPED: could not open uinput (%s)" % exc)
    sys.exit(0)

time.sleep(0.4)                                # let udev name the nodes

try:
    print("it watches the controllers in the room, not the ones over the wire")
    reader = Shoulders()
    names = [d.name for d in reader.devices]
    check("Synthetic Test Pad" in names, "a pad in the room is watched")
    check(not any(n.startswith("Fourth Player") for n in names),
          "a guest's own pad is not: they must not approve their own request")

    def press(device, down):
        device.write(e.EV_KEY, e.BTN_TL, 1 if down else 0)
        device.write(e.EV_KEY, e.BTN_TR, 1 if down else 0)
        device.syn()
        time.sleep(0.15)

    print("one bumper is not the gesture")
    mine.write(e.EV_KEY, e.BTN_TL, 1)
    mine.syn()
    time.sleep(0.15)
    now = time.monotonic()
    check(reader.progress(now) == 0.0, "holding only L does nothing")
    mine.write(e.EV_KEY, e.BTN_TL, 0)
    mine.syn()
    time.sleep(0.15)
    reader.progress(time.monotonic())

    print("both, held, fills up over a second and a half")
    press(mine, True)
    start = time.monotonic()
    first = reader.progress(start)
    check(first == 0.0 and reader.holding,
          "the first instant of a hold reads as no progress but is a hold")
    check(0.0 < reader.progress(start + 0.2) < 1.0, "and then it counts up")
    check(reader.progress(start + 0.5) < 1.0, "half a second is not enough")
    check(reader.progress(start + Shoulders.HOLD_SECONDS + 0.01) >= 1.0,
          "a second and a half is")

    print("letting go abandons it")
    press(mine, False)
    check(reader.progress(time.monotonic()) == 0.0, "released, back to nothing")
    press(mine, True)
    restarted = reader.progress(time.monotonic())
    check(restarted < 0.5, "and the next hold starts from the beginning")
    press(mine, False)
    reader.progress(time.monotonic())

    print("a guest holding their own bumpers achieves nothing")
    theirs.write(e.EV_KEY, e.BTN_TL, 1)
    theirs.write(e.EV_KEY, e.BTN_TR, 1)
    theirs.syn()
    time.sleep(0.2)
    check(reader.progress(time.monotonic() + 10) == 0.0,
          "even held for ten seconds, on the pad of the guest who asked")
    print("a hold already under way when the request arrives does not count")
    # The overlay arms on the bumpers coming up, not on progress reading zero,
    # because at the first instant of any hold those look identical.
    press(mine, True)
    reader.forget()                            # what happens when a request lands
    reader.progress(time.monotonic())
    check(reader.holding,
          "still held, so the overlay will not arm and nothing is approved")
    press(mine, False)
    reader.progress(time.monotonic())
    check(not reader.holding, "letting go is what arms it")

finally:
    for device in (mine, theirs):
        try:
            device.close()
        except Exception:
            pass

print(("FAILED: %d" % len(fails)) if fails else "test_approve: all ok")
sys.exit(1 if fails else 0)
