"""A seat somebody just left keeps its controller for a while.

Unplugging is not a tidy-up. It is something the machine on the other side
*notices*, and some of what it notices it does not undo: an emulator binds a
game's motion controls to a particular device, and a DualShock that goes away
and comes back is a different device to it. The buttons survive, because they
are re-matched by name. The gyroscope does not, and stays dead until the
emulator is restarted.

Reported exactly that way -- leave a stream, come back, and gyro is gone in
Suyu until Suyu is restarted.

The janitor that unplugs empty seats is right to exist: a pad with nobody
behind it takes a player port in RetroArch, and Steam may hand a running game
to it. It just ran the instant somebody stood up, and leaving a stream and
coming back is a thing people do constantly.

What matters here is both halves. A seat filled again inside the grace period
must keep the *same device* -- a new one would be the very unplug this
avoids. And a seat nobody returns to must still be cleared, or the fix is
just the old bug with a delay.
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


from fourthplayer import pads                                 # noqa: E402


class FakePad:
    """Enough of a device to be unplugged and to notice having been."""

    def __init__(self, name):
        self.name = name
        self.closed = False
        self.released = False

    def release_all(self):
        self.released = True

    def close(self):
        self.closed = True


def seats(count=2, now=None):
    s = pads.PadSet.__new__(pads.PadSet)
    s._now = now or (lambda: 0.0)
    s._label = "test"
    s._guide = False
    s._kind = pads.DEFAULT_KIND
    s._motion = True
    s._order = None
    s.names = ["pad %d" % (i + 1) for i in range(count)]
    s.pads = [None] * count
    s.kinds = [pads.DEFAULT_KIND] * count
    s._empty_at = {}
    return s


clock = [0.0]
seat = seats(now=lambda: clock[0])
seat.pads[0] = FakePad("one")
first = seat.pads[0]

print("-- an empty seat is not unplugged at once --")
check(seat.unplug_idle(set()) == [], "nothing goes on the first look")
check(seat.pads[0] is first, "the device is still there")
clock[0] = pads.LINGER_SECONDS - 1
check(seat.unplug_idle(set()) == [], "nor just before the grace period is up")
check(first.closed is False, "and it has not been closed behind our back")

print("\n-- somebody coming back keeps the very same device --")
# The whole point. A new device would be the unplug this exists to avoid, and
# the gyroscope would be just as dead.
clock[0] = pads.LINGER_SECONDS - 0.5
seat.unplug_idle({0})
clock[0] = pads.LINGER_SECONDS + 100
check(seat.unplug_idle({0}) == [],
      "still occupied, so still nothing happens however long it has been")
check(seat.pads[0] is first,
      "and it is the same object, not a replacement wearing its seat")

print("\n-- and the clock starts again when they leave again --")
# Not from when they first left, or a guest who came back for a minute would
# have their pad pulled out from under them a moment later.
clock[0] = pads.LINGER_SECONDS + 101
check(seat.unplug_idle(set()) == [], "leaving again starts a fresh grace")
clock[0] = pads.LINGER_SECONDS + 101 + pads.LINGER_SECONDS - 1
check(seat.unplug_idle(set()) == [], "which is the full period, not a remnant")

print("\n-- a seat nobody returns to is still cleared --")
# Or this is the old fault with a delay: a pad with nobody behind it takes a
# player port in RetroArch, and Steam may hand a running game to it.
clock[0] += 2
gone = seat.unplug_idle(set())
check([i for i, _p in gone] == [0], "it is unplugged: %r" % (gone,))
check(first.closed is True, "the device really is closed")
check(first.released is True,
      "and everything was let go of first -- a pad removed mid-press leaves "
      "the emulator holding whatever it held")
check(seat.pads[0] is None, "the seat is empty")
check(seat.unplug_idle(set()) == [], "and unplugging it again does nothing")

print("\n-- while a game is running, nothing is unplugged at all --")
# "I may step away from a game for half an hour then come back. I don't want
# to be disrupted." A grace period measured in minutes does not cover that,
# and it should not have to: the host already knows whether something
# game-like is in front, because it withholds guest frames when one is not.
clock[0] = 0.0
seat = seats(now=lambda: clock[0])
seat.pads[0] = FakePad("one")
held = seat.pads[0]
clock[0] = pads.LINGER_SECONDS * 20
check(seat.unplug_idle(set(), hold=True) == [],
      "twenty times the grace period, and it is still plugged in")
check(seat.pads[0] is held, "the very same device")

print("\n-- and the grace period starts when the game ends --")
# Not when they walked away. Otherwise closing the game would unplug the
# controller in the same instant, which is the interruption this avoids,
# moved rather than removed.
check(seat.unplug_idle(set()) == [],
      "the moment the game stops, nothing goes yet")
clock[0] += pads.LINGER_SECONDS - 1
check(seat.unplug_idle(set()) == [], "nor most of the way through")
clock[0] += 2
check([i for i, _p in seat.unplug_idle(set())] == [0],
      "and then it does, because an empty seat still takes a player port")

print("\n-- a held seat is not disturbed by the hold either --")
clock[0] = 0.0
seat = seats(now=lambda: clock[0])
seat.pads[0] = FakePad("one")
clock[0] = pads.LINGER_SECONDS * 5
check(seat.unplug_idle({0}, hold=True) == [],
      "somebody sitting on it, with a game running: nothing happens")
check(seat.pads[0] is not None, "and they keep their device")

print("\n-- seats are independent --")
clock[0] = 0.0
seat = seats(now=lambda: clock[0])
seat.pads[0], seat.pads[1] = FakePad("one"), FakePad("two")
seat.unplug_idle({1})                      # 0 is empty, 1 is held
clock[0] = pads.LINGER_SECONDS + 1
gone = seat.unplug_idle({1})
check([i for i, _p in gone] == [0],
      "the empty one goes and the held one stays: %r" % (gone,))
check(seat.pads[1] is not None, "seat two still has its device")

print("\n-- the grace period is long enough to be worth having --")
# Two minutes was the first guess and it was too short: the case is "I may
# step away for half an hour then come back", and the foreground check above
# does not cover a game that loses focus while somebody is out of the room.
#
# The asymmetry is what sets it. A pad kept for somebody who returns costs a
# player port nobody else was going to use; a pad unplugged from somebody who
# returns costs them their game's motion controls until they restart it.
check(pads.LINGER_SECONDS >= 1800,
      "at least half an hour: %r" % (pads.LINGER_SECONDS,))

print("\n-- and the caller may choose its own --")
clock[0] = 0.0
seat = seats(now=lambda: clock[0])
seat.pads[0] = FakePad("one")
clock[0] = 30
check(seat.unplug_idle(set(), after=60) == [], "not yet, at thirty of sixty")
clock[0] = 61
check([i for i, _p in seat.unplug_idle(set(), after=60)] == [0],
      "and then it goes, so a machine where ports are contended can say so")

print()
if fails:
    print("FAILURES: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_padlinger: all ok")
