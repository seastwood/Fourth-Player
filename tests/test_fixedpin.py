"""A PIN the owner sets, instead of six new digits every session.

Reading a fresh PIN off the television before anybody can join is the thing
this removes. What it must not remove is the reason the PIN existed: it is
still checked against a digest, still behind the same lockout, and it still
never appears in a log or in a status reply.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from fourthplayer import invites                                  # noqa: E402

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


print("a set PIN is the one guests type, every session")
live = invites.Session(slots=2, duration=600, now=0.0, pin="24680")
check(live.clear_invite[1] == "24680", "the session uses it as given")
check(live.fixed_pin == "24680", "and remembers that it was chosen")

print("\nand it survives a re-share, which only replaces the link")
token_before = live.clear_invite[0]
token_after, pin_after = live.reshare()
check(token_after != token_before, "the link is new, which is what re-sharing is")
check(pin_after == "24680",
      "the PIN the owner chose is not quietly replaced: %r" % pin_after)

print("\nwithout one, every session still gets its own")
a = invites.Session(slots=1, duration=60, now=0.0)
b = invites.Session(slots=1, duration=60, now=0.0)
check(a.clear_invite[1] != b.clear_invite[1],
      "two sessions do not share a random PIN")
check(len(a.clear_invite[1]) == invites.PIN_DIGITS,
      "and it is still six digits")
check(a.fixed_pin == "", "with nothing recorded as chosen")

print("\nit is still only ever stored as a digest")
kept = [name for name, value in vars(live).items()
        if isinstance(value, str) and value == "24680"]
check(kept == ["fixed_pin"] or kept == ["fixed_pin", "_pin"],
      "the clear PIN is held only where the owner has to be shown it: %s" % kept)
check(invites._matches("24680", live.pin_digest), "the digest is what is checked")
live.forget_clear()
check(live.clear_invite is None,
      "and it can still be dropped from memory after a restart")

print("\na PIN nobody could use is refused where it is set")
for bad, why in (("12", "too short"),
                 ("1234567890123", "too long"),
                 ("12ab", "not digits"),
                 ("0000", "one repeated digit")):
    check(invites.check_fixed_pin(bad), "%r is refused (%s)" % (bad, why))
for good in ("1234", "24680", "918273645"):
    check(invites.check_fixed_pin(good) is None, "%r is allowed" % good)
check(invites.check_fixed_pin("") is None and invites.check_fixed_pin(None) is None,
      "and empty means the random default, not an error")

try:
    invites.Session(slots=1, duration=60, now=0.0, pin="1")
    check(False, "a session cannot be opened with an unusable PIN")
except ValueError:
    check(True, "a session cannot be opened with an unusable PIN")

print("\nthe lockout is unchanged, and matters more to a PIN that never rotates")
guard = invites.Session(slots=1, duration=600, now=0.0, pin="24680")
kinds = []
for attempt in range(12):
    try:
        guard.join(guard.clear_invite[0], "00000", attempt, address="10.0.0.9")
        kinds.append("ADMITTED")
    except invites.JoinError as exc:
        kinds.append(type(exc).__name__)
check("ADMITTED" not in kinds, "no wrong PIN is ever admitted: %s" % set(kinds))
check(kinds.count("BadPin") <= 3 and "LockedOut" in kinds,
      "one address gets a few tries and is then shut out: %s" % kinds[:5])
# And the shutting out is per address, so the guesser cannot spend the whole
# invite's budget and lock the room out of a PIN that is now permanent.
check(guard.pin_attempts < invites.MAX_PIN_ATTEMPTS and not guard.destroyed,
      "without burning the invite for everybody else (attempts=%d, destroyed=%s)"
      % (guard.pin_attempts, guard.destroyed))
check(guard.join(guard.clear_invite[0], "24680", 0.0, address="10.0.0.7"),
      "so somebody else with the right PIN still gets in")

print("\nsetting one on a session that is already open takes effect there")
live2 = invites.Session(slots=2, duration=600, now=0.0)
was = live2.clear_invite[1]
token = live2.clear_invite[0]
live2.set_pin("13579")
check(live2.clear_invite[1] == "13579", "the open session now takes it")
check(live2.fixed_pin == "13579", "and records that it was chosen")
try:
    live2.join(token, was, 1.0, address="10.0.0.4")
    check(False, "the PIN it replaced stops working")
except invites.JoinError:
    check(True, "the PIN it replaced stops working")
check(live2.join(token, "13579", 2.0, address="10.0.0.5"),
      "and the new one lets somebody in on the same link")

print("\nclearing it goes back to a random PIN, without reusing the old one")
live3 = invites.Session(slots=1, duration=600, now=0.0, pin="24680")
live3.set_pin("")
check(live3.fixed_pin == "", "nothing is recorded as chosen any more")
check(live3.clear_invite[1] != "24680",
      "and the PIN that was set is not still the one in force")
check(len(live3.clear_invite[1]) == invites.PIN_DIGITS,
      "it is six random digits again")

print("\na restart does not quietly throw the set PIN away")
keeper = invites.Session(slots=2, duration=600, now=0.0, pin="24680")
snapshot = keeper.snapshot(0.0)
back = invites.Session.restore(snapshot, 0.0)
check(back is not None, "the session comes back")
check(back.fixed_pin == "", "and knows nothing about a set PIN on its own")
check(back.adopt_fixed_pin("24680"),
      "being told what was set, it recognises its own digest")
check(back.clear_invite is None or back.clear_invite[1] == "24680",
      "so the PIN is the one the owner chose")
token, pin = back.reshare()
check(pin == "24680",
      "and a re-share after a restart keeps it rather than inventing one: %r"
      % pin)

# If the owner changed it while the service was down, the stored digest wins
# until they re-share -- locking out everyone holding the old link would be a
# worse answer than a PIN that is briefly out of date.
other = invites.Session.restore(snapshot, 0.0)
check(other.adopt_fixed_pin("999111") is False,
      "a PIN changed while it was down is not mistaken for the old one")

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
