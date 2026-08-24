"""Virtual pads: the translation, the diffing, and the dead-man switch.

UInput is replaced with a recorder, so this runs anywhere -- no kernel, no
/dev/uinput, no permissions. What it cannot prove is that the kernel accepts
the capability set; that is what `tools/padcheck.py` is for, on real hardware.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fourthplayer import protocol as P

try:
    from evdev import ecodes as e
except ImportError:
    print("SKIPPED: python3-evdev is not installed here, so pads cannot be")
    print("         imported. This suite runs on the host machine.")
    sys.exit(0)

from fourthplayer import pads as PADS

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


class FakeDevice:
    path = "/dev/input/event99"


class FakeUInput:
    def __init__(self, caps, name=None, vendor=None, product=None,
                 version=None, bustype=None):
        self.caps, self.name = caps, name
        self.device = FakeDevice()
        self.written = []
        self.syns = 0
        self.closed = False

    def write(self, etype, code, value):
        self.written.append((etype, code, value))

    def syn(self):
        self.syns += 1

    def close(self):
        self.closed = True


PADS.UInput = FakeUInput

print("the device is a gamepad and nothing else")
caps = PADS.capabilities()
check(set(caps) == {e.EV_KEY, e.EV_ABS},
      "only buttons and axes are declared, got %r" % sorted(caps))
letters = [c for c in caps[e.EV_KEY] if e.KEY_A <= c <= e.KEY_Z]
check(not letters, "no keyboard code is declared, found %r" % letters)
check(e.EV_REL not in caps, "no relative axes, so no mouse")

print("\ntranslation")
state = P.PadState(buttons=(1 << P.BTN_A), axes=[100, 0, 0, 0, P.TRIGGER_MAX, 0])
ev = dict(((t, c), v) for t, c, v in PADS.to_events(state))
check(ev[(e.EV_KEY, e.BTN_A)] == 1, "A pressed")
check(ev[(e.EV_KEY, e.BTN_B)] == 0, "B not pressed")
check(ev[(e.EV_ABS, e.ABS_X)] == 100, "left stick passes through")
check(ev[(e.EV_ABS, e.ABS_Z)] == PADS.TRIGGER_MAX,
      "a full trigger scales to %d, got %r" % (PADS.TRIGGER_MAX, ev[(e.EV_ABS, e.ABS_Z)]))

print("\nthe d-pad becomes two signed axes")
def hat(*buttons):
    s = P.PadState(buttons=sum(1 << b for b in buttons))
    d = dict(((t, c), v) for t, c, v in PADS.to_events(s))
    return d[(e.EV_ABS, e.ABS_HAT0X)], d[(e.EV_ABS, e.ABS_HAT0Y)]

check(hat(P.BTN_RIGHT) == (1, 0), "right is +X")
check(hat(P.BTN_LEFT) == (-1, 0), "left is -X")
check(hat(P.BTN_UP) == (0, -1), "up is -Y")
check(hat(P.BTN_DOWN) == (0, 1), "down is +Y")
check(hat(P.BTN_LEFT, P.BTN_RIGHT) == (0, 0),
      "opposing presses cancel rather than one winning")

print("\nonly changes are written")
clock = [0.0]
pad = PADS.VirtualPad("test", now=lambda: clock[0])
pad.apply(P.PadState(seq=1, buttons=1 << P.BTN_A), now=lambda: clock[0])
first = len(pad._ui.written)
check(first > 0, "the first frame writes something")
pad.apply(P.PadState(seq=2, buttons=1 << P.BTN_A), now=lambda: clock[0])
check(len(pad._ui.written) == first,
      "an identical frame writes nothing more, wrote %d" % (len(pad._ui.written) - first))
pad.apply(P.PadState(seq=3, buttons=1 << P.BTN_B), now=lambda: clock[0])
check(len(pad._ui.written) > first, "a changed frame does write")

print("\nstale frames are ignored but still count as proof of life")
clock[0] = 5.0
before = len(pad._ui.written)
applied = pad.apply(P.PadState(seq=2, buttons=0), now=lambda: clock[0])
check(applied is False, "an out-of-order frame is refused")
check(len(pad._ui.written) == before, "and changes nothing")
check(pad.last_seen == 5.0, "but does prove the guest is alive")

print("\nthe dead-man switch")
clock[0] = 0.0
pads = PADS.PadSet(2, now=lambda: clock[0])
pads[0].apply(P.PadState(seq=1, buttons=1 << P.BTN_RIGHT), now=lambda: clock[0])
check(not pads[0].released, "a pad holding a direction is not released")
clock[0] = 0.1
check(pads.sweep() == [], "a pad heard from 100 ms ago is left alone")
clock[0] = 0.5
opened = pads.sweep()
check(opened == [pads[0]], "a silent pad is opened, got %r" % opened)
check(pads[0].released, "and is marked released")
last = dict(((t, c), v) for t, c, v in
            [(t, c, v) for t, c, v in pads[0]._ui.written])
check(last[(e.EV_ABS, e.ABS_HAT0X)] == 0, "the stuck direction is centred")
check(pads.sweep() == [], "sweeping again does not re-release it")

print("\na reconnecting guest starts counting again from zero")
clock[0] = 0.0
pad = PADS.VirtualPad("rejoin", now=lambda: clock[0])
for seq in range(1, 400):
    pad.apply(P.PadState(seq=seq, buttons=1 << P.BTN_A), now=lambda: clock[0])
check(pad._seq == 399, "the pad tracked the sequence up to 399")
# The browser reloads. Its counter restarts, and every frame now looks stale.
stale = pad.apply(P.PadState(seq=0, buttons=1 << P.BTN_B), now=lambda: clock[0])
check(stale is False,
      "without being told, a restarted counter reads as stale -- this is the bug")
pad.adopt_new_sender()
fresh = pad.apply(P.PadState(seq=0, buttons=1 << P.BTN_B), now=lambda: clock[0])
check(fresh is True, "after adopt_new_sender the same frame is accepted")
check(pad._ui.written, "and it actually reached the device")
pad.adopt_new_sender()
check(pad.released, "adopting also releases whatever the old guest was holding")

print("\nrelease is idempotent and close is safe")
pads[1].release_all()
pads.close()
check(True, "closing a set with an already-released pad does not raise")

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
