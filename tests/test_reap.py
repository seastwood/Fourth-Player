"""Which guests the sweep is allowed to hang up on.

Three limits, and getting them wrong is not a tidy-up problem: freeing a seat
takes the guest's controller away with it and hands their reconnect a
different one.

The case this exists for: a guest switching to media-track drawing closes and
reopens signalling as part of starting up, so for a moment their socket is
gone while their ICE is up and the host is still sending them the picture.
They were being hung up on three seconds into it -- the transform was handed
nothing, the page blamed its own decoder, and it fell back to WebRTC. From
the chair that is "media track just black screens and fails over".
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

try:
    from fourthplayer import session as sessionlib
except Exception as exc:                      # pragma: no cover
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)

bad = 0


def check(ok, what):
    global bad
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        bad += 1


class Peer:
    def __init__(self, ice_ok):
        self.ice_ok = ice_ok
        self.sent = {}


class Guest:
    def __init__(self, socket, peer, media_since):
        self.socket = socket
        self.peer = peer
        self.media_since = media_since
        self.label = "Guest 1"
        self.input_only = False
        self.last_input = 0.0

    def has_media(self, now=None):
        return False              # the whole point: they are quiet


class Room:
    """Just enough session to run the sweep."""

    _reap_ghosts = sessionlib.LiveSession._reap_ghosts

    def __init__(self, guest):
        self.guests = {0: guest}
        self.dropped = []

    def _now(self):
        return 1000.0

    def drop(self, slot, reason=""):
        self.dropped.append(slot)
        self.guests.pop(slot, None)


def swept(socket, ice_ok, quiet_for):
    guest = Guest(socket, Peer(ice_ok), 1000.0 - quiet_for)
    room = Room(guest)
    room._reap_ghosts()
    return bool(room.dropped)


print("a guest holding a socket open keeps their seat for a long time")
# A phone freezes a backgrounded tab, so everything has_media listens for
# stops within a second or two. The socket is a real connection a vanished
# guest cannot hold open.
check(not swept(socket=object(), ice_ok=True, quiet_for=60),
      "a minute of silence with the socket up is not gone")
check(not swept(socket=object(), ice_ok=True, quiet_for=600),
      "nor ten minutes -- stepping away from a game and coming back must not "
      "cost the seat")
check(swept(socket=object(), ice_ok=True,
            quiet_for=sessionlib.HELD_SECONDS + 1),
      "but it does end: %.0fs" % sessionlib.HELD_SECONDS)

print("\nand a peer that is plainly still connected is not a closed tab")
check(not swept(socket=None, ice_ok=True, quiet_for=10),
      "ten seconds quiet with ICE up does NOT free the seat -- this is the "
      "media-track startup, and hanging up here is what broke it")
check(swept(socket=None, ice_ok=True, quiet_for=sessionlib.GHOST_SECONDS + 1),
      "though it still ends at the ordinary deadline, because webrtcbin sits "
      "at 'completed' long after somebody has gone")

print("\nbut somebody who really did close the tab goes quickly")
check(swept(socket=None, ice_ok=False, quiet_for=sessionlib.LEFT_SECONDS + 1),
      "no socket and no ICE is gone, at %.0fs" % sessionlib.LEFT_SECONDS)
check(not swept(socket=None, ice_ok=False, quiet_for=1),
      "and not so quickly that a blink counts")

print("\n%d FAILED" % bad if bad else "\nall ok")
sys.exit(1 if bad else 0)
