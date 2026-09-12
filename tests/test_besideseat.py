"""A second controller seated on the strength of the seat beside it.

Two people on one sofa share one browser page, and the second controller asks
for a seat of its own. It used to ask with the PIN, the same way the first one
did, and for the person this was built for that could not work: `sessionPin`
lives in memory and is set only when somebody types it at the gate, so a page
that came back on its saved guest token -- a home screen icon, a reconnect,
the host restarting underneath it -- had an empty one. Tapping "Add player"
sent no PIN, the host said "That link or PIN is not valid", and there was
nothing to be done about it from the sofa.

So the credential is the seat they already hold. It cannot be stale, and a
guest who is already admitted gains nothing by being admitted twice. What
must still hold: a token that was never issued, belongs elsewhere, or has been
burned is refused, and the slot limit is not negotiable.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


from fourthplayer import invites

# -- the host half ---------------------------------------------------------

live = invites.Session(slots=3, duration=600, now=0.0, pin="13579")
token, pin = live.clear_invite[0], live.clear_invite[1]

slot0, cred0 = live.join(token, pin, now=1.0, address="10.0.0.1", label="Sis")
check(slot0 == 0, "the first guest takes slot 0")

slot1, cred1 = live.join_beside(cred0, now=2.0, address="10.0.0.1",
                                label="Sis 2")
check(slot1 == 1, "the controller beside it takes the next slot")
check(cred1 and cred1 != cred0, "it gets a credential of its own, not a copy")
check(live.guest_for(cred1, now=3.0).slot == 1,
      "and that credential resolves to the new seat")

# The whole point: no PIN was involved anywhere above.
try:
    live.join_beside(cred0, now=4.0, address="10.0.0.1", label="Sis 3")
    check(True, "a third controller is seated without the PIN too")
except invites.JoinError as exc:
    check(False, "a third controller should have been seated (%s)" % exc)

# -- what must still be refused --------------------------------------------

try:
    live.join_beside("not-a-real-token", now=5.0, address="10.0.0.1")
    check(False, "an invented token was accepted")
except invites.UnknownGuest:
    check(True, "an invented token is refused")
except invites.JoinError as exc:
    check(False, "an invented token raised the wrong thing (%r)" % exc)

# Slots are full now (3 of 3), and being vouched for does not conjure one.
try:
    live.join_beside(cred0, now=6.0, address="10.0.0.1")
    check(False, "a fourth seat appeared out of a session with three")
except invites.SessionFull:
    check(True, "the slot limit still applies to a vouched-for seat")
except invites.JoinError as exc:
    check(False, "a full session raised the wrong thing (%r)" % exc)

# A seat that was taken away cannot vouch for anything.
other = invites.Session(slots=2, duration=600, now=0.0, pin="24680")
otoken, opin = other.clear_invite[0], other.clear_invite[1]
_, ocred = other.join(otoken, opin, now=1.0, address="10.0.0.9")
other.kick(0)
try:
    other.join_beside(ocred, now=3.0, address="10.0.0.9")
    check(False, "a kicked seat was still able to vouch for another")
except invites.UnknownGuest:
    check(True, "a kicked seat cannot vouch for another")
except invites.JoinError as exc:
    check(False, "a kicked seat raised the wrong thing (%r)" % exc)

# A token from another session is not a credential here.
try:
    live.join_beside(ocred, now=7.0, address="10.0.0.1")
    check(False, "another session's token was accepted")
except invites.JoinError:
    check(True, "another session's token is refused")

# -- an empty PIN is not a guess -------------------------------------------
#
# This is the damage the old behaviour did. Every tap of "Add player" sent an
# empty PIN, each one counted as a wrong guess, and the tenth destroyed the
# session for everybody in it -- host included -- with nothing in the log and
# "That link or PIN is not valid" the only thing on screen. It is a denial of
# service as well as a bug: ten empty strings from anyone who can reach the
# page, with no knowledge of the PIN at all.

quiet = invites.Session(slots=2, duration=600, now=0.0, pin="13579")
qtoken, qpin = quiet.clear_invite[0], quiet.clear_invite[1]
for i in range(30):
    try:
        quiet.join(qtoken, "", now=1.0 + i, address="10.0.0.%d" % (i % 200))
    except invites.JoinError:
        pass
check(not quiet.destroyed,
      "thirty empty PINs do not destroy the invite")
check(quiet.pin_attempts == 0,
      "and none of them counts against the tally")
kept, kcred = quiet.join(qtoken, qpin, now=100.0, address="10.1.1.1")
check(kept == 0, "a real guest can still join afterwards")
check(quiet.join_beside(kcred, now=101.0, address="10.1.1.1")[0] == 1,
      "and still seat a second controller")

# The protection the tally exists for has to survive all of that.
loud = invites.Session(slots=2, duration=600, now=0.0, pin="13579")
ltoken = loud.clear_invite[0]
for i in range(12):
    try:
        loud.join(ltoken, "99999", now=1.0 + i, address="10.5.0.%d" % i)
    except invites.JoinError:
        pass
check(loud.destroyed,
      "a wrong PIN that was actually guessed still destroys the invite")
check(loud.pin_attempts >= 10, "after the full tally of attempts")

# -- the page half ---------------------------------------------------------

app = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
extra = app[app.index("class ExtraPlayer"):]
extra = extra[:extra.index("function dropExtra")]

check("beside: guestToken" in extra,
      "the page offers the seat it holds as the credential")
check(re.search(r"guestToken\s*\n?\s*\?\s*\{\s*beside", extra) is not None,
      "and prefers it over the PIN when it has one")
check("pin: sessionPin" in extra,
      "the PIN is still the fallback for a page with no seat yet")
check(extra.index("beside: guestToken") < extra.index("pin: sessionPin"),
      "in that order -- the seat first, the PIN only if there is none")

print()
if fails:
    print("test_besideseat: %d FAILED" % len(fails))
    sys.exit(1)
print("test_besideseat: all ok")
