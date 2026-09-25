"""Which guests the sweep is allowed to hang up on.

Three limits, and getting them wrong is not a tidy-up problem: freeing a seat
takes the guest's controller with it and hands their reconnect a different
one.

The case this exists for, from the host's own log:

    08:47:38  the media track's frames were routed to the decoder
    08:47:38  taking the encoded frames off the media track
    08:47:38  painting with webgl
    08:47:41  Guest 2 had no video for 8s; freeing the slot

Switching to media-track drawing closes and reopens signalling as part of
starting up, so for a moment the socket is gone while ICE is up and the host
is still sending the picture. The sweep applied the closed-tab limit and hung
up three seconds into it. The transform was handed nothing, the page blamed
its own decoder, and it fell back to WebRTC -- reported as "media track just
black screens and fails over".

Read rather than run: importing the session pulls in GstWebRTC, which is not
present on the machines this suite runs on (it skipped on both). The rule is
small and the reasons for each branch are the valuable part, so they are
asserted where they are written.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
SRC = open(os.path.join(os.path.dirname(HERE), "fourthplayer", "session.py"),
           encoding="utf-8").read()

bad = 0


def check(ok, what):
    global bad
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        bad += 1


def constant(name):
    found = re.search(r"^%s = ([0-9.]+)" % name, SRC, re.M)
    return float(found.group(1)) if found else None


sweep = SRC[SRC.index("def _reap_ghosts"):]
sweep = sweep[:sweep.index("\n    # How many pointless")]

print("the three limits are told apart")
check("if alive and guest.socket is not None:" in sweep
      and "limit = HELD_SECONDS" in sweep,
      "a live peer AND a socket still open buys the long one")
# Not the socket alone. Written that way first, it held a seat for half an hour
# for a guest whose ICE had gone -- the network-switch case, and the one thing
# this sweep exists to clear. A page that is merely minimised keeps both: its
# connection is established and only its timers are frozen.
check("if guest.socket is not None:\n" not in sweep,
      "and the socket alone does not, because a dead connection can hold one")
check(re.search(r"alive = guest\.peer is not None and getattr\(\s*"
                r"guest\.peer, \"ice_ok\", False\)", sweep) is not None,
      "a peer with ICE up is recognised as still connected")
check("elif alive:\n                limit = seconds" in sweep,
      "and buys the ordinary deadline -- not the closed-tab one, which is "
      "what hung up on a guest three seconds into starting media track")
check("limit = min(seconds, LEFT_SECONDS)" in sweep,
      "while no socket and no ICE is somebody who closed the tab")

print("\nand they are ordered, so each means something")
held, ghost, left = (constant("HELD_SECONDS"), constant("GHOST_SECONDS"),
                     constant("LEFT_SECONDS"))
check(left is not None and ghost is not None and held is not None,
      "all three are named constants: %s, %s, %s" % (left, ghost, held))
check(left < ghost < held,
      "closed tab < ordinary < holding a socket open (%.0f < %.0f < %.0f)"
      % (left, ghost, held))
check(held >= 900,
      "and the long one is long enough to step away from a game and come "
      "back: %.0fs" % held)

print("\nand none of them is forever")
check("if now - guest.media_since > limit:" in sweep,
      "every branch still ends in a deadline, because webrtcbin sits at "
      "'completed' long after a guest has vanished")

print("\n%d FAILED" % bad if bad else "\nall ok")
sys.exit(1 if bad else 0)
