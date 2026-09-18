"""The keyboard and the mouse, on Windows, through SendInput.

Windows has no uinput, and unlike the gamepads there is no driver to install
either: SendInput posts into the same queue real hardware does, and needs no
privilege beyond running in the session it is typing into.

Which is also its limit, and worth stating before anything below is believed.
SendInput is *session-wide*, not a device: there is one keyboard focus and one
cursor, and everything sent here goes wherever that focus is. On Linux each
guest at the desk gets their own uinput keyboard and the X server merges them,
which is not obviously different from a user's point of view -- both end up
typing into the same window -- but it means anything that hoped to tell two
desks apart cannot, here.

Keys arrive as evdev codes, because that is the vocabulary this package speaks
everywhere (see codes.py). They leave as **scan codes** rather than virtual
keys, deliberately: a scan code is what the hardware would have sent, so it
survives whatever layout the host has, and games reading DirectInput see it.
Sending VK codes instead would be reinterpreted by the host's layout and a
guest on a US layout would type something else on a host set to German.

The translation is mostly free. Linux's key codes 1..88 were chosen to match
AT scan code set 1, so for that whole range the code *is* the scan code, which
covers the letters, the digits, the function keys, the modifiers and the
keypad. The rest are the ones a PC keyboard sends with an 0xE0 prefix, and
they are listed out because there is no rule to follow.
"""
import ctypes
from ctypes import wintypes

from .codes import ecodes as e

# --- what SendInput wants ---------------------------------------------------

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x0001, 0x0002
KEYEVENTF_SCANCODE = 0x0008
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE = 0x0001, 0x8000
# Do not let Windows merge our moves with each other.
#
# SendInput's relative motion is coalesced by default: several moves arriving
# within a tick become one, which is exactly wrong here. The whole reason the
# guest now sends each movement as it happens rather than one summary per
# animation frame is so the host sees the shape of the hand's motion, and
# having Windows put it back into per-tick jumps at the other end undoes that.
# It also matters for anything reading raw input, which sees the individual
# moves rather than the cursor.
MOUSEEVENTF_MOVE_NOCOALESCE = 0x2000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
WHEEL_DELTA = 120                      # one notch, as Windows counts them


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]


def _send(*events):
    if not events:
        return
    array = (_INPUT * len(events))(*events)
    ctypes.windll.user32.SendInput(len(events), array, ctypes.sizeof(_INPUT))


# --- evdev key code -> AT scan code set 1 -----------------------------------

# 1..88 need no table: Linux numbered them to match. Written as a loop rather
# than 88 lines partly for brevity and mostly because a typo in one of 88
# hand-copied pairs would be a single key that silently does the wrong thing.
SCAN = {code: code for code in range(1, 89)}
SCAN.update({
    e.KEY_RO: 0x73,            # the extra key on a Japanese layout
    e.KEY_YEN: 0x7D,
    e.KEY_KPEQUAL: 0x59,
})

# The ones a keyboard prefixes with 0xE0. There is no pattern here; this is
# the PC's own history.
SCAN_E0 = {
    e.KEY_KPENTER: 0x1C, e.KEY_RIGHTCTRL: 0x1D, e.KEY_KPSLASH: 0x35,
    e.KEY_SYSRQ: 0x37, e.KEY_RIGHTALT: 0x38,
    e.KEY_HOME: 0x47, e.KEY_UP: 0x48, e.KEY_PAGEUP: 0x49,
    e.KEY_LEFT: 0x4B, e.KEY_RIGHT: 0x4D, e.KEY_END: 0x4F,
    e.KEY_DOWN: 0x50, e.KEY_PAGEDOWN: 0x51, e.KEY_INSERT: 0x52,
    e.KEY_DELETE: 0x53,
    e.KEY_LEFTMETA: 0x5B, e.KEY_RIGHTMETA: 0x5C, e.KEY_COMPOSE: 0x5D,
    e.KEY_MUTE: 0x20, e.KEY_VOLUMEDOWN: 0x2E, e.KEY_VOLUMEUP: 0x30,
    e.KEY_NEXTSONG: 0x19, e.KEY_PLAYPAUSE: 0x22,
    e.KEY_PREVIOUSSONG: 0x10, e.KEY_STOPCD: 0x24,
}

# Deliberately absent: KEY_PAUSE, which is the one key that sends a three-byte
# sequence of its own (0xE1 0x1D 0x45) rather than a scan code, and F13..F24,
# which a PC keyboard has no set-1 codes for at all. A key with no code here is
# dropped rather than guessed at -- see _Keyboard.write.

MOUSE_BUTTONS = {
    e.BTN_LEFT: (0x0002, 0x0004),        # down, up
    e.BTN_RIGHT: (0x0008, 0x0010),
    e.BTN_MIDDLE: (0x0020, 0x0040),
    e.BTN_SIDE: (0x0080, 0x0100),        # XBUTTON1
    e.BTN_EXTRA: (0x0080, 0x0100),       # XBUTTON2, told apart by mouseData
}
XBUTTON = {e.BTN_SIDE: 1, e.BTN_EXTRA: 2}


class _Device:
    """What evdev's UInput offers, for the parts desk.py uses."""

    def __init__(self, name):
        self.name = name
        self.device = type("Node", (), {"path": "sendinput:" + name})()
        self._pending = []

    def syn(self):
        if self._pending:
            _send(*self._pending)
            self._pending = []

    def close(self):
        self.syn()


class Keyboard(_Device):
    def write(self, etype, code, value):
        if etype != e.EV_KEY:
            return
        scan, extended = SCAN.get(code), False
        if scan is None:
            scan, extended = SCAN_E0.get(code), True
        if scan is None:
            # Unknown or unsendable. Dropped rather than guessed: a wrong scan
            # code is a key the guest did not press arriving in somebody's
            # game, which is worse than a key that does nothing.
            return
        flags = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if extended else 0)
        if not value:
            flags |= KEYEVENTF_KEYUP
        self._pending.append(_INPUT(type=INPUT_KEYBOARD,
                                    ki=_KEYBDINPUT(wVk=0, wScan=scan,
                                                   dwFlags=flags, time=0,
                                                   dwExtraInfo=None)))


class Mouse(_Device):
    """Relative movement, buttons and both wheels."""

    def __init__(self, name):
        super().__init__(name)
        self._dx = self._dy = 0

    def write(self, etype, code, value):
        if etype == e.EV_REL:
            if code == e.REL_X:
                self._dx += value
            elif code == e.REL_Y:
                self._dy += value
            elif code in (e.REL_WHEEL, e.REL_HWHEEL):
                # evdev counts notches; Windows counts 120ths of one.
                flag = MOUSEEVENTF_WHEEL if code == e.REL_WHEEL else MOUSEEVENTF_HWHEEL
                self._pending.append(_INPUT(
                    type=INPUT_MOUSE,
                    mi=_MOUSEINPUT(dx=0, dy=0,
                                   mouseData=ctypes.c_uint32(
                                       value * WHEEL_DELTA).value,
                                   dwFlags=flag, time=0, dwExtraInfo=None)))
        elif etype == e.EV_KEY:
            pair = MOUSE_BUTTONS.get(code)
            if pair is None:
                return
            self._pending.append(_INPUT(
                type=INPUT_MOUSE,
                mi=_MOUSEINPUT(dx=0, dy=0, mouseData=XBUTTON.get(code, 0),
                               dwFlags=pair[0] if value else pair[1],
                               time=0, dwExtraInfo=None)))

    def syn(self):
        # Movement is accumulated and sent as one event, because desk.py
        # writes REL_X and REL_Y separately and two SendInput moves are two
        # cursor jumps -- visible as a stair-step on a diagonal drag.
        if self._dx or self._dy:
            self._pending.insert(0, _INPUT(
                type=INPUT_MOUSE,
                mi=_MOUSEINPUT(dx=self._dx, dy=self._dy, mouseData=0,
                               dwFlags=(MOUSEEVENTF_MOVE
                                        | MOUSEEVENTF_MOVE_NOCOALESCE),
                               time=0,
                               dwExtraInfo=None)))
            self._dx = self._dy = 0
        super().syn()


class Pointer(_Device):
    """Absolute positioning, in the 0..POINT_MAX the desk protocol carries.

    Windows wants 0..65535 across the whole virtual desktop, which is the same
    idea in a different unit, so this is a rescale rather than a conversion.
    """

    def __init__(self, name, span):
        super().__init__(name)
        self._span = max(1, span)
        self._x = self._y = 0

    def write(self, etype, code, value):
        if etype == e.EV_ABS:
            if code == e.ABS_X:
                self._x = value
            elif code == e.ABS_Y:
                self._y = value
        elif etype == e.EV_KEY:
            pair = MOUSE_BUTTONS.get(code)
            if pair is not None:
                self._pending.append(_INPUT(
                    type=INPUT_MOUSE,
                    mi=_MOUSEINPUT(dx=0, dy=0, mouseData=XBUTTON.get(code, 0),
                                   dwFlags=pair[0] if value else pair[1],
                                   time=0, dwExtraInfo=None)))

    def syn(self):
        scale = 65535 / self._span
        self._pending.insert(0, _INPUT(
            type=INPUT_MOUSE,
            mi=_MOUSEINPUT(dx=int(self._x * scale), dy=int(self._y * scale),
                           mouseData=0,
                           dwFlags=(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
                                    | MOUSEEVENTF_VIRTUALDESK),
                           time=0, dwExtraInfo=None)))
        super().syn()
