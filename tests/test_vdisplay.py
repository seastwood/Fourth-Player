"""A monitor made in software, so the picture is not limited to the panel.

The capture is the machine's own desktop, so the desktop's size is the most
the stream can contain: asking for more is the same pixels scaled up at a
larger picture's bitrate. That is fine when the console has a screen worth
sending, and wrong in two cases -- a host whose monitor is smaller than the
guest's, and a host with no monitor at all.

Most of this cannot run away from Windows, and says so rather than pretending.
What is checked everywhere is the part that has no excuse to be
platform-specific: the IOCTL numbers, which are a documented arithmetic and
were copied from a header by hand, and that the module is safe to import and
ask on a machine that has none of it.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from fourthplayer import vdisplay

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


print("the control codes are the ones the driver is listening for")
# CTL_CODE(FILE_DEVICE_UNKNOWN, function, METHOD_BUFFERED, FILE_ANY_ACCESS)
# is (0x22 << 16) | (function << 2). Worked out here rather than copied, so a
# transcription slip in the module fails rather than being confirmed by a
# constant transcribed the same way twice. An IOCTL sent to the wrong number
# is not a polite failure: it is an unknown request at a driver.
def ctl(function):
    return (0x22 << 16) | (function << 2)


for name, function in (("ADD", 0x800), ("REMOVE", 0x801), ("WATCHDOG", 0x803),
                       ("PING", 0x888), ("VERSION", 0x8FF)):
    have = getattr(vdisplay, "IOCTL_" + name)
    check(have == ctl(function),
          "IOCTL_%s is 0x%06x (0x%06x)" % (name, ctl(function), have))

print()
print("and they are all different, which a bad shift would not be")
codes = [vdisplay.IOCTL_ADD, vdisplay.IOCTL_REMOVE, vdisplay.IOCTL_WATCHDOG,
         vdisplay.IOCTL_PING, vdisplay.IOCTL_VERSION]
check(len(set(codes)) == len(codes), "five distinct codes")

print()
print("the interface is the driver's own, not one of the standard display ones")
# root\\display\\0000 also exposes GUID_DEVINTERFACE_DISPLAY_ADAPTER and
# GUID_DEVINTERFACE_MONITOR. Opening either of those and sending these codes
# would be talking to the wrong thing.
check(vdisplay.INTERFACE_GUID.lower()
      == "{e5bcc234-1e0c-418a-a0d4-ef8b7501414d}",
      "SUVDA_INTERFACE_GUID")
for standard in ("{1ca05180-a699-450a-9a0c-de4fbe3ddd89}",
                 "{5b45201d-f2f2-4f3b-85bb-30ff1f953599}"):
    check(vdisplay.INTERFACE_GUID.lower() != standard,
          "not %s" % standard)

print()
print("a machine with no virtual display driver is not broken by asking")
if sys.platform == "win32":
    print("  (on Windows: available() answers about this machine)")
    check(isinstance(vdisplay.available(), bool), "available() answers")
else:
    check(vdisplay.available() is False,
          "available() is False away from Windows, rather than raising")
    check(vdisplay.monitors() == [],
          "monitors() is empty rather than raising")
    screen = vdisplay.VirtualDisplay()
    check(screen.open(2560, 1440, 60) is False,
          "open() refuses rather than raising -- a host that cannot make a "
          "virtual screen must stream the real one, not fail to start")
    check(screen.monitor_index is None, "and points the capture at nothing")
    screen.close()
    check(True, "close() on something never opened is safe")

print()
print("the size asked for is the size that is sent")
# The whole point: a virtual screen is made at the streaming resolution, so
# there is no scaling anywhere. If these ever diverge the feature is pointless
# -- it would be an upscale with extra steps.
source = open(os.path.join(ROOT, "fourthplayer", "video.py")).read()
check("display.open(width, height, cfg.fps)" in source,
      "the capture asks for a screen the size of the picture")
check("monitor-handle=%d" in source,
      "and points the capture at it by handle, not by index -- an index is a "
      "position anything plugging in a screen can renumber")

print()
print("and it is given back when the capture stops")
check("self.vdisplay.close()" in source,
      "stop() closes it, so a recapture does not leave two behind")

print()
print("it is a switch somebody can reach, not a line in a file")
sess = open(os.path.join(ROOT, "fourthplayer", "session.py")).read()
page = open(os.path.join(ROOT, "web", "setup.html")).read()
script = open(os.path.join(ROOT, "web", "setup.js")).read()
check('"virtual_display"' in sess, "the host publishes whether it is on")
check('"can_virtual_display"' in sess,
      "and whether this machine could do it at all -- a switch that silently "
      "does nothing is worse than no switch")
check('id="set-virtual"' in page, "the page has the switch")
check("virtual_display: Boolean(" in script,
      "and sends a real boolean; a string would be true whatever it said, "
      "which turned a setting on once already")
check('for flag in ("audio", "virtual_display")' in sess,
      "both flags are read the same way, rather than one growing its own "
      "slightly different parsing")

print()
print("a display made in software can be any size, not one of six")
# The named sizes exist because the capture is a real screen and those are the
# shapes real screens come in. A software one has no such constraint, and
# matching a client exactly is the point of having it -- a MacBook is
# 2560x1664, which is not 16:9 and is never going to be in a list.
sess = open(os.path.join(ROOT, "fourthplayer", "session.py")).read()
check("if want_virtual and custom:" in sess,
      "an exact size is taken when the display is one we made")
check("_even" in sess, "and passed through something that squares it up")

import importlib.util
spec = importlib.util.spec_from_file_location(
    "sessmod", os.path.join(ROOT, "fourthplayer", "session.py"))
# Importing session.py needs GStreamer, so the rounding is exercised as the
# plain function it is rather than through the module.
import re as _re
body = _re.search(r"def _even\(value, low, high\):.*?return number - \(number % 2\)",
                  sess, _re.S).group(0)
ns = {}
exec("class _T:\n    @staticmethod\n    " + body.replace("\n", "\n    "), ns)
even = ns["_T"]._even

check(even(2560, 640, 7680) == 2560, "an even size is left alone")
check(even(1665, 480, 4320) == 1664,
      "an odd one is rounded down, because encoders refuse odd dimensions "
      "and somebody typing their laptop's height will not know that: got %d"
      % even(1665, 480, 4320))
check(even(100, 640, 7680) == 640, "below the floor is raised to it")
check(even(99999, 640, 7680) == 7680, "above the ceiling is capped")
check(even(4320, 480, 4320) == 4320, "the ceiling itself is allowed")
try:
    even("wide", 640, 7680)
    check(False, "nonsense is refused")
except ValueError:
    check(True, "nonsense is refused rather than turned into a size")

print()
print("and the boxes for it are only shown where they mean something")
page = open(os.path.join(ROOT, "web", "index.html")).read()
script = open(os.path.join(ROOT, "web", "app.js")).read()
check('id="stream-custom-row"' in page, "the row exists")
check('id="stream-custom-row" hidden' in page, "and starts hidden")
check("customRow.hidden = !state.virtual_display" in script,
      "shown only while a display made in software is on")
check("virtual && el(\"stream-width\")" in script,
      "and only sent when it is, so the named size still decides otherwise")

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
