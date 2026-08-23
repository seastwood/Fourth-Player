"""The wire format, and the two properties the design actually leans on:
a frame is a whole truth, and sequence numbers survive the wrap.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fourthplayer import protocol as P

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


print("frame shape")
check(P.FRAME_SIZE == 20, "a frame is 20 bytes, got %d" % P.FRAME_SIZE)
blank = P.encode(P.PadState())
check(len(blank) == P.FRAME_SIZE, "an empty pad still encodes to a full frame")

print("\nround trip")
s = P.PadState(seq=1234, buttons=(1 << P.BTN_A) | (1 << P.BTN_RIGHT),
               axes=[-32768, 32767, 0, 15, 32767, 0])
d = P.decode(P.encode(s))
check(d.seq == 1234, "sequence survives")
check(d.pressed(P.BTN_A) and d.pressed(P.BTN_RIGHT), "pressed buttons survive")
check(not d.pressed(P.BTN_B), "unpressed buttons stay unpressed")
check(d.axes == [-32768, 32767, 0, 15, 32767, 0], "axes survive at full range")

print("\nframes that cannot be trusted are dropped whole")
for bad, why in [(b"", "empty"), (b"\x01" * 19, "one byte short"),
                 (b"\x01" * 21, "one byte long")]:
    try:
        P.decode(bad)
        check(False, "a %s frame was accepted" % why)
    except P.ProtocolError:
        check(True, "a %s frame is refused" % why)

wrong_version = bytearray(P.encode(P.PadState()))
wrong_version[0] = 99
try:
    P.decode(bytes(wrong_version))
    check(False, "an unknown version was accepted")
except P.ProtocolError:
    check(True, "an unknown version is refused")

print("\nunknown buttons are ignored, not an error")
future = P.FRAME.pack(P.VERSION, 0, 0, 0xFFFFFFFF, 0, 0, 0, 0, 0, 0)
d = P.decode(future)
check(d.buttons == (1 << P.BUTTON_COUNT) - 1,
      "bits above the known buttons are masked off, got %#x" % d.buttons)

print("\nrelease-all is carried in the frame, not inferred")
d = P.decode(P.encode(P.PadState(release_all=True)))
check(d.release_all, "an explicit release survives the wire")

print("\nsequence numbers across the wrap")
check(P.is_newer(5, 4), "5 is newer than 4")
check(not P.is_newer(4, 5), "4 is not newer than 5")
check(P.is_newer(0, 65535), "0 is newer than 65535 -- the wrap")
check(P.is_newer(1, 65530), "1 is newer than 65530")
check(not P.is_newer(65535, 0), "65535 is not newer than 0")
check(not P.is_newer(7, 7), "a repeat of the same frame is not newer")

print("\nout-of-range axes are clamped rather than wrapping")
d = P.decode(P.encode(P.PadState(axes=[999999, -999999, 0, 0, 0, 0])))
check(d.axes[0] == 32767 and d.axes[1] == -32768,
      "a client sending nonsense gets clamped, not inverted: %r" % d.axes[:2])

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
