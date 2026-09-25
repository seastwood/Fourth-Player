"""What the accelerometer half of a motion pad reports, and why it is a choice.

A phone's accelerometer answers a different question from its gyroscope, and a
game that fuses the two cares which. Rotation rate does not depend on which way
is down. Gravity is nothing but which way is down.

A Switch emulator uses the second to stop the first drifting. So the same flick
of the wrist resolved differently sitting up and lying on one side -- with the
screen in portrait both times, and the gyroscope reporting the same numbers
both times. Reported as "the gyro x and y get messed up based on the way I am
sitting or laying", and it is not the gyro at all.

Read rather than run: importing the pad pulls in evdev and ViGEm, neither of
which is present on the machines this suite runs on.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
VIRTUAL = open(os.path.join(ROOT, "fourthplayer", "virtual.py"),
               encoding="utf-8").read()
CONFIG = open(os.path.join(ROOT, "fourthplayer", "config.py"),
              encoding="utf-8").read()
PADS = open(os.path.join(ROOT, "fourthplayer", "pads.py"),
            encoding="utf-8").read()
SESSION = open(os.path.join(ROOT, "fourthplayer", "session.py"),
               encoding="utf-8").read()

bad = 0


def check(ok, what):
    global bad
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        bad += 1


print("there are three ways, and they are named")
check('ACCEL_WAYS = ("steady", "device", "off")' in VIRTUAL,
      "steady, device and off")
check('guest_motion_accel: str = "steady"' in CONFIG,
      "and steady is the default, because aiming wants a fixed reference "
      "rather than an honest one")

print("\nand the report says which one it is sending")
report = VIRTUAL[VIRTUAL.index("packed = self.REPORT.pack("):]
report = report[:report.index("ctypes.memmove")]
check("*self._turn(gyro)," in report,
      "the gyroscope is always the phone's own, turned into the pad's frame")
check('self._turn(accel) if self.accel_way == "device"' in report,
      "the accelerometer is the phone's own only when asked for")
check('else self.ACCEL_STEADY if self.accel_way == "steady"' in report,
      "a steady one otherwise")
check("else (0, 0, 0)" in report, "and nothing when it is off")

print("\nthe steady one is a real resting reading, not zero")
found = re.search(r"ACCEL_STEADY = \((-?\d+), (-?\d+), (-?\d+)\)", VIRTUAL)
check(found is not None, "it is a constant in the pad's own frame")
if found:
    x, y, z = (int(v) for v in found.groups())
    # Two stages, and both matter. The page sends thousandths of a gravity
    # (ACCEL_PER_G in frame.js); the host scales those by ACCEL_SCALE on the
    # way into the report. So one gravity on the wire is the product of the
    # two, not either one of them -- which is what I got wrong when first
    # writing this, and what the failing line caught.
    frame = open(os.path.join(ROOT, "web", "frame.js"), encoding="utf-8").read()
    per_g = float(re.search(r"ACCEL_PER_G = ([\d.]+)", frame).group(1))
    scale = float(re.search(r"ACCEL_SCALE = ([\d.]+)", VIRTUAL).group(1))
    size = (x * x + y * y + z * z) ** 0.5 / (per_g * scale)
    check(abs(size - 1.0) < 0.01,
          "one gravity, so a fusion levelling against it is not told the pad "
          "is falling: %.2f g" % size)
    check((x, y, z) != (0, 0, 0),
          "and not zero, which is free-fall and means something else")

print("\nand it is not turned, because it has nothing to turn from")
# The gyro order converts a phone's frame into the pad's. A steady reading is
# already in the pad's frame: rotating it would aim the constant somewhere
# that depends on how the phone is held, which is the whole fault this avoids.
check("ACCEL_STEADY" in report and "self._turn(self.ACCEL_STEADY" not in report,
      "the constant goes out as it is")

print("\nand the setting reaches the pad")
check("accel=None" in PADS, "PadSet and VirtualPad both take it")
check("self._ui.accel_way = accel" in PADS,
      "and it is set on the device, like the gyro order beside it")
check('accel=getattr(self.cfg,\n                                                "guest_motion_accel", None)'
      in SESSION or "guest_motion_accel" in SESSION,
      "the session passes what the config says")

print("\n%d FAILED" % bad if bad else "\nall ok")
sys.exit(1 if bad else 0)
