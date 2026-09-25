"""A guest given a DualShock instead of an Xbox pad.

Sunshine offers this and the reason is not cosmetic: a game reads the pad's
identity and names its buttons accordingly, so a guest on a DualShock told to
"press A" is being told to press a button that is not there.

Two halves, and they are tested differently.

The identity is chosen in pads.py and carried as the USB vendor and product
the device declares -- not as a flag -- because evdev's UInput has a fixed
signature with nowhere to put one, and because vendor and product are what SDL,
Windows and virtual.py all key off already.

The translation is in virtual.py, at the one edge where evdev's vocabulary
becomes ViGEm's. test_winpad.py tests the Xbox half against real XInput and
says, rightly, that there is no point faking that: XInput is the thing being
asked. But XInput cannot see a DS4 at all -- it is not an XInput device -- and
the decisions here are the kind a stub does catch: which face button a thumb
lands on, a stick scaled into a byte, and a flip that must *not* happen.

That last one is why this file exists. The Xbox path inverts every vertical
axis because evdev counts down as positive and XInput counts up. A DualShock
counts down as positive too, so the same flip would be wrong -- and wrong in
the way that is hardest to catch from a log, because the picture is fine, the
game responds, and up is down.
"""
import ctypes
import importlib
import struct
import importlib.util
import os
import time
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


# ---- the identity, which needs no backend at all ----

from fourthplayer import pads                                 # noqa: E402

print("-- the kinds on offer --")
check(set(pads.KINDS) == {"xbox360", "ds4"},
      "two, named: %s" % sorted(pads.KINDS))
check(pads.KINDS["xbox360"]["vendor"] == 0x045E
      and pads.KINDS["xbox360"]["product"] == 0x028E,
      "the Xbox pad keeps the identity SDL already has a mapping for")
check(pads.KINDS["ds4"]["vendor"] == 0x054C
      and pads.KINDS["ds4"]["product"] == 0x09CC,
      "and the DualShock declares Sony's, which is what everything keys on")

print("\n-- a name that is not a kind is not a reason to have no pad --")
check(pads.kind_or_default("ds4") == "ds4", "a good name is kept")
check(pads.kind_or_default("DS4 ") == "ds4", "spelled loosely, still kept")
for bad in ("", None, "switch", "ds5", 7):
    check(pads.kind_or_default(bad) == "xbox360",
          "%r falls back rather than raising -- the wrong pad is a button "
          "prompt showing the wrong letter, no pad is an evening lost" % (bad,))

print("\n-- a seat's kind, and what changing it does --")
seats = pads.PadSet.__new__(pads.PadSet)
seats._kind = "xbox360"
seats.names = ["one", "two"]
seats.pads = [None, None]
seats.kinds = ["xbox360", "xbox360"]
seats.released = []
seats.release = lambda i: seats.released.append(i)
check(seats.kind_for(0) == "xbox360", "a seat starts as the session's kind")
check(pads.PadSet.set_kind(seats, 0, "ds4") is True, "changing it says so")
check(seats.kind_for(0) == "ds4", "and it took")
check(seats.released == [],
      "with no device yet there is nothing to unplug")
check(pads.PadSet.set_kind(seats, 0, "ds4") is False,
      "setting it to what it already is changes nothing")
seats.pads[0] = object()
pads.PadSet.set_kind(seats, 0, "xbox360")
check(seats.released == [0],
      "but a seat that has a device has it unplugged: what a pad *is* cannot "
      "be changed once the kernel has it, so the device goes and the next "
      "frame makes a new one")
check(seats.kind_for(1) == "xbox360", "and the other seat is left alone")

# ---- the translation, with ViGEm stubbed ----
#
# The stub proves the mapping, not ViGEm. That distinction is worth keeping:
# test_winpad.py asks real XInput because XInput's semantics are the question
# there. Here the question is which of vgamepad's calls we make and with what,
# and a recorder answers that exactly.

class _Enum(dict):
    """Stands in for vgamepad's IntEnum members, by name."""

    def __getattr__(self, name):
        if name not in self:
            raise AttributeError(name)
        return self[name]


DS4_BUTTONS = _Enum({n: n for n in (
    "DS4_BUTTON_CROSS", "DS4_BUTTON_CIRCLE", "DS4_BUTTON_SQUARE",
    "DS4_BUTTON_TRIANGLE", "DS4_BUTTON_SHOULDER_LEFT",
    "DS4_BUTTON_SHOULDER_RIGHT", "DS4_BUTTON_TRIGGER_LEFT",
    "DS4_BUTTON_TRIGGER_RIGHT", "DS4_BUTTON_SHARE", "DS4_BUTTON_OPTIONS",
    "DS4_BUTTON_THUMB_LEFT", "DS4_BUTTON_THUMB_RIGHT")})
DS4_SPECIAL_BUTTONS = _Enum({n: n for n in
                             ("DS4_SPECIAL_BUTTON_PS",
                              "DS4_SPECIAL_BUTTON_TOUCHPAD")})
DS4_DPAD_DIRECTIONS = _Enum({n: n for n in (
    "DS4_BUTTON_DPAD_NONE", "DS4_BUTTON_DPAD_NORTH",
    "DS4_BUTTON_DPAD_NORTHEAST", "DS4_BUTTON_DPAD_EAST",
    "DS4_BUTTON_DPAD_SOUTHEAST", "DS4_BUTTON_DPAD_SOUTH",
    "DS4_BUTTON_DPAD_SOUTHWEST", "DS4_BUTTON_DPAD_WEST",
    "DS4_BUTTON_DPAD_NORTHWEST")})
XUSB_BUTTON = _Enum({n: n for n in (
    "XUSB_GAMEPAD_A", "XUSB_GAMEPAD_B", "XUSB_GAMEPAD_X", "XUSB_GAMEPAD_Y",
    "XUSB_GAMEPAD_LEFT_SHOULDER", "XUSB_GAMEPAD_RIGHT_SHOULDER",
    "XUSB_GAMEPAD_BACK", "XUSB_GAMEPAD_START", "XUSB_GAMEPAD_LEFT_THUMB",
    "XUSB_GAMEPAD_RIGHT_THUMB", "XUSB_GAMEPAD_GUIDE",
    "XUSB_GAMEPAD_DPAD_LEFT", "XUSB_GAMEPAD_DPAD_RIGHT",
    "XUSB_GAMEPAD_DPAD_UP", "XUSB_GAMEPAD_DPAD_DOWN")})


class _ShortReport(ctypes.Structure):
    """The fields vgamepad fills, which the extended report starts with."""

    _fields_ = [("bThumbLX", ctypes.c_ubyte), ("bThumbLY", ctypes.c_ubyte),
                ("bThumbRX", ctypes.c_ubyte), ("bThumbRY", ctypes.c_ubyte),
                ("wButtons", ctypes.c_ushort), ("bSpecial", ctypes.c_ubyte),
                ("bTriggerL", ctypes.c_ubyte), ("bTriggerR", ctypes.c_ubyte)]

    def __init__(self):
        super().__init__()
        # Centred, as vgamepad's own default report is. Zero is both sticks
        # held hard up and left, which is what a motion-only frame would have
        # sent if this started there.
        self.bThumbLX = self.bThumbLY = 0x80
        self.bThumbRX = self.bThumbRY = 0x80
        # Neutral d-pad, which is 8 in the low nibble and not 0.
        self.wButtons = 8


class _SubReportEx(ctypes.Structure):
    """As much of ViGEm's extended report as this checks, at its real offsets.

    Taken from the struct on the host rather than invented: gyro at 14 and
    acceleration at 20, with the short report's fields at the front. Building
    it here with ctypes means the copy-by-name in virtual.py is exercised
    against a layout shaped like the real one.
    """

    _fields_ = [("bThumbLX", ctypes.c_ubyte), ("bThumbLY", ctypes.c_ubyte),
                ("bThumbRX", ctypes.c_ubyte), ("bThumbRY", ctypes.c_ubyte),
                ("wButtons", ctypes.c_ushort), ("bSpecial", ctypes.c_ubyte),
                ("bTriggerL", ctypes.c_ubyte), ("bTriggerR", ctypes.c_ubyte),
                ("_pad0", ctypes.c_ubyte), ("wTimestamp", ctypes.c_ushort),
                ("bBatteryLvl", ctypes.c_ubyte), ("_pad1", ctypes.c_ubyte),
                ("wGyroX", ctypes.c_short), ("wGyroY", ctypes.c_short),
                ("wGyroZ", ctypes.c_short), ("wAccelX", ctypes.c_short),
                ("wAccelY", ctypes.c_short), ("wAccelZ", ctypes.c_short)]


class _ReportEx(ctypes.Union):
    """A union, exactly as ViGEm declares it -- and with vgamepad's bug in it.

    `Report` is the ctypes-*aligned* struct above, which is what vgamepad
    hands out and which has phantom padding at offsets 9 and 13 that a HID
    report does not have. `ReportBuffer` is the same memory seen as bytes.

    Keeping the aligned struct here rather than quietly fixing it is the point:
    the code under test has to write the right bytes *despite* it, and a stub
    that was packed would pass whether or not it did.
    """

    _fields_ = [("Report", _SubReportEx),
                ("ReportBuffer", ctypes.c_ubyte * 63)]


# The real thing, which has no padding because it describes bytes on a wire.
PACKED = struct.Struct("<BBBBHBBBHBhhhhhh")


class FakeCommons:
    DS4_REPORT_EX = _ReportEx


class FakeWin:
    """`vgamepad.win`, which is how the extended report is reached."""

    vigem_commons = FakeCommons


class Recorder:
    """A vgamepad pad that writes down what it was asked to do."""

    def __init__(self):
        self.down = set()
        self.special = set()
        self.left = self.right = None
        self.triggers = {}
        self.dpad = None
        self.updates = 0
        self.extended = None
        self.stamps = []
        self.raw = None
        self.sticks = None
        self.buttonword = None
        self.battery = None
        self.report = _ShortReport()

    # vgamepad's own calls write into self.report and update() sends it. The
    # first version of this stub only recorded them, so the repacking read
    # zeros -- and the suite would have blessed a report with the sticks hard
    # up and left in it. A stand-in narrower than the real object is the
    # commonest way a test here passes for the wrong reason.
    _BITS = {name: 1 << i for i, name in enumerate(DS4_BUTTONS)}

    def press_button(self, button):
        self.down.add(button)
        self.report.wButtons |= self._BITS.get(button, 0)

    def release_button(self, button):
        self.down.discard(button)
        self.report.wButtons &= ~self._BITS.get(button, 0) & 0xFFFF

    def press_special_button(self, special_button):
        self.special.add(special_button)

    def release_special_button(self, special_button):
        self.special.discard(special_button)

    def left_joystick(self, x_value, y_value):
        self.left = (x_value, y_value)
        self.report.bThumbLX, self.report.bThumbLY = x_value, y_value

    def right_joystick(self, x_value, y_value):
        self.right = (x_value, y_value)
        self.report.bThumbRX, self.report.bThumbRY = x_value, y_value

    def left_trigger(self, value):
        self.triggers["l"] = value
        self.report.bTriggerL = value

    def right_trigger(self, value):
        self.triggers["r"] = value
        self.report.bTriggerR = value

    def directional_pad(self, direction): self.dpad = direction

    # vgamepad fills this as the buttons and sticks are set, and the extended
    # report is built from it.
    report = None

    def update(self): self.updates += 1

    def update_extended_report(self, report):
        # Read out of the bytes at the offsets a DualShock really uses, not out
        # of the aligned struct's fields. Reading the fields would agree with
        # the bug this exists to catch.
        raw = bytes(bytearray(report.ReportBuffer)[:PACKED.size])
        got = PACKED.unpack(raw)
        self.extended = ([got[10], got[11], got[12]],
                         [got[13], got[14], got[15]])
        self.stamps.append(got[8])
        self.sticks = got[0:4]
        self.buttonword = got[4]
        self.battery = got[9]
        self.raw = raw

    def reset(self): self.__init__()


class FakeVgamepad:
    win = FakeWin
    DS4_BUTTONS = DS4_BUTTONS
    DS4_SPECIAL_BUTTONS = DS4_SPECIAL_BUTTONS
    DS4_DPAD_DIRECTIONS = DS4_DPAD_DIRECTIONS
    XUSB_BUTTON = XUSB_BUTTON
    made = []

    @staticmethod
    def VDS4Gamepad():
        FakeVgamepad.made.append("ds4")
        return Recorder()

    @staticmethod
    def VX360Gamepad():
        FakeVgamepad.made.append("x360")
        return Recorder()


def windows_backend():
    """virtual.py as it is on Windows, on whatever machine this runs.

    evdev is set to None in sys.modules, which makes `from evdev import ...`
    raise ImportError -- the exact condition the module branches on -- and the
    module is then loaded fresh under its own name so the real one is left
    alone for everything else in the suite.
    """
    saved = sys.modules.get("evdev", "absent")
    sys.modules["evdev"] = None
    try:
        spec = importlib.util.spec_from_file_location(
            "fourthplayer._virtual_windows",
            os.path.join(ROOT, "fourthplayer", "virtual.py"))
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "fourthplayer"
        spec.loader.exec_module(mod)
        return mod
    finally:
        if saved == "absent":
            sys.modules.pop("evdev", None)
        else:
            sys.modules["evdev"] = saved


virtual = windows_backend()
print("-- the Windows backend, loaded as Windows loads it --")
check(virtual.BACKEND == "vigem",
      "it took the ViGEm branch, got %r" % virtual.BACKEND)

sys.modules["vgamepad"] = FakeVgamepad
# virtual.py reaches the extended report through `import
# vgamepad.win.vigem_commons`, which is a submodule import and does not go
# through the top-level object -- so it needs registering by name too.
sys.modules["vgamepad.win"] = FakeWin
sys.modules["vgamepad.win.vigem_commons"] = FakeCommons
from fourthplayer.codes import ecodes as e                    # noqa: E402


def a_pad(kind):
    spec = pads.KINDS[kind]
    FakeVgamepad.made = []
    ui = virtual.UInput(pads.capabilities(True), name="test",
                        vendor=spec["vendor"], product=spec["product"],
                        version=spec["version"], bustype=0x0003)
    return ui, ui._pad


print("\n-- the declared vendor is what picks the target --")
ui, pad = a_pad("ds4")
check(FakeVgamepad.made == ["ds4"],
      "Sony's vendor opens a DS4 target, got %r" % FakeVgamepad.made)
check("ds4" in ui.device.path, "and says so in its path: %s" % ui.device.path)
ui2, _ = a_pad("xbox360")
check(FakeVgamepad.made == ["x360"],
      "Microsoft's opens an Xbox one, got %r" % FakeVgamepad.made)

print("\n-- the face buttons go by position, not by letter --")
ui, pad = a_pad("ds4")
for code, want in ((e.BTN_A, "DS4_BUTTON_CROSS"), (e.BTN_B, "DS4_BUTTON_CIRCLE"),
                   (e.BTN_X, "DS4_BUTTON_SQUARE"),
                   (e.BTN_Y, "DS4_BUTTON_TRIANGLE")):
    pad.down.clear()
    ui.write(e.EV_KEY, code, 1)
    check(pad.down == {want},
          "%s lands on %s, got %r" % (code, want, pad.down))

print("\n-- and the PS button is a special one, in its own byte --")
ui, pad = a_pad("ds4")
ui.write(e.EV_KEY, e.BTN_MODE, 1)
check(pad.special == {"DS4_SPECIAL_BUTTON_PS"} and not pad.down,
      "press_special_button, not press_button: %r / %r" % (pad.special, pad.down))
ui.write(e.EV_KEY, e.BTN_MODE, 0)
check(pad.special == set(), "and it lets go")

print("\n-- sticks become bytes, and are NOT flipped --")
ui, pad = a_pad("ds4")
check(pad.left == (128, 128) or ui._lx == 128,
      "a new pad's sticks sit in the middle at 128, not at 0 -- which on a "
      "DS4 is both sticks held hard up and left")
ui.write(e.EV_ABS, e.ABS_X, 32767)
ui.write(e.EV_ABS, e.ABS_Y, 32767)
check(pad.left == (255, 255),
      "hard right and hard *down* both read 255: evdev and a DualShock agree "
      "that down is positive, so the flip XInput needs would put up at the "
      "bottom. Got %r" % (pad.left,))
ui.write(e.EV_ABS, e.ABS_X, -32768)
ui.write(e.EV_ABS, e.ABS_Y, -32768)
check(pad.left == (0, 0), "and the other corner is 0, got %r" % (pad.left,))
ui.write(e.EV_ABS, e.ABS_X, 0)
check(pad.left[0] == 128, "centre is 128, got %r" % (pad.left[0],))
# The Xbox path must still invert, or fixing one broke the other.
ui3, x360 = a_pad("xbox360")
ui3.write(e.EV_ABS, e.ABS_Y, 32767)
check(x360.left == (0, -32767),
      "the Xbox pad still inverts, because XInput counts up: got %r"
      % (x360.left,))

print("\n-- a trigger is an axis and a button at once --")
ui, pad = a_pad("ds4")
ui.write(e.EV_ABS, e.ABS_Z, 200)
check(pad.triggers.get("l") == 200, "the analog value goes out")
check("DS4_BUTTON_TRIGGER_LEFT" in pad.down,
      "and the digital bit with it -- a real DS4 sets both, and games read "
      "either")
ui.write(e.EV_ABS, e.ABS_Z, 0)
check(pad.triggers.get("l") == 0 and "DS4_BUTTON_TRIGGER_LEFT" not in pad.down,
      "and both let go together")

print("\n-- the d-pad is one of eight directions, diagonals named --")
ui, pad = a_pad("ds4")
for x, y, want in ((0, 0, "NONE"), (0, -1, "NORTH"), (1, -1, "NORTHEAST"),
                   (1, 0, "EAST"), (1, 1, "SOUTHEAST"), (0, 1, "SOUTH"),
                   (-1, 1, "SOUTHWEST"), (-1, 0, "WEST"),
                   (-1, -1, "NORTHWEST")):
    ui.write(e.EV_ABS, e.ABS_HAT0X, x)
    ui.write(e.EV_ABS, e.ABS_HAT0Y, y)
    check(pad.dpad == "DS4_BUTTON_DPAD_" + want,
          "(%d,%d) is %s, got %r" % (x, y, want, pad.dpad))

print("\n-- motion, which only a DualShock has anywhere to put --")
ui, pad = a_pad("xbox360")
check(ui.motion([100, 0, 0, 0, 0, 1000]) is False,
      "an Xbox pad refuses it rather than dropping it silently: there is no "
      "motion in an XUSB report and nowhere honest to put it")
ui, pad = a_pad("ds4")
check(ui.motion(None) is False, "no values is not motion")
check(ui.motion([1, 2]) is False, "and neither is a short list")
check(ui.motion([160, -320, 480, 0, 0, 1000]) is True, "six values are taken")
check(ui.motion([160, -320, 480, 0, 0, 1000]) is False,
      "and the same six again change nothing, so an unmoving hand does not "
      "send a report per frame")

print("\n-- the wire's units become the pad's --")
ui, pad = a_pad("ds4")
# 10 deg/s is 160 on the wire, and a DS4's gyroscope reads about sixteen
# counts per degree per second, so that is 160 there too -- a coincidence,
# which is why the scale is a named 1.0 rather than an absent multiply.
#
# Pitch, which is the wire's first rotation and the pad's third: see
# GYRO_ORDER. Asserted where it lands rather than where it started, so this
# says something about the scaling and nothing about the order -- the order
# has its own check below.
ui.motion([160, 0, 0, 0, 0, 1000])
ui.syn()
check(pad.extended is not None, "an extended report is what carries it")
gyro, accel = pad.extended
check(gyro[2] == 160, "10 deg/s stays 160, got %r" % (gyro[2],))
# One gravity is 1000 on the wire and about 8192 on a DS4. On the wire's z,
# which GYRO_ORDER moves to the pad's y -- gravity rides the same rotation as
# the rotation does.
check(accel[1] == 8192, "1 g becomes 8192, got %r" % (accel[1],))

print("\n-- at the offsets a DualShock really uses, not the aligned ones --")
# The fault this replaced. ViGEm declares its report structs packed -- they
# describe bytes on a wire -- and vgamepad's ctypes translation leaves _pack_
# unset, so ctypes inserts a byte at offset 9 and another at 13. Writing
# through the named fields put the timestamp over the battery and gyro X's low
# byte, pitch on the real gyro Y, yaw on gyro Z, and roll into the
# accelerometer. The real gyro X -- up and down -- received a battery level of
# zero and a padding byte, for ever.
ui, pad = a_pad("ds4")
# Wire units in: sixteenths of a degree per second, thousandths of gravity.
# Rotation passes through unscaled and acceleration is multiplied by 8.192,
# so one gravity on z is 8192.
ui.motion([-877, 236, -226, 0, 0, 1000])
ui.syn()
check(pad.raw is not None, "a report went out")
# The offset, which is the point: a DualShock keeps its first gyro word at
# byte 12. ctypes' aligned struct puts it at 14, and writing there fed the
# real gyro X a battery level of zero for ever.
check(pad.raw[12:14] == bytes([0xec, 0x00]),
      "the first gyro word is at byte 12, not 14: %s"
      % pad.raw[12:14].hex(" "))
check(pad.raw[14:16] != bytes([0xec, 0x00]),
      "and is not also sitting where the aligned struct would have put it")
# The rotations are permuted on the way out. A controller is held face up and
# a phone in portrait face toward you, so the frames differ -- and which wrist
# should steer is a preference on top of that. Both were settled by watching a
# game, in two goes: straight through gave the vertical to the wrong wrist,
# and swapping pitch and roll alone left the horizontal on turning the phone
# like a door rather than twisting it.
check(pad.extended[0] == [236, -226, -877],
      "the pad receives (yaw, roll, pitch): wire (pitch -877, yaw 236, roll "
      "-226) leaves as (%r)" % (pad.extended[0],))
check(virtual.UInput.GYRO_ORDER == (1, 2, 0),
      "with the order named rather than buried in the packing, since it was "
      "found by watching a game and may need finding again")
check(sorted(virtual.UInput.GYRO_ORDER) == [0, 1, 2],
      "and it is a permutation -- every rotation goes somewhere and none goes "
      "twice, which a hand-edited tuple can quietly stop being")
# Gravity is permuted with the rotation, because the two describe one object.
# One gravity on the wire's z leaves on the pad's y, since GYRO_ORDER puts the
# wire's third component second.
check(pad.extended[1] == [0, 8192, 0],
      "gravity rides the same rotation as the gyro, scaled into the pad's "
      "units: %r" % (pad.extended[1],))

# And the order has to be a rotation rather than any old permutation: swapping
# two axes is a mirror, and nothing can be held that way. Only the cyclic ones
# are postures.
order = virtual.UInput.GYRO_ORDER
cyclic = [(0, 1, 2), (1, 2, 0), (2, 0, 1)]
check(tuple(order) in cyclic,
      "the order is one a real object could be in -- a swap of two axes flips "
      "handedness, which is why fixing the horizontal that way kept "
      "disturbing the vertical. Got %r" % (order,))
check(pad.battery == 0xFF,
      "with a charge that does not read as flat: %r" % (pad.battery,))
# The struct's own fields must now disagree, or the padding is not being
# stepped over and this test is passing for the wrong reason.
aligned = _ReportEx()
ctypes.memmove(aligned.ReportBuffer, pad.raw, len(pad.raw))
check(aligned.Report.wGyroX != -877,
      "and reading it back through the aligned fields gives the wrong answer "
      "(%d), which is what proves the padding is being stepped over rather "
      "than the stub being packed" % aligned.Report.wGyroX)

print("\n-- and the order can be set, but only to a posture --")
# It cannot be worked out from the host: part frame conversion, part
# preference about which wrist steers, and the only instrument for either is
# somebody playing a game. Three goes at guessing it from this end each moved
# the wrong axis, so it is settable.
check(sorted(pads.GYRO_ORDERS.values()) == [(0, 1, 2), (1, 2, 0), (2, 0, 1)],
      "the three on offer are the cyclic ones, which are the rotations: %r"
      % (sorted(pads.GYRO_ORDERS.values()),))
check(pads.gyro_order("roll,pitch,yaw") == (2, 0, 1), "a name is honoured")
check(pads.gyro_order("Roll, Pitch, Yaw") == (2, 0, 1),
      "spelled loosely, still honoured")
for bad in ("", None, "sideways", "pitch,roll,yaw", 7):
    check(pads.gyro_order(bad) == pads.GYRO_ORDERS[pads.DEFAULT_GYRO_ORDER],
          "%r falls back rather than raising" % (bad,))
# "pitch,roll,yaw" above is the trap: a real-looking name that is a swap of
# two axes, which is a mirror rather than a posture. It must not be offered.
check("pitch,roll,yaw" not in pads.GYRO_ORDERS,
      "and a mirror is not one of the names, however plausible it reads")

ui, pad = a_pad("ds4")
check(ui.gyro_order((2, 0, 1)) is True, "the device takes a rotation")
check(ui.GYRO_ORDER == (2, 0, 1), "and uses it")
check(ui.gyro_order((2, 1, 0)) is False,
      "and refuses a mirror rather than quietly accepting it")
check(ui.GYRO_ORDER == (2, 0, 1), "keeping what it had")
check(ui.gyro_order("nonsense") is False, "and refuses nonsense")

print("\n-- the buttons and sticks still land where they were --")
ui, pad = a_pad("ds4")
ui.write(e.EV_ABS, e.ABS_X, 32767)
ui.write(e.EV_KEY, e.BTN_A, 1)
ui.motion([0, 0, 0, 0, 0, 1000])
ui.syn()
check(pad.sticks[0] == 255,
      "a stick pushed right is 255 in the first byte: %r" % (pad.sticks[0],))
check(pad.sticks[1] == 128, "and the other axis stays centred")
check(pad.buttonword != 0,
      "with the button word carried too, not lost to the repacking: %#x"
      % pad.buttonword)

print("\n-- and a report with motion says time is passing --")
# A game integrating rotation into an aim uses the report timestamp as its
# clock. Frozen, it may read as no time having passed at all.
first = pad.stamps[-1]
time.sleep(0.02)
ui.motion([200, 0, 0, 0, 0, 1000])
ui.syn()
check(pad.stamps[-1] != first,
      "the timestamp moved between two reports: %r then %r"
      % (first, pad.stamps[-1]))

print("\n-- a pad with no motion still sends the short report --")
ui, pad = a_pad("ds4")
ui.write(e.EV_KEY, e.BTN_A, 1)
ui.syn()
check(pad.updates == 1 and pad.extended is None,
      "update(), not update_extended_report(): a guest sending no motion is "
      "unchanged by any of this")

print("\n-- and nothing reaches the game until syn --")
ui, pad = a_pad("ds4")
ui.write(e.EV_KEY, e.BTN_A, 1)
ui.write(e.EV_KEY, e.BTN_B, 1)
check(pad.updates == 0, "two buttons in one frame send nothing yet")
ui.syn()
check(pad.updates == 1,
      "and one report when the frame ends, not one per button: %d"
      % pad.updates)

print()
if fails:
    print("FAILURES: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_ds4pad: all ok")
