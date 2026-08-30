"""Changing which player you are, without stopping the game.

A game that is running has bound its player ports to devices and will not
revisit that until it restarts. So somebody who joins halfway through, or a
second person arriving after one player claimed, could only be given controls
by stopping the game and starting it again -- which is what was reported.

Moving them onto the pad that is already player 2 does it instantly, because
that pad is already player 2. This is that move.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from fourthplayer.session import GuestConnection, LiveSession

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


class Pad:
    def __init__(self, name):
        self.name, self.path = name, "/dev/input/" + name
        self.released = 0
        self.adopted = 0

    def release_all(self):
        self.released += 1

    def adopt_new_sender(self):
        self.adopted += 1


class FakePads(list):
    """A stand-in for PadSet: seats with names, whose devices come and go.

    Modelled on the real thing rather than on a plain list, because the real
    thing stopped being one: an empty seat has no device, so reading a name
    must not conjure one, and letting a seat go has to be something a caller
    can do.
    """

    def __init__(self, pads):
        super().__init__(pads)
        self.released = []

    @property
    def names(self):
        return [p.name for p in self]

    def name_for(self, index):
        return self[index].name

    def release(self, index):
        self.released.append(index)
        return True


def session_with(n):
    live = LiveSession.__new__(LiveSession)
    live.pads = FakePads([Pad("pad%d" % i) for i in range(n)])
    live.guests = {}
    live.on_notice = lambda m: live.notices.append(m)
    live.notices = []
    live.publish_pad_names = lambda: None
    return live


def guest_on(live, slot, name):
    g = GuestConnection.__new__(GuestConnection)
    g.session, g.slot, g.name = live, slot, name
    g.pad_index = slot
    g.label = name
    live.guests[slot] = g
    return g


print("a guest can take an empty pad")
live = session_with(4)
a = guest_on(live, 0, "Ann")
check(a.pad.name == "pad0", "they start on the pad matching their slot")
live.set_pad(a, 2)
check(a.pad_index == 2 and a.pad.name == "pad2", "and can move to another")
check(live.pads[0].released == 1,
      "letting go of the one they left, so nothing is stuck down on it")
check(live.pads[2].adopted == 1,
      "and the new one starts a fresh sequence rather than rejecting them")

print("taking a pad somebody else is on swaps the two")
live = session_with(4)
a = guest_on(live, 0, "Ann")
b = guest_on(live, 1, "Bob")
live.set_pad(a, 1)
check(a.pad_index == 1, "the one who asked gets the pad they asked for")
check(b.pad_index == 0, "and the other takes the one just vacated: %d" % b.pad_index)
check(live.pads[0].released >= 1 and live.pads[1].released >= 1,
      "both are let go of first, so neither is left holding a direction")

print("and nobody ends up sharing a pad")
seats = [g.pad_index for g in live.guests.values()]
check(len(set(seats)) == len(seats), "one guest per pad: %s" % seats)

print("a pad that does not exist is refused, counted the way a person counts")
live = session_with(3)
a = guest_on(live, 0, "Ann")
for bad in (3, 99, -1):
    try:
        live.set_pad(a, bad)
        check(False, "pad %s is refused" % bad)
    except ValueError as exc:
        # Not "player": which player a pad is belongs to the running game,
        # not to its position in this list. Controllers are what this counts.
        check("controller" in str(exc), "pad %s is refused: %s" % (bad, exc))
check(a.pad_index == 0, "and they stay where they were")

print("asking for the pad you are already on does nothing at all")
live = session_with(3)
a = guest_on(live, 0, "Ann")
live.set_pad(a, 0)
check(live.pads[0].released == 0, "no release, so no interruption to play")

print("everybody is told who is where")
live = session_with(3)
a = guest_on(live, 0, "Ann")
guest_on(live, 1, "Bob")
live.set_pad(a, 2)
told = [m for m in live.notices if m.get("t") == "pads"]
check(told and told[-1]["who"].get("2") == "Ann",
      "the pad map goes out: %s" % (told[-1]["who"] if told else None))
check(told and told[-1]["count"] == 3, "with how many there are")

print(("FAILED: %d" % len(fails)) if fails else "test_seats: all ok")
sys.exit(1 if fails else 0)
