"""Virtual input devices, whichever kernel is underneath.

`pads.py` and `desk.py` create devices and write events at them. What they
need of the thing underneath is remarkably small -- a constructor, `write`,
`syn`, `close`, and a path to put in a log line -- and that smallness is what
makes a second platform possible without disturbing either of them.

On Linux this is evdev's `UInput`, unchanged and unwrapped.

On Windows there is no uinput: nothing in userspace can create an input
device, and the answer is ViGEmBus, a signed kernel driver that emulates an
Xbox 360 pad. So the class below takes the same calls and the same event
codes -- see codes.py for why the vocabulary stays evdev's everywhere -- and
translates them at this one edge. Everything above it is unchanged and does
not know which platform it is on.

That the translation is possible at all is luck worth naming: pads.py already
declares its device as an Xbox 360 pad, vendor 0x045E product 0x028E, because
that is what SDL recognises. ViGEm emulates exactly that pad. The two ends
were already speaking about the same hardware.

**XInput has four slots and no more.** ViGEm will create a fifth and a sixth
device happily and XInput reports four, so a Windows host can seat four guests
at most -- and every controller physically plugged into it takes one of the
same four. Measured; see tools/winspike/README.md. Linux has no such ceiling.
"""
import sys

try:
    # Linux, and the only case where these are the real thing.
    from evdev import UInput, AbsInfo             # noqa: F401
    BACKEND = "uinput"

except ImportError:
    BACKEND = "vigem"
    import collections

    # evdev's AbsInfo is a namedtuple of exactly these fields, and the callers
    # build it by keyword, so this stands in for it without them noticing.
    AbsInfo = collections.namedtuple(
        "AbsInfo", "value min max fuzz flat resolution")

    # What this pad's axes mean once they reach XInput. Two differences from
    # evdev are worth stating rather than leaving in the arithmetic: the
    # vertical sticks are inverted, because evdev counts down as positive on a
    # gamepad and XInput counts up as positive; and the triggers need no
    # scaling at all, because pads.py already rescales them to 0..255 for the
    # Xbox pad it is pretending to be, which is the range XInput wants.
    def _flip(value):
        """A vertical stick reading, the other way up.

        evdev counts down as positive on a gamepad stick and XInput counts up,
        so every vertical axis is negated crossing this edge. Clamped because
        the range is not symmetrical: -32768 has no positive twin in an int16,
        and negating it lands one past the top.
        """
        return max(-32768, min(32767, -value))

    class _Node:
        """Stands in for evdev's `device`, which is only ever asked its path."""

        def __init__(self, path):
            self.path = path

    class UInput:
        """An Xbox 360 pad on ViGEmBus, driven with evdev's calls.

        Deliberately buffered. evdev's UInput writes each event as it is given
        and `syn()` marks the end of a frame; ViGEm has no such notion and
        `update()` pushes the whole pad state at once. Writing straight
        through would therefore send a partial frame per button -- a guest
        pressing two buttons in one frame would land as two reports, and a
        stick moving diagonally as two moves. So writes accumulate and syn()
        is what sends, which is what the caller already means by it.
        """

        _made = 0

        def __init__(self, capabilities, name="", vendor=0, product=0,
                     version=0, bustype=0, **_ignored):
            from .codes import ecodes as e
            self.name = name
            self._caps = capabilities or {}
            keys = set(self._caps.get(e.EV_KEY, []) or [])
            axes = {code for code, _info in (self._caps.get(e.EV_ABS, []) or [])}

            # Which kind of device this is, asked of what it declares rather
            # than of a flag nobody would remember to pass. A pad has sticks
            # and face buttons; a keyboard has neither.
            if e.ABS_X in axes and e.BTN_A in keys:
                self._open_pad()
            else:
                # desk.py's keyboard, mouse and pointer. They need SendInput
                # rather than ViGEm and are not done yet -- but this module
                # still has to *import* on Windows, so the failure waits until
                # something actually asks for one.
                raise NotImplementedError(
                    "only gamepads are emulated on Windows so far; the "
                    "keyboard and mouse need SendInput and are not written "
                    "yet (device %r)" % (name,))

        def _open_pad(self):
            try:
                import vgamepad
            except ImportError as exc:      # pragma: no cover - install-time
                raise RuntimeError(
                    "vgamepad is needed to make a controller on Windows "
                    "(py -m pip install vgamepad, which installs the ViGEmBus "
                    "driver)") from exc
            self._vg = vgamepad
            self._pad = vgamepad.VX360Gamepad()
            UInput._made += 1
            self.device = _Node("vigem:x360:%d" % UInput._made)
            self._buttons = self._button_map()
            self._hat = {}               # ABS_HAT0X/Y -> -1, 0, 1
            # Both sticks are two-dimensional and arrive one axis at a time,
            # so each needs the other's last value to send a position at all.
            self._lx = self._ly = self._rx = self._ry = 0
            self._dirty = False

        def _button_map(self):
            """evdev button code -> the XUSB flag that means the same button."""
            from .codes import ecodes as e
            B = self._vg.XUSB_BUTTON
            return {
                e.BTN_A: B.XUSB_GAMEPAD_A,
                e.BTN_B: B.XUSB_GAMEPAD_B,
                e.BTN_X: B.XUSB_GAMEPAD_X,
                e.BTN_Y: B.XUSB_GAMEPAD_Y,
                e.BTN_TL: B.XUSB_GAMEPAD_LEFT_SHOULDER,
                e.BTN_TR: B.XUSB_GAMEPAD_RIGHT_SHOULDER,
                e.BTN_SELECT: B.XUSB_GAMEPAD_BACK,
                e.BTN_START: B.XUSB_GAMEPAD_START,
                e.BTN_THUMBL: B.XUSB_GAMEPAD_LEFT_THUMB,
                e.BTN_THUMBR: B.XUSB_GAMEPAD_RIGHT_THUMB,
                e.BTN_MODE: B.XUSB_GAMEPAD_GUIDE,
            }

        def write(self, etype, code, value):
            """One event, held until syn(). Unknown codes are dropped."""
            from .codes import ecodes as e
            if etype == e.EV_KEY:
                flag = self._buttons.get(code)
                if flag is None:
                    # BTN_TL2 and BTN_TR2 land here: an Xbox pad reports its
                    # triggers as axes and the digital pair carries nothing
                    # XInput has a place for. Dropping them loses nothing --
                    # the axis beside them says the same thing.
                    return
                if value:
                    self._pad.press_button(button=flag)
                else:
                    self._pad.release_button(button=flag)
                self._dirty = True
            elif etype == e.EV_ABS:
                self._axis(code, value)
                self._dirty = True

        def _axis(self, code, value):
            from .codes import ecodes as e
            pad = self._pad
            if code == e.ABS_X:
                self._lx = value
                pad.left_joystick(x_value=self._lx, y_value=self._ly)
            elif code == e.ABS_Y:
                self._ly = _flip(value)
                pad.left_joystick(x_value=self._lx, y_value=self._ly)
            elif code == e.ABS_RX:
                self._rx = value
                pad.right_joystick(x_value=self._rx, y_value=self._ry)
            elif code == e.ABS_RY:
                self._ry = _flip(value)
                pad.right_joystick(x_value=self._rx, y_value=self._ry)
            elif code == e.ABS_Z:
                pad.left_trigger(value=max(0, min(255, value)))
            elif code == e.ABS_RZ:
                pad.right_trigger(value=max(0, min(255, value)))
            elif code in (e.ABS_HAT0X, e.ABS_HAT0Y):
                self._hat[code] = value
                self._press_hat()

        def _press_hat(self):
            """The d-pad, which is two signed axes here and four buttons there."""
            from .codes import ecodes as e
            B = self._vg.XUSB_BUTTON
            x = self._hat.get(e.ABS_HAT0X, 0)
            y = self._hat.get(e.ABS_HAT0Y, 0)
            for flag, on in (
                (B.XUSB_GAMEPAD_DPAD_LEFT, x < 0),
                (B.XUSB_GAMEPAD_DPAD_RIGHT, x > 0),
                # Up is negative on the hat, as it is on the sticks.
                (B.XUSB_GAMEPAD_DPAD_UP, y < 0),
                (B.XUSB_GAMEPAD_DPAD_DOWN, y > 0),
            ):
                if on:
                    self._pad.press_button(button=flag)
                else:
                    self._pad.release_button(button=flag)

        def syn(self):
            """Send the frame. Nothing reaches the game until this."""
            if self._dirty:
                self._pad.update()
                self._dirty = False

        def close(self):
            # Everything let go of before the device goes, so a guest leaving
            # cannot leave a button held down in somebody's game -- the same
            # care detach() takes on the Linux side.
            try:
                self._pad.reset()
                self._pad.update()
            except Exception:
                pass
            self._pad = None
