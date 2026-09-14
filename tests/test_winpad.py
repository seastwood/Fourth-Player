"""A guest's frame, through pads.py, onto ViGEm, and back out of XInput.

The whole Windows translation end to end, and the only test here that can say
whether it is right: PadState -> to_events -> virtual.UInput -> ViGEmBus ->
what a game would actually read. Everything in between is arithmetic that
looks correct either way round, and one of those ways puts a guest's thumb up
when they pushed down.

Runs only on Windows with ViGEmBus installed, and skips loudly anywhere else.
There is no way to fake this one: XInput is the thing being asked, and asking
a stub would prove only that the stub agrees with the code that wrote it.
"""
import ctypes
import os
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

if sys.platform != "win32":
    print("SKIPPED: not Windows, so there is no ViGEm or XInput to ask.")
    sys.exit(0)

try:
    import vgamepad                                            # noqa: F401
except Exception as exc:
    print("SKIPPED: vgamepad is not installed here (%s)." % exc)
    print("         py -m pip install vgamepad, which brings ViGEmBus.")
    sys.exit(0)

from fourthplayer import pads as padlib, protocol as P

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


class _PAD(ctypes.Structure):
    _fields_ = [("wButtons", ctypes.c_ushort), ("bLeftTrigger", ctypes.c_ubyte),
                ("bRightTrigger", ctypes.c_ubyte), ("sThumbLX", ctypes.c_short),
                ("sThumbLY", ctypes.c_short), ("sThumbRX", ctypes.c_short),
                ("sThumbRY", ctypes.c_short)]


class _STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_uint), ("Gamepad", _PAD)]


_xinput = None
for _name in ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"):
    try:
        _xinput = ctypes.WinDLL(_name)
        break
    except OSError:
        continue
if _xinput is None:
    print("SKIPPED: no XInput library on this machine, which is odd for Windows 11.")
    sys.exit(0)

XUSB_A, XUSB_DPAD_UP = 0x1000, 0x0001
SETTLE = 0.3          # ViGEm's devices arrive and update asynchronously


def read(slot=0):
    state = _STATE()
    if _xinput.XInputGetState(slot, ctypes.byref(state)) != 0:
        return None
    return state.Gamepad


def after(state, pad):
    pad.apply(state)
    time.sleep(SETTLE)
    return read(0)


print("a pad made the way a session makes one")
before = read(0)
if before is not None:
    print("SKIPPED: XInput slot 0 is already busy -- a real controller is")
    print("         plugged in, and this test would be reading that instead.")
    sys.exit(0)

pad = padlib.VirtualPad("Fourth Player 1", guide=False)
try:
    print("  path: %s" % pad.path)
    time.sleep(SETTLE * 2)
    check(read(0) is not None, "XInput sees it, in the first of its four slots")

    print("\nbuttons")
    got = after(P.PadState(seq=1, buttons=(1 << P.BTN_A)), pad)
    check(got is not None and bool(got.wButtons & XUSB_A),
          "A arrives as XUSB's A: 0x%04x" % (got.wButtons if got else 0))

    print("\nthe vertical sticks, which are the ones that can come out upside down")
    # evdev counts down as positive on a gamepad; XInput counts up. So 'up'
    # goes in negative and has to come out positive, and the clamp matters:
    # -32768 has no positive twin in an int16.
    got = after(P.PadState(seq=2, axes=[0, -32768, 0, 0, 0, 0]), pad)
    check(got is not None and got.sThumbLY > 30000,
          "pushed up, reads up: sThumbLY = %d" % (got.sThumbLY if got else 0))
    got = after(P.PadState(seq=3, axes=[0, 32767, 0, 0, 0, 0]), pad)
    check(got is not None and got.sThumbLY < -30000,
          "pushed down, reads down: sThumbLY = %d" % (got.sThumbLY if got else 0))
    got = after(P.PadState(seq=4, axes=[0, 0, 0, -32768, 0, 0]), pad)
    check(got is not None and got.sThumbRY > 30000,
          "and the right stick the same way: sThumbRY = %d"
          % (got.sThumbRY if got else 0))

    print("\ntriggers, which pads.py has already scaled to an Xbox pad's 0..255")
    got = after(P.PadState(seq=5, axes=[0, 0, 0, 0, 0, P.TRIGGER_MAX // 2]), pad)
    check(got is not None and 100 < got.bRightTrigger < 160,
          "half in, half out: %d of 255" % (got.bRightTrigger if got else -1))

    print("\nthe d-pad, which is two signed axes here and four buttons there")
    got = after(P.PadState(seq=6, buttons=(1 << P.BTN_UP)), pad)
    check(got is not None and bool(got.wButtons & XUSB_DPAD_UP),
          "a hat of -1 arrives as the up button: 0x%04x"
          % (got.wButtons if got else 0))

    print("\nand letting go")
    got = after(P.PadState(seq=7), pad)
    check(got is not None and got.wButtons == 0 and abs(got.sThumbLY) < 3000,
          "nothing stays held: buttons 0x%04x, stick %d"
          % (got.wButtons, got.sThumbLY))
finally:
    pad.close()

time.sleep(SETTLE * 2)
check(read(0) is None,
      "closing the pad takes the device away, rather than leaving a "
      "controller nobody is holding")

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
