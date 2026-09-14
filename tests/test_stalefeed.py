"""A guest the session has let go of must not be able to make a controller.

Reported as a controller port that "kept disconnecting and reconnecting over
and over" after going into the controls panel and coming back. The journal
shows exactly how: the dead-man switch freed the slot after 25s without video
-- which is what sitting in the controls panel looks like from the host -- and
the page then rejoined while the old connection carried on sending pad state
every 50 ms.

`GuestConnection.pad` is a property that indexes Pads, and Pads.__getitem__
makes the device on first use, so each of those frames built the controller
straight back. `_unplug_orphans` removed it again each time, because no guest
in session.guests sat on that seat. Neither half is wrong alone; together they
are a fight at about four a second, and RetroArch and Steam both read that as
a controller being plugged and unplugged without end.
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


try:
    from fourthplayer import protocol
    from fourthplayer.session import GuestConnection
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)


class Pad:
    def __init__(self):
        self.applied = 0

    def apply(self, state, sender=None):
        self.applied += 1


class Seats:
    """Pads, with the property that matters here: indexing makes a device."""

    def __init__(self):
        self.devices = {}
        self.made = 0

    def __getitem__(self, index):
        if index not in self.devices:
            self.devices[index] = Pad()
            self.made += 1
        return self.devices[index]

    def release(self, index):
        return self.devices.pop(index, None) is not None


class Session:
    def __init__(self, pads):
        self.pads = pads
        self.guests = {}

    def holding(self, _guest):
        return (False, "")


def guest(session, slot, pad_index):
    g = GuestConnection.__new__(GuestConnection)
    g.session = session
    g.slot = slot
    g.pad_index = pad_index
    g.label = "guest %d" % slot
    g.frames = g.bad_frames = g.held_frames = g.stray_frames = 0
    g.last_input = 0.0
    return g


# A frame the real decoder accepts, built by the real encoder.
FRAME = protocol.encode(protocol.PadState()) if hasattr(protocol, "PadState") \
    else None
if FRAME is None:
    print("SKIPPED: no encoder here to build a frame with")
    sys.exit(0)

print("a seated guest reaches the pad")
pads = Seats()
session = Session(pads)
seated = guest(session, 0, 0)
session.guests[0] = seated
seated.feed(FRAME)
check(pads.made == 1 and pads.devices[0].applied == 1,
      "their frame makes the controller and lands on it: made=%d applied=%d"
      % (pads.made, pads.devices[0].applied if 0 in pads.devices else -1))

print("\na guest the session has dropped does not")
# Exactly what drop() leaves behind: out of session.guests, pad released, and
# a data channel still delivering.
session.guests.pop(0)
pads.release(0)
before = pads.made
for _ in range(200):
    seated.feed(FRAME)
check(pads.made == before,
      "two hundred frames from the dropped connection make no controller at "
      "all: made %d more" % (pads.made - before))
check(pads.devices == {},
      "and nothing is plugged in for the janitor to take away: %r"
      % sorted(pads.devices))
check(seated.stray_frames == 200,
      "they are counted rather than silently dropped: %d" % seated.stray_frames)

print("\nand cannot drive whoever got the seat after them")
# The seat may since have been handed to somebody else. Checking the slot
# number rather than the identity would let the stale connection drive them.
successor = guest(session, 0, 0)
session.guests[0] = successor
stale_before = seated.stray_frames
seated.feed(FRAME)
check(seated.stray_frames == stale_before + 1,
      "the old connection's frame is still refused: %d" % seated.stray_frames)
check(pads.made == before,
      "and made no device of its own: made=%d" % pads.made)
successor.feed(FRAME)
check(pads.made == before + 1 and pads.devices[0].applied == 1,
      "while the guest actually sitting there is served normally")

print("\nthe guard is on identity, and reads that way")
source = open(os.path.join(ROOT, "fourthplayer", "session.py"),
              encoding="utf-8").read()
body = source.split("def feed(self, data):")[1].split("\n    def ")[0]
check("is not self" in body,
      "feed compares the seated guest with `is not self`, so a slot number "
      "cannot stand in for who is sitting there")

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
