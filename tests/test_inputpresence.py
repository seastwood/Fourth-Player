"""Whether a seat with no picture looks like somebody who is there.

A second controller on one machine gets an input-only seat: a pad, a name and
a slot, and deliberately no video, because the picture is already on the screen
in front of them. Presence for that seat can only be its pad frames arriving.

It was its ICE state instead, and that is a state an input-only peer never
reaches. webrtcbin derives ice-connection-state from its RTP transceivers, and
this peer has none -- so it stays at `new`, notifies nobody, `ice_ok` stays
False, and has_media answered False at its first line however busily the
controller was being used. The seat was swept GHOST_SECONDS after it was made:
a second player who joined, worked, and disappeared just as a game got going.
Observed on 2026-09-12 as "Guest 2 had no video for 25s; freeing the slot"
against a seat whose whole purpose is having no video.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


try:
    from fourthplayer.session import GuestConnection, SILENCE_SECONDS
except Exception as exc:                      # no GStreamer here, or no deps
    print("SKIPPED the host half: %s" % exc)
    sys.exit(0)


class FakePeer:
    def __init__(self, ice_ok=False, media=True):
        self.ice_ok = ice_ok
        self.media = media


def guest(*, input_only, ice_ok, last_input, socket=object(), media=True):
    """A GuestConnection with only what has_media reads, and nothing else."""
    g = GuestConnection.__new__(GuestConnection)
    g.input_only = input_only
    g.peer = FakePeer(ice_ok=ice_ok, media=media)
    g.last_input = last_input
    g.socket = socket
    return g


now = time.monotonic()
fresh = now - (SILENCE_SECONDS / 2)
stale = now - (SILENCE_SECONDS * 4)

# -- the seat this is all for ---------------------------------------------
check(guest(input_only=True, ice_ok=False, last_input=fresh,
            media=False).has_media(now),
      "an input-only seat sending pad frames is present, with no ICE state")

check(not guest(input_only=True, ice_ok=False, last_input=stale,
                media=False).has_media(now),
      "and is absent once its frames stop, so it is still mortal")

check(not guest(input_only=True, ice_ok=False, last_input=stale,
                socket=None, media=False).has_media(now),
      "absent too when its socket has gone with its frames")

# A seat that has only just arrived has sent nothing yet, and holds a socket.
check(guest(input_only=True, ice_ok=False, last_input=None,
            media=False).has_media(now),
      "a seat that just arrived is given the benefit of its open socket")

# -- the ordinary seat must be unchanged ----------------------------------
check(not guest(input_only=False, ice_ok=False, last_input=fresh).has_media(now),
      "a guest with video still needs ICE up, however recent their frames")

check(guest(input_only=False, ice_ok=True, last_input=fresh).has_media(now),
      "and is present with ICE up and frames arriving")

check(not guest(input_only=False, ice_ok=True, last_input=stale).has_media(now),
      "but not with ICE up and nothing heard for a while")

# No peer at all is nobody, whichever kind of seat it is.
for only in (True, False):
    g = guest(input_only=only, ice_ok=True, last_input=fresh)
    g.peer = None
    check(not g.has_media(now),
          "a seat with no peer is absent (input_only=%s)" % only)

print()
if fails:
    print("test_inputpresence: %d FAILED" % len(fails))
    sys.exit(1)
print("test_inputpresence: all ok")
