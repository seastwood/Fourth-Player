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
import ctypes
import logging
import struct
import sys
import time

log = logging.getLogger("fourthplayer.virtual")

try:
    # Linux, and the only case where these are the real thing.
    from evdev import UInput as _KernelUInput, AbsInfo   # noqa: F401
    BACKEND = "uinput"

    class UInput(_KernelUInput):
        """evdev's UInput, plus the one call it has no equivalent for.

        A real DualShock reports its gyroscope on a *second* evdev device --
        "Wireless Controller Motion Sensors" -- rather than as extra axes on
        the pad, because the pad's axes are already spoken for. Making that
        second device is the Linux half of gyro and is not written yet, so
        this says so by answering False rather than by raising: pads.py can
        then hand motion to whatever device it has without asking which
        platform it is on, and a guest tilting a phone at a Linux host loses
        the tilt and keeps the pad.
        """

        def motion(self, values):
            return False

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
        # Only a pad is ever a DS4, and only _open_pad decides. Class-level so
        # the pointer, mouse and keyboard paths -- which never call it -- can
        # still be asked without raising.
        _ds4 = False

        def __init__(self, capabilities, name="", vendor=0, product=0,
                     version=0, bustype=0, **_ignored):
            from .codes import ecodes as e
            self.name = name
            self._caps = capabilities or {}
            keys = set(self._caps.get(e.EV_KEY, []) or [])
            axes = {code for code, _info in (self._caps.get(e.EV_ABS, []) or [])}

            # Which kind of device this is, asked of what it declares rather
            # than of a flag nobody would remember to pass. A pad has sticks
            # and face buttons; a pointer has an absolute position and no face
            # buttons; a mouse has relative movement; a keyboard has none of
            # it. The same four things desk.py and pads.py ask for.
            self._impl = None
            if e.ABS_X in axes and e.BTN_A in keys:
                self._open_pad(vendor)
                return
            # Imported here rather than at the top: windesk pulls in
            # ctypes.wintypes, which does not exist off Windows, and this
            # module has to import everywhere.
            from . import windesk
            if e.ABS_X in axes:
                # The span the caller declared for its absolute axes, so the
                # rescale to Windows' 0..65535 uses the protocol's own range
                # rather than a number copied here that could drift from it.
                span = 0
                for code, info in (self._caps.get(e.EV_ABS) or ()):
                    if code == e.ABS_X:
                        span = getattr(info, "max", 0) or 0
                self._impl = windesk.Pointer(name, span)
            elif self._caps.get(e.EV_REL):
                self._impl = windesk.Mouse(name)
            else:
                self._impl = windesk.Keyboard(name)
            self.device = self._impl.device

        def _open_pad(self, vendor=0):
            """Open the ViGEm target this pad declared itself to be.

            The kind is read from the declared vendor rather than passed as an
            argument, because evdev's UInput -- which this stands in for -- has
            a fixed signature with nowhere to put one. pads.py declares Sony's
            vendor id for a DualShock and Microsoft's for an Xbox pad, SDL and
            Windows key off exactly those two numbers, and so does this.
            """
            try:
                import vgamepad
            except ImportError as exc:      # pragma: no cover - install-time
                raise RuntimeError(
                    "vgamepad is needed to make a controller on Windows "
                    "(py -m pip install vgamepad, which installs the ViGEmBus "
                    "driver)") from exc
            self._vg = vgamepad
            self._impl = None
            self._ds4 = vendor == 0x054C
            self._pad = (vgamepad.VDS4Gamepad() if self._ds4
                         else vgamepad.VX360Gamepad())
            UInput._made += 1
            self.device = _Node("vigem:%s:%d"
                                % ("ds4" if self._ds4 else "x360", UInput._made))
            self._buttons = self._button_map()
            self._hat = {}               # ABS_HAT0X/Y -> -1, 0, 1
            # Both sticks are two-dimensional and arrive one axis at a time,
            # so each needs the other's last value to send a position at all.
            # Centred, which is 0 on an Xbox pad and 128 on a DS4. Starting a
            # DS4 at 0 is both sticks held hard up and left until the guest
            # touches them.
            self._lx = self._ly = self._rx = self._ry = 128 if self._ds4 else 0
            # None until a guest sends any, because a pad held still reads as
            # no rotation and one gravity -- "no sensor" has to be tellable
            # from "not moving", or this would start claiming an attitude
            # nobody reported.
            self._motion = None
            self._motion_at = 0.0
            self._ticks = 0
            self._dirty = False

        def _button_map(self):
            """evdev button code -> the flag that means the same button."""
            from .codes import ecodes as e
            if self._ds4:
                # The face buttons are matched by *position*, not by letter.
                # Xbox's X is the western button and so is Sony's square;
                # Xbox's Y is the northern one and so is triangle. A guest
                # pressing the left-hand face button gets the left-hand face
                # button, which is what their thumb meant.
                D = self._vg.DS4_BUTTONS
                return {
                    e.BTN_A: D.DS4_BUTTON_CROSS,
                    e.BTN_B: D.DS4_BUTTON_CIRCLE,
                    e.BTN_X: D.DS4_BUTTON_SQUARE,
                    e.BTN_Y: D.DS4_BUTTON_TRIANGLE,
                    e.BTN_TL: D.DS4_BUTTON_SHOULDER_LEFT,
                    e.BTN_TR: D.DS4_BUTTON_SHOULDER_RIGHT,
                    e.BTN_TL2: D.DS4_BUTTON_TRIGGER_LEFT,
                    e.BTN_TR2: D.DS4_BUTTON_TRIGGER_RIGHT,
                    e.BTN_SELECT: D.DS4_BUTTON_SHARE,
                    e.BTN_START: D.DS4_BUTTON_OPTIONS,
                    e.BTN_THUMBL: D.DS4_BUTTON_THUMB_LEFT,
                    e.BTN_THUMBR: D.DS4_BUTTON_THUMB_RIGHT,
                }
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
            if self._impl is not None:
                self._impl.write(etype, code, value)
                return
            if etype == e.EV_KEY:
                if self._ds4 and code == e.BTN_MODE:
                    # The PS button is a "special" one on a DS4 -- it lives in
                    # a different byte of the report and vgamepad gives it its
                    # own call. pads.py only ever writes it when the session
                    # allows the guide button at all.
                    special = self._vg.DS4_SPECIAL_BUTTONS.DS4_SPECIAL_BUTTON_PS
                    if value:
                        self._pad.press_special_button(special_button=special)
                    else:
                        self._pad.release_special_button(special_button=special)
                    self._dirty = True
                    return
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

        @staticmethod
        def _to_byte(value):
            """An evdev stick reading as a DS4 byte: 0..255, 128 in the middle.

            No inversion, unlike the Xbox path. evdev counts down as positive
            on a stick and so does a DualShock -- 0 is up and 255 is down --
            so the flip that XInput needs would be wrong here, and wrong in the
            way that is hardest to notice from a log: the picture is fine, the
            game responds, and up is down.
            """
            return max(0, min(255, (int(value) + 32768) >> 8))

        def _axis(self, code, value):
            from .codes import ecodes as e
            if self._ds4:
                self._ds4_axis(code, value)
                return
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

        def _ds4_axis(self, code, value):
            from .codes import ecodes as e
            pad = self._pad
            if code == e.ABS_X:
                self._lx = self._to_byte(value)
                pad.left_joystick(x_value=self._lx, y_value=self._ly)
            elif code == e.ABS_Y:
                self._ly = self._to_byte(value)
                pad.left_joystick(x_value=self._lx, y_value=self._ly)
            elif code == e.ABS_RX:
                self._rx = self._to_byte(value)
                pad.right_joystick(x_value=self._rx, y_value=self._ry)
            elif code == e.ABS_RY:
                self._ry = self._to_byte(value)
                pad.right_joystick(x_value=self._rx, y_value=self._ry)
            elif code in (e.ABS_Z, e.ABS_RZ):
                amount = max(0, min(255, value))
                left = code == e.ABS_Z
                if left:
                    pad.left_trigger(value=amount)
                else:
                    pad.right_trigger(value=amount)
                # A real DS4 sets the digital trigger button as well as the
                # analog value, and games read either. Sending only the axis
                # is a trigger that reads as untouched to anything watching
                # the buttons.
                D = self._vg.DS4_BUTTONS
                flag = (D.DS4_BUTTON_TRIGGER_LEFT if left
                        else D.DS4_BUTTON_TRIGGER_RIGHT)
                if amount:
                    pad.press_button(button=flag)
                else:
                    pad.release_button(button=flag)
            elif code in (e.ABS_HAT0X, e.ABS_HAT0Y):
                self._hat[code] = value
                self._press_hat()

        def _press_hat(self):
            """The d-pad, which is two signed axes here and four buttons there."""
            from .codes import ecodes as e
            x = self._hat.get(e.ABS_HAT0X, 0)
            y = self._hat.get(e.ABS_HAT0Y, 0)
            if self._ds4:
                # One of eight directions and a neutral, not four independent
                # flags: that is how the DS4 report carries it, and the
                # diagonals have to be named rather than implied.
                D = self._vg.DS4_DPAD_DIRECTIONS
                where = {
                    (0, 0): D.DS4_BUTTON_DPAD_NONE,
                    (0, -1): D.DS4_BUTTON_DPAD_NORTH,
                    (1, -1): D.DS4_BUTTON_DPAD_NORTHEAST,
                    (1, 0): D.DS4_BUTTON_DPAD_EAST,
                    (1, 1): D.DS4_BUTTON_DPAD_SOUTHEAST,
                    (0, 1): D.DS4_BUTTON_DPAD_SOUTH,
                    (-1, 1): D.DS4_BUTTON_DPAD_SOUTHWEST,
                    (-1, 0): D.DS4_BUTTON_DPAD_WEST,
                    (-1, -1): D.DS4_BUTTON_DPAD_NORTHWEST,
                }[(max(-1, min(1, x)), max(-1, min(1, y)))]
                self._pad.directional_pad(direction=where)
                return
            B = self._vg.XUSB_BUTTON
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

        # Turning the wire's units into the ones a DualShock reports.
        #
        # The wire carries sixteenths of a degree per second, and a DS4's
        # gyroscope reads very close to sixteen counts per degree per second --
        # so the rotation conversion is one to one. That is a coincidence and
        # not a design, and it is named here rather than left as an absent
        # multiply, because the next person to read this will want to know
        # whether the scaling was considered or forgotten.
        #
        # Acceleration is not so lucky: the wire is thousandths of gravity and
        # a DS4 reports about 8192 counts per gravity.
        #
        # Both figures are the commonly used ones rather than a calibration of
        # a particular controller, which varies part to part. They decide how
        # fast a game reads a given wrist movement, so anybody who wants it
        # quicker or slower wants a sensitivity setting -- correcting the feel
        # by mis-scaling the physics here would make the accelerometer lie
        # about which way is down.
        GYRO_SCALE = 1.0
        ACCEL_SCALE = 8.192

        # Which of the DualShock's three gyro words each of the wire's
        # rotations belongs in.
        #
        # The wire carries them in the *screen's* frame: pitch about the
        # screen's horizontal axis, yaw about its vertical one, roll about its
        # normal. A DualShock's are in its own body's frame -- and the two are
        # not held the same way. A controller lies face up in the hands; a
        # phone in portrait is held face toward you. That is ninety degrees
        # apart about the horizontal axis, so the phone's screen-normal
        # corresponds to the controller's vertical and its screen-vertical to
        # the controller's front-to-back.
        #
        # Which means pitch and roll trade places, and yaw does not move. Sent
        # straight through, the effect from the sofa was precise and
        # confusing: "rolling the phone left and right moves it up and down,
        # tilting left and right moves it left and right" -- the horizontal
        # right, the vertical driven by the wrong wrist.
        #
        # Determined by watching a game rather than from a datasheet, which is
        # the only honest way: the posture a pad is held in is not written
        # down anywhere. Indices into the wire's (pitch, yaw, roll).
        GYRO_ORDER = (2, 1, 0)
        # A DS4's report timestamp counts in units of about 5.33 microseconds
        # and wraps at 16 bits. Games that integrate rotation into an aim use
        # it as their clock, so a report with a frozen timestamp is a report
        # they may read as no time having passed.
        TICK_SECONDS = 5.33e-6

        def motion(self, values):
            """Six wire values: gyro x, y, z then accelerometer x, y, z.

            False where this device cannot carry them, which is every pad that
            is not a DualShock: an Xbox pad has no motion in its report at all,
            and there is nowhere honest to put it.
            """
            if not self._ds4 or not values or len(values) < 6:
                return False
            gyro = [int(max(-32768, min(32767, round(v * self.GYRO_SCALE))))
                    for v in values[:3]]
            accel = [int(max(-32768, min(32767, round(v * self.ACCEL_SCALE))))
                     for v in values[3:6]]
            if self._motion == (gyro, accel):
                return False
            self._motion = (gyro, accel)
            self._dirty = True
            return True

        # The DS4 report, laid out by hand because the struct cannot be trusted.
        #
        # ViGEm declares its report structs packed -- they describe bytes on a
        # wire, and a HID report has no padding in it. vgamepad's ctypes
        # translation leaves `_pack_` unset, so ctypes aligns them: DS4_REPORT
        # measures 10 bytes where its content is 9, and DS4_SUB_REPORT_EX gains
        # a phantom byte at offset 9 and another at 13.
        #
        # Writing through the named fields therefore lands every field after
        # the triggers one or two bytes late. Measured from the bytes actually
        # sent:
        #
        #   wTimestamp -> the battery level and gyro X's low byte
        #   wGyroX     -> the real gyro Y
        #   wGyroY     -> the real gyro Z
        #   wGyroZ     -> the real accelerometer X
        #
        # So the real gyro X -- pitch, which is up and down -- was being fed a
        # battery level of zero and a padding byte, for ever. Reported as
        # "only getting x axis gyro, nothing in the y axis", and it was:
        # rotation appeared on axes the game was not reading for vertical aim
        # and never on the one it was.
        #
        # Format: four stick bytes, the button word, the special byte, two
        # trigger bytes, the timestamp word, the battery byte, then three gyro
        # and three accelerometer words. "<" means no alignment, which is the
        # whole point.
        REPORT = struct.Struct("<BBBBHBBBHBhhhhhh")

        def _send_extended(self):
            """The whole DS4 report, motion included.

            vgamepad's ordinary update() sends the short report, which has no
            room for a gyroscope. The extended one does -- but its fields are
            at the wrong offsets, so the bytes are packed here instead. See
            REPORT above for what that cost before it was found.
            """
            import vgamepad.win.vigem_commons as commons
            report = commons.DS4_REPORT_EX()
            short = self._pad.report
            now = time.monotonic()
            if self._motion_at:
                self._ticks = (self._ticks
                               + int((now - self._motion_at) / self.TICK_SECONDS)) & 0xFFFF
            self._motion_at = now
            gyro, accel = self._motion
            packed = self.REPORT.pack(
                short.bThumbLX, short.bThumbLY, short.bThumbRX, short.bThumbRY,
                short.wButtons, short.bSpecial,
                short.bTriggerL, short.bTriggerR,
                self._ticks,
                # A real pad reports a charge. Zero reads as flat, and a game
                # or an overlay that shows it has no reason to be told this
                # pad is dying.
                0xFF,
                gyro[self.GYRO_ORDER[0]], gyro[self.GYRO_ORDER[1]],
                gyro[self.GYRO_ORDER[2]],
                # The accelerometer is left in the order it arrived. Nothing
                # visible reads it yet -- it says which way is down, which
                # matters to a game that draws a tilting object and to nothing
                # else here -- so permuting it to match would be a guess with
                # no way to check it. Worth revisiting the day something uses
                # it.
                accel[0], accel[1], accel[2])
            # Into the union's byte view, which is the same memory as the
            # struct and the only way to put these where the wire wants them.
            ctypes.memmove(report.ReportBuffer, packed, len(packed))
            # Said once, with the bytes, because everything above this point
            # can be right and the report still be wrong -- and from the game's
            # side a gyroscope that does nothing looks the same either way.
            # The offsets are the answer to "is the host sending it, or is the
            # game ignoring it", which is two different repairs.
            if not getattr(self, "_said_report", False):
                self._said_report = True
                try:
                    raw = bytes(bytearray(report.ReportBuffer)[:24])
                    # Read back out of the bytes, not out of the struct's
                    # fields: the fields are at the offsets that were wrong in
                    # the first place, so reporting through them would have
                    # agreed with the bug.
                    back = self.REPORT.unpack(raw)
                    log.info("DS4 extended report: gyro %d %d %d accel %d %d %d"
                             " stamp %d; 24 bytes %s",
                             back[10], back[11], back[12], back[13], back[14],
                             back[15], back[8], raw.hex(" "))
                except Exception:
                    log.info("DS4 extended report built, but its bytes could "
                             "not be read back", exc_info=True)
            self._pad.update_extended_report(report)

        def syn(self):
            """Send the frame. Nothing reaches the game until this."""
            if self._impl is not None:
                self._impl.syn()
                return
            if self._dirty:
                if self._motion is not None:
                    self._send_extended()
                else:
                    self._pad.update()
                self._dirty = False

        def close(self):
            if self._impl is not None:
                self._impl.close()
                return
            # Everything let go of before the device goes, so a guest leaving
            # cannot leave a button held down in somebody's game -- the same
            # care detach() takes on the Linux side.
            try:
                self._pad.reset()
                self._pad.update()
            except Exception:
                pass
            self._pad = None
