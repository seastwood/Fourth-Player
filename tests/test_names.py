"""Guests with names, and getting in without the link.

Both change what a stranger can do, so both are checked here rather than by
looking at the screen: a name is drawn on somebody else's television, and the
link being optional is one secret instead of two.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from fourthplayer import invites
from fourthplayer.session import clean_name, NAME_MAX

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


print("a name is tidied before it goes anywhere")
check(clean_name("  Dave  ") == "Dave", "trimmed")
check(clean_name("Dave    the   Rave") == "Dave the Rave", "runs of space collapse")
check(clean_name("a" * 40) == "a" * NAME_MAX, "and it cannot be longer than a card")
check("\n" not in clean_name("Dave\nPlayer 1\nPlayer 2"),
      "newlines cannot be used to draw extra lines on the television")
check(clean_name("\x00\x07bad") == "bad", "control characters are dropped")
check(clean_name("") == "" and clean_name(None) == "",
      "and nothing given is nothing, so the slot number is used")

print("the link is required unless the host says otherwise")
now = 1000.0
inv = invites.Session(slots=2, duration=600, now=now)
token, pin = inv.clear_invite

try:
    inv.join("", pin, now=now, address="a")
    check(False, "a missing link is refused by default")
except invites.JoinError:
    check(True, "a missing link is refused by default")

slot, guest = inv.join(token, pin, now=now, address="b")
check(slot == 0, "the real link and PIN get in")

print("with it open, the PIN alone is enough")
inv2 = invites.Session(slots=2, duration=600, now=now)
token2, pin2 = inv2.clear_invite
slot, guest = inv2.join("", pin2, now=now, address="c", require_token=False)
check(slot == 0, "no link, right PIN, in")

try:
    inv2.join("", "000000", now=now, address="d", require_token=False)
    check(False, "but a wrong PIN is still a wrong PIN")
except invites.JoinError:
    check(True, "but a wrong PIN is still a wrong PIN")

print("and a wrong link never passes, even when one is not required")
# Otherwise a stale link would quietly work, which is worse than being told.
try:
    inv2.join("not-the-token", pin2, now=now, address="e", require_token=False)
    check(False, "a link that is offered is checked")
except invites.JoinError:
    check(True, "a link that is offered is checked")

print("guessing is still rate-limited when the link is optional")
inv3 = invites.Session(slots=2, duration=600, now=now)
_t, real = inv3.clear_invite
locked = False
for i in range(6):
    try:
        inv3.join("", "111111", now=now + i, address="f", require_token=False)
    except invites.LockedOut:
        locked = True
        break
    except invites.JoinError:
        pass
check(locked, "wrong PINs lock the caller out rather than being free tries")

print("the page is told which of the two it is, before anybody joins")
# It decides what the join page says about home screens, and whether the token
# is dropped from the address afterwards -- both of which have to be settled
# before a guest has joined anything.
import re
js = open(os.path.join(ROOT, "web", "app.js")).read()
server = open(os.path.join(ROOT, "fourthplayer", "server.py")).read()
check('route == "/mode"' in server, "the host answers what the mode is")
check('"require_link": self.cfg.require_link' in server,
      "and says which it is, from the setting rather than a guess")
check('fetch("/mode"' in js, "the page asks")
check("history.replaceState" in js and "linkRequired" in js,
      "and drops the invite from the address only when it is not needed")
check("if (linkRequired || !token) return;" in js,
      "never while the link is what gets people in")

print(("FAILED: %d" % len(fails)) if fails else "test_names: all ok")
sys.exit(1 if fails else 0)
