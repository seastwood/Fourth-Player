"""A controller nobody is sitting on should not exist.

Three of them survived on the console with no guests connected and nothing
reconnecting. Whatever the route there, the state is always wrong: a device
takes a player port in RetroArch, and Steam may hand a running game to it --
which is how an evening went, with a game bound to a pad nobody could press.
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
    from fourthplayer.session import LiveSession
    from fourthplayer.config import Config
    from fourthplayer import pads
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)


class Pad:
    pass


class Seats:
    """A stand-in that keeps the real grace period rather than skipping it.

    unplug_idle is borrowed from the real PadSet instead of being written out
    here, so the timing this suite depends on is the timing that ships. A
    hand-written copy was how the last three of these stubs quietly stopped
    describing the thing they stood in for.
    """

    unplug_idle = pads.PadSet.unplug_idle

    def __init__(self, count):
        self.devices = {i: Pad() for i in range(count)}
        self.names = ["Fourth Player %d" % (i + 1) for i in range(count)]
        self._empty_at = {}
        self.clock = [0.0]

    def _now(self):
        return self.clock[0]

    def wait(self, seconds):
        self.clock[0] += seconds

    # unplug_idle reads self.pads[index] before releasing, to hand the caller
    # the device it unplugged.
    @property
    def pads(self):
        return {i: self.devices.get(i) for i in range(len(self.names))}

    def live(self):
        return list(self.devices.items())

    def name_for(self, index):
        return self.names[index]

    def release(self, index):
        return self.devices.pop(index, None) is not None


class Invite:
    """Which seats somebody may still walk back into.

    Leaving leaves a claim behind; coming back into a different seat consumes
    it. That is the difference between an empty seat worth keeping a
    controller plugged into and one that is simply abandoned, and the janitor
    asks about it -- so a stub that cannot answer makes every empty seat look
    abandoned.
    """

    def __init__(self, claimed=()):
        self.claimed = set(claimed)

    def claimed_slots(self, now):
        return set(self.claimed)


class Guest:
    def __init__(self, slot, pad_index):
        self.slot = slot
        self.pad_index = pad_index
        self.label = "guest %d" % slot


print("a seat nobody is on keeps its device for a moment, then loses it")
# Unplugging is something an emulator notices and does not always undo: it
# binds a game's motion controls to a particular device, and a pad that goes
# away and comes back is a different one to it. So an empty seat is given a
# grace period -- long enough to cover leaving a stream and returning, which
# is a thing people do constantly.
session = LiveSession.__new__(LiveSession)
# Everything the janitor asks a session, in one place rather than discovered
# one AttributeError at a time: the config for the grace period, an invite for
# which seats somebody may still walk back into, and whether guest input is
# being withheld -- which is how it knows a game is in front.
session.cfg = Config()
session.input_held = True          # no game up, so nothing is held for one
# Everybody left their seat and may come back to it, which is what a claim
# means. Without one an empty seat is not waited for at all -- see below.
session.invite = Invite({0, 2, 3})
session.pads = Seats(4)
session.guests = {1: Guest(1, 1)}
session._now = lambda: session.pads.clock[0]
session._unplug_orphans()
check(sorted(session.pads.devices) == [0, 1, 2, 3],
      "nothing goes immediately: %r" % sorted(session.pads.devices))
session.pads.wait(pads.LINGER_SECONDS + 1)
session._unplug_orphans()
check(sorted(session.pads.devices) == [1],
      "only the seat somebody is on keeps one: %r" % sorted(session.pads.devices))

print("\nand one that is occupied keeps it")
session.pads = Seats(4)
session.guests = {0: Guest(0, 0), 2: Guest(2, 2)}
session._unplug_orphans()
session.pads.wait(pads.LINGER_SECONDS + 1)
session._unplug_orphans()
check(sorted(session.pads.devices) == [0, 2],
      "both occupied seats keep theirs: %r" % sorted(session.pads.devices))

print("\nwith nobody here at all, nothing is plugged in")
session.pads = Seats(4)
session.guests = {}
session._unplug_orphans()
session.pads.wait(pads.LINGER_SECONDS + 1)
session._unplug_orphans()
check(session.pads.devices == {}, "every device goes: %r" % session.pads.devices)

print("\nand it is safe with no pads at all")
session.pads = None
session._unplug_orphans()
check(True, "no session, no complaint")

print("\nthe sweep runs it")
source = open(os.path.join(ROOT, "fourthplayer", "session.py"),
              encoding="utf-8").read()
tick = source.split("await asyncio.sleep(SWEEP_INTERVAL)")[1].split("\n    async def ")[0]
check("_unplug_orphans()" in tick,
      "on the sweep that is already ticking, beside the ghost reaper")

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
