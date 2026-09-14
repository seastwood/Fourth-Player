"""evdev key codes to the scan codes Windows is sent.

127 keys can arrive from the web UI, and a mistake in any one of them is a
single key that silently does the wrong thing on one platform -- a guest
pressing Home and getting Insert, with nothing anywhere to point at. So the
table is checked rather than trusted.

It runs everywhere, deliberately. The table is pure data and needs no Windows
to be read, and the value of checking it is highest on the machine where the
port is being written rather than the one it is for.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


from fourthplayer import keymap
from fourthplayer._codes import CODES

NAME = {}
for _n, _v in CODES.items():
    if _n.startswith("KEY_"):
        NAME.setdefault(_v, _n)

# windesk imports ctypes.wintypes, which exists only on Windows, so it is
# stubbed and the real module imported. Reading the table out of the source, or
# rebuilding it here from the same assumption it was written with, would give a
# test that passes whatever windesk.py actually says -- which is the one thing
# it must not do.
import ctypes
import types as _types

if not hasattr(ctypes, "wintypes"):
    _fake = _types.ModuleType("ctypes.wintypes")
    _fake.LONG = ctypes.c_long
    _fake.DWORD = ctypes.c_ulong
    _fake.WORD = ctypes.c_ushort
    sys.modules["ctypes.wintypes"] = _fake
    ctypes.wintypes = _fake

from fourthplayer.windesk import SCAN, SCAN_E0, MOUSE_BUTTONS

print("the identity range")
# Linux numbered its key codes to match AT scan code set 1 for this whole
# range. If that ever stopped being true, every letter would be wrong at once.
for name, want in (("KEY_ESC", 0x01), ("KEY_1", 0x02), ("KEY_Q", 0x10),
                   ("KEY_A", 0x1E), ("KEY_Z", 0x2C), ("KEY_SPACE", 0x39),
                   ("KEY_F1", 0x3B), ("KEY_F10", 0x44), ("KEY_KP0", 0x52),
                   ("KEY_F11", 0x57), ("KEY_F12", 0x58)):
    got = SCAN.get(CODES[name])
    check(got == want, "%-14s -> 0x%02X" % (name, got or 0))

print("\nthe ones that need an 0xE0 in front")
for name, want in (("KEY_UP", 0x48), ("KEY_DOWN", 0x50), ("KEY_LEFT", 0x4B),
                   ("KEY_RIGHT", 0x4D), ("KEY_HOME", 0x47), ("KEY_END", 0x4F),
                   ("KEY_INSERT", 0x52), ("KEY_DELETE", 0x53),
                   ("KEY_PAGEUP", 0x49), ("KEY_PAGEDOWN", 0x51),
                   ("KEY_RIGHTCTRL", 0x1D), ("KEY_RIGHTALT", 0x38),
                   ("KEY_LEFTMETA", 0x5B), ("KEY_KPENTER", 0x1C)):
    got = SCAN_E0.get(CODES[name])
    check(got == want, "%-14s -> E0 0x%02X" % (name, got or 0))

print("\nthe two halves do not overlap")
# A key in both tables would be sent twice, or with the wrong prefix,
# depending which was consulted first.
both = sorted(set(SCAN) & set(SCAN_E0))
check(not both, "no key is in both: %s"
      % ", ".join(NAME.get(c, str(c)) for c in both[:5]))

print("\nthe pairs that are easy to confuse are not confused")
# INSERT and KP0 share 0x52, DELETE and KPDOT share 0x53, HOME and KP7 share
# 0x47, and so on down the keypad. They are told apart by the prefix alone, so
# this is exactly where a mistake would hide.
for keypad, edit in (("KEY_KP0", "KEY_INSERT"), ("KEY_KPDOT", "KEY_DELETE"),
                     ("KEY_KP7", "KEY_HOME"), ("KEY_KP1", "KEY_END"),
                     ("KEY_KP8", "KEY_UP"), ("KEY_KP2", "KEY_DOWN")):
    plain, ext = SCAN.get(CODES[keypad]), SCAN_E0.get(CODES[edit])
    check(plain == ext and plain is not None,
          "%s and %s share 0x%02X and differ only by the prefix"
          % (keypad, edit, plain or 0))

print("\nwhat the web UI can send, and what the table answers for")
reachable = sorted(set(keymap.CODES.values()))
known = [c for c in reachable if c in SCAN or c in SCAN_E0]
unknown = [c for c in reachable if c not in SCAN and c not in SCAN_E0]
check(len(known) > 110,
      "%d of the %d keys a guest can press have a scan code"
      % (len(known), len(reachable)))
# The ones that do not are a short, known list rather than an accident.
expected_gaps = {"KEY_PAUSE"} | {"KEY_F%d" % n for n in range(13, 25)}
surprises = [NAME.get(c, str(c)) for c in unknown
             if NAME.get(c, "") not in expected_gaps]
check(not surprises,
      "and the ones without are only Pause and F13-F24%s"
      % ("" if not surprises else ": also " + ", ".join(surprises)))

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
