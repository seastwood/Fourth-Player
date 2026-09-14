"""Whether this Windows machine can give a guest a controller.

The other half of the spike, and the one with no obvious answer. Linux has
`/dev/uinput`: any user with write access can create a gamepad that the kernel
and every game treat as real. Windows has nothing equivalent in userspace. The
realistic route is ViGEmBus -- a signed kernel-mode driver that emulates Xbox
360 and DualShock 4 pads, the same one DS4Windows and friends use -- driven
from Python through the `vgamepad` package.

What matters here is not that the Python call succeeds. It is that *a game*
sees a controller, which is a different claim: XInput has a hard limit of four
pads, ViGEm's devices arrive and leave asynchronously, and a game that already
bound its ports will ignore one that appears later. All three of those are
facts Fourth Player's design leans on being true.

    py -m pip install vgamepad          # brings the ViGEmBus installer
    py check_pad.py                     # one pad, wiggled
    py check_pad.py --pads 4 --seconds 30

Then, while it runs, open `joy.cpl` (Set up USB game controllers) and watch.
For the real answer, open a game and see whether it binds them.
"""
import argparse
import ctypes
import sys
import time


class _PAD(ctypes.Structure):
    _fields_ = [("wButtons", ctypes.c_ushort), ("bLeftTrigger", ctypes.c_ubyte),
                ("bRightTrigger", ctypes.c_ubyte), ("sThumbLX", ctypes.c_short),
                ("sThumbLY", ctypes.c_short), ("sThumbRX", ctypes.c_short),
                ("sThumbRY", ctypes.c_short)]


class _STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_uint), ("Gamepad", _PAD)]


def xinput_slots():
    """Which of XInput's four slots report a controller, or None if no XInput.

    This is the number that matters and it is not the number of devices.
    ViGEm will happily create a sixth pad; XInput has four slots and most
    Windows games use XInput, so the sixth is a device nothing will ever read.
    """
    for name in ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"):
        try:
            lib = ctypes.WinDLL(name)
        except OSError:
            continue
        found = []
        for i in range(4):
            state = _STATE()
            if lib.XInputGetState(i, ctypes.byref(state)) == 0:
                found.append(i)
        return found
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pads", type=int, default=1,
                    help="how many virtual controllers to make at once")
    ap.add_argument("--seconds", type=float, default=20,
                    help="how long to keep them alive and moving")
    args = ap.parse_args()

    try:
        import vgamepad as vg
    except Exception as exc:
        print("vgamepad will not import: %s" % exc)
        print("\n  py -m pip install vgamepad\n"
              "  It prompts to install the ViGEmBus driver, which needs\n"
              "  administrator rights. That prompt is the point: a kernel\n"
              "  driver is what Windows has instead of uinput, and any\n"
              "  installer this project ships will have to carry it.")
        return 1

    before = xinput_slots()
    if before is None:
        print("no XInput library here, which is odd on Windows 11")
    else:
        print("XInput slots busy before we start: %s" % (before or "none"))
        if before:
            print("  (a real controller is plugged in, and it is using one of\n"
                  "   the same four slots a guest would need)")

    print("\nmaking %d pad(s)" % args.pads)
    pads = []
    try:
        for i in range(args.pads):
            # Xbox 360 rather than DS4: XInput is what Windows games expect,
            # and it is the layout the browser's standard mapping already
            # matches, so the button order needs no translating.
            pad = vg.VX360Gamepad()
            pads.append(pad)
            print("  pad %d created" % (i + 1))
    except Exception as exc:
        print("  failed after %d: %s" % (len(pads), exc))
        print("\n  If this failed at the fifth, that is XInput's limit of four\n"
              "  and a real constraint on how many guests a Windows host can\n"
              "  seat -- worth knowing now rather than later.")
        if not pads:
            return 1

    time.sleep(0.5)
    slots = xinput_slots()
    if slots is not None:
        print("\nXInput now reports %d slot(s) in use: %s" % (len(slots), slots))
        if len(pads) > len(slots):
            print("  %d device(s) made, %d visible. XInput's ceiling is four,\n"
                  "  and it is the ceiling on guests this host can seat --\n"
                  "  shared with every controller physically plugged in."
                  % (len(pads), len(slots)))

    print("\nwiggling them for %.0fs -- open joy.cpl and watch" % args.seconds)
    print("(each pad walks its face buttons and sweeps the left stick)")
    buttons = [vg.XUSB_BUTTON.XUSB_GAMEPAD_A, vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
               vg.XUSB_BUTTON.XUSB_GAMEPAD_X, vg.XUSB_BUTTON.XUSB_GAMEPAD_Y]
    started = time.monotonic()
    step = 0
    try:
        while time.monotonic() - started < args.seconds:
            for index, pad in enumerate(pads):
                pad.reset()
                pad.press_button(button=buttons[(step + index) % len(buttons)])
                # A slow circle, so a stick that is being read looks obviously
                # driven rather than noisy.
                angle = (step % 40) / 40.0 * 6.28318
                import math
                pad.left_joystick_float(x_value_float=math.cos(angle),
                                        y_value_float=math.sin(angle))
                pad.update()
            step += 1
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\nstopped early")
    finally:
        for pad in pads:
            try:
                pad.reset()
                pad.update()
            except Exception:
                pass

    print("\nheld %d pad(s) for %.0fs without falling over."
          % (len(pads), time.monotonic() - started))
    print("\nWhat this has and has not shown:")
    print("  shown     -- the driver installs, pads can be created and driven")
    print("  not shown -- that a *game* binds them. Open one and check, and")
    print("               check what happens to a game that was already")
    print("               running when the pad appeared. Fourth Player's")
    print("               whole seating model depends on the answer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
