"""Sharing one controller, for the games meant to be played that way.

Advance Wars is passed round a sofa: everybody taking a turn is player one.
Swapping seats in the web UI can do that, but only one at a time -- this is
about both of them driving the same port at once.

The hard part is not the wiring. A pad kept one sequence number, so two senders
interleaving their counters made each other's frames look stale and both went
dead; and the last frame won, so somebody sitting still cancelled somebody
playing.
"""
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from fourthplayer import pads as padlib, protocol as P            # noqa: E402

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


class FakePad(padlib.VirtualPad):
    """A pad that records what it would write instead of opening uinput."""

    def __init__(self):
        self.name = "test"
        self._last = {}
        self._senders = {}
        self.last_seen = 0.0
        self.released = True
        self.writes = []
        self.motions = []

    # motion as well as events: a pad carries a guest's device motion
    # alongside the buttons, and this stub is what stands in for the device.
    # Recorded rather than ignored, because a shared pad has a rule about it --
    # attitude does not add up, so whoever is actually moving wins.
    def _write(self, events, motion=None):
        self.writes.append(events)
        self.motions.append(motion)

    @property
    def sent(self):
        return self._merged()


UP, DOWN = 12, 13          # two buttons, whichever they are
LEFT_X = 0

print("two senders do not make each other look stale")
pad = FakePad()
ok_a = pad.apply(P.PadState(seq=1, buttons=1 << UP), sender="a")
ok_b = pad.apply(P.PadState(seq=1, buttons=1 << DOWN), sender="b")
ok_a2 = pad.apply(P.PadState(seq=2, buttons=1 << UP), sender="a")
check(ok_a and ok_b and ok_a2,
      "every frame is accepted: a=%s b=%s a again=%s" % (ok_a, ok_b, ok_a2))

print("\nand what the game sees is the two of them together")
merged = pad.sent
check(merged.pressed(UP) and merged.pressed(DOWN),
      "both buttons are held, not just the newest sender's")

print("\nsomebody sitting still does not cancel somebody playing")
pad = FakePad()
pad.apply(P.PadState(seq=1, axes=[30000, 0, 0, 0, 0, 0]), sender="driver")
pad.apply(P.PadState(seq=1, axes=[0, 0, 0, 0, 0, 0]), sender="passenger")
check(pad.sent.axis(LEFT_X) == 30000,
      "the stick stays where the driver put it, got %d" % pad.sent.axis(LEFT_X))
pad.apply(P.PadState(seq=2, axes=[-9000, 0, 0, 0, 0, 0]), sender="passenger")
check(pad.sent.axis(LEFT_X) == 30000,
      "and the further push wins over the smaller one, got %d"
      % pad.sent.axis(LEFT_X))

print("\none of them letting go leaves the other holding what they held")
pad = FakePad()
pad.apply(P.PadState(seq=1, buttons=1 << UP), sender="a")
pad.apply(P.PadState(seq=1, buttons=1 << DOWN), sender="b")
pad.apply(P.PadState(seq=2, release_all=True), sender="b")
check(pad.sent.pressed(UP), "a is still pressing up")
check(not pad.sent.pressed(DOWN), "and b is no longer pressing down")
check(not pad.released, "the pad is still live for the one still on it")

print("\nthe last one out releases the pad")
pad.apply(P.PadState(seq=3, release_all=True), sender="a")
check(pad.released, "with nobody left, everything is let go")

print("\na guest reloading does not take the pad from the other")
pad = FakePad()
pad.apply(P.PadState(seq=900, buttons=1 << UP), sender="a")
pad.apply(P.PadState(seq=900, buttons=1 << DOWN), sender="b")
pad.adopt_new_sender("b")
check(pad.sent.pressed(UP), "a keeps playing through b's reload")
check(pad.apply(P.PadState(seq=0, buttons=1 << DOWN), sender="b"),
      "and b's counter starting again at zero is accepted, not called stale")

print("\nand it is not limited to two of them")
# The wording in Kodi says "players", not "two people", so the code had better
# mean it. Nothing here counts senders -- the merge walks whoever is present.
pad = FakePad()
for i, who in enumerate(("a", "b", "c", "d", "e")):
    accepted = pad.apply(P.PadState(seq=i + 1, buttons=1 << (i + 8)), sender=who)
    check(accepted, "sender %s is accepted alongside the rest" % who)
merged = pad.sent
check(all(merged.pressed(8 + i) for i in range(5)),
      "all five are held at once, not just the last two")
pad.apply(P.PadState(seq=99, axes=[12000, 0, 0, 0, 0, 0]), sender="a")
pad.apply(P.PadState(seq=99, axes=[-31000, 0, 0, 0, 0, 0]), sender="c")
pad.apply(P.PadState(seq=99, axes=[0, 0, 0, 0, 0, 0]), sender="e")
check(pad.sent.axis(0) == -31000,
      "the furthest push wins across all of them, got %d" % pad.sent.axis(0))
pad.forget("c")
check(pad.sent.axis(0) == 12000,
      "and dropping one falls back to the next furthest, got %d" % pad.sent.axis(0))
check(not pad.released, "with three still on it, the pad stays live")

print("\nalone on a pad, nothing changed")
pad = FakePad()
check(pad.apply(P.PadState(seq=5, buttons=1 << UP)), "a frame with no sender applies")
check(not pad.apply(P.PadState(seq=4, buttons=0)),
      "and an older one from the same sender is still refused")

# ---- and the seat rule that decides whether sharing happens at all ----
from fourthplayer.session import GuestConnection, LiveSession        # noqa: E402


class SeatPad:
    def __init__(self, name):
        self.name, self.path = name, "/dev/input/" + name

    def release_all(self):
        pass

    def forget(self, sender=None):
        pass

    def adopt_new_sender(self, sender=None):
        pass


class SeatPads(list):
    @property
    def names(self):
        return [p.name for p in self]

    def name_for(self, index):
        return self[index].name

    def release(self, index):
        return True


def session(share):
    live = LiveSession.__new__(LiveSession)
    live.pads = SeatPads([SeatPad("pad%d" % i) for i in range(4)])
    live.guests = {}
    live.notices = []
    live.on_notice = live.notices.append
    live.publish_pad_names = lambda: None
    live.cfg = types.SimpleNamespace(share_pads=share)
    return live


def guest(live, slot, name):
    g = GuestConnection.__new__(GuestConnection)
    g.session, g.slot, g.name, g.label = live, slot, name, name
    g.pad_index = slot
    # Changing seats now tells everybody who is in the room, and that list
    # says how long each person has been here. The rest of what it reads has
    # a default on the class; a joining time cannot sensibly have one.
    g.joined_at = time.monotonic()
    g.media_since = time.monotonic()
    live.guests[slot] = g
    return g


print("\nwith sharing off, taking a held controller swaps the two of you")
live = session(False)
ann, bob = guest(live, 0, "Ann"), guest(live, 1, "Bob")
live.set_pad(bob, 0)
check(bob.pad_index == 0, "Bob got the controller he asked for")
check(ann.pad_index == 1, "and Ann was moved off it, got %d" % ann.pad_index)

print("\nwith sharing on, nobody is moved and both drive it")
live = session(True)
ann, bob = guest(live, 0, "Ann"), guest(live, 1, "Bob")
live.set_pad(bob, 0)
check(bob.pad_index == 0 and ann.pad_index == 0,
      "both are on controller 1: Ann=%d Bob=%d" % (ann.pad_index, bob.pad_index))
check(ann.pad is bob.pad, "and it is literally the same device")

print("\nattitude does not add up, so whoever is moving wins")
# Buttons are or-ed and axes take whichever is furthest from centre, both so
# that somebody sitting still cannot cancel somebody playing. Motion cannot
# work either way: two people are not both holding the phone, and averaging
# two attitudes describes a position neither of them is in. So the same rule
# in spirit -- the one actually turning.
pad = FakePad()
still = [2, 0, 0, 0, 0, 1000]          # a phone on a table: sensor noise
turning = [900, -400, 60, 100, 0, 980]
pad.apply(P.PadState(seq=1, motion=still), sender="a")
pad.apply(P.PadState(seq=1, motion=turning), sender="b")
check(pad.motions[-1] == turning,
      "the hand that is moving is the one carried: %r" % (pad.motions[-1],))
# And in the other arrival order, or this passes by luck.
pad = FakePad()
pad.apply(P.PadState(seq=1, motion=turning), sender="b")
pad.apply(P.PadState(seq=1, motion=still), sender="a")
check(pad.motions[-1] == turning,
      "whichever order they arrive in: %r" % (pad.motions[-1],))

print("\nand a sender with no motion does not erase one who has it")
pad = FakePad()
pad.apply(P.PadState(seq=1, motion=turning), sender="b")
pad.apply(P.PadState(seq=1), sender="a")
check(pad.motions[-1] == turning,
      "somebody on a controller with no gyroscope joining a shared pad must "
      "not blank the attitude of the person tilting a phone: %r"
      % (pad.motions[-1],))

print("\nwith nobody sending any, none is carried")
pad = FakePad()
pad.apply(P.PadState(seq=1), sender="a")
pad.apply(P.PadState(seq=1), sender="b")
check(pad.motions[-1] is None,
      "None rather than zeros -- a pad held still reads as no rotation and "
      "one gravity, so 'no sensor' has to be tellable from 'not moving': %r"
      % (pad.motions[-1],))

print("\nand the panel names everybody on it, not just the last one looked at")
who = live.pad_state()["who"]
check(who.get("0") == "Ann, Bob",
      "a shared controller lists both: %r" % who.get("0"))
live2 = session(False)
solo = guest(live2, 0, "Ann")
check(live2.pad_state()["who"].get("0") == "Ann",
      "and one guest still reads as just their name")

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
