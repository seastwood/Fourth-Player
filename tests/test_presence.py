"""Not asking for the authenticator code again after a blip.

Reported as: "it's kind of frustrating how frequently I have to input the
authenticator code -- it lost connection briefly and now I have to re-enter it".

There was already a grace period for this, thirty minutes of sudo-style
"you proved you were there recently". It never applied to the two things that
actually produce the retyping, because it was kept on the live connection:

  The slot is given away the moment somebody stops being heard from, so a
  guest who reconnects often reclaims a *different* slot -- which the code
  itself calls the normal outcome of a network switch. A different slot means
  no existing connection to inherit from, so a brand new one, so zero.

  And a host restart leaves no connections at all. Every restart cost
  everybody six digits, and from the far end a restart and a blip look exactly
  the same.

So the moment is kept with the token, which is the thing that survives both.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from fourthplayer import invites

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def a_session(now, slots=4):
    return invites.Session(slots=slots, duration=3600, now=now, pin="123456")


print("a code typed now is remembered against the token")
now = 1000.0
sess = a_session(now)
slot, token = sess.join(sess.clear_invite[0], "123456", now=now, address="1.2.3.4")
digest = invites.digest_of(token)
check(sess.proof_age_digest(digest, now) is None,
      "nothing proved yet, so no age")
sess.proved_digest(digest, now)
check(sess.proof_age_digest(digest, now) == 0.0, "proved just now")
check(sess.proof_age_digest(digest, now + 60) == 60.0,
      "and it ages: %s" % sess.proof_age_digest(digest, now + 60))
check(sess.guests[slot].proved_at == now,
      "the live record carries it too")

print()
print("it survives losing the slot, which is what a network switch does")
# The slot goes back the moment they stop being heard from, and somebody else
# may take it. Coming back means reclaiming whatever is free.
sess.release(slot, now=now + 5)
sess.join(sess.clear_invite[0], "123456", now=now + 6, address="9.9.9.9")   # someone else
again = sess.reclaim(token, now=now + 10)
check(again != slot, "they came back to a different slot (%s, was %s)"
      % (again, slot))
check(sess.guests[again].proved_at == now,
      "and the moment of their code came with them, rather than resetting to "
      "zero because the connection object is new")

print()
print("and it survives the host restarting, which looks identical from a phone")
now2 = 5000.0                       # a fresh monotonic clock, as after a restart
snapshot = sess.snapshot(now=now + 20)
back = invites.Session.restore(snapshot, now=now2)
check(back is not None, "the invite came back")
age_before = (now + 20) - now
age_after = back.proof_age_digest(digest, now2)
check(age_after is not None, "the proof came back too")
check(abs(age_after - age_before) < 2.0,
      "and is the same age across the restart: %.1fs before, %.1fs after"
      % (age_before, age_after if age_after is not None else -1))

print()
print("a restart neither extends a proof nor throws it away")
# Written as an age rather than a timestamp, so it cannot be laundered into a
# fresh one by bouncing the host -- that would make a restart a way past the
# code.
# Ten minutes, not an hour: an hour of downtime also runs the session out, so
# restore returns None and the check would pass for the wrong reason -- which
# is what the first version of it did.
old_snapshot = sess.snapshot(now=now + 20)
old_snapshot["saved_at"] = time.time() - 600
stale = invites.Session.restore(old_snapshot, now=now2)
check(stale is not None, "the session itself outlived the downtime")
aged = stale.proof_age_digest(digest, now2) if stale else None
check(aged is not None and 600 < aged < 700,
      "ten minutes of downtime ages the proof by ten minutes: %s" % aged)

print()
print("reclaiming after the proof has gone stale carries a stale one")
# The invite layer keeps the age honestly; whether it is still worth anything
# is the session's decision, against code_grace_minutes. Keeping those two
# apart is what stops the grace being silently different in two places.
check(sess.proof_age_digest(digest, now + 100000) > 99000,
      "the age keeps growing rather than being forgotten")

print()
print("somebody else's token gets nothing from it")
other_slot, other = sess.join(sess.clear_invite[0], "123456", now=now + 30,
                              address="5.5.5.5")
check(sess.proof_age_digest(invites.digest_of(other), now + 30) is None,
      "a different token has proved nothing, whoever else has")

print()
print("the session consults it, and bounds it by the grace")
source = open(os.path.join(ROOT, "fourthplayer", "session.py")).read()
check("_proof_from_token" in source, "there is a path that restores it")
check("code_grace_minutes" in source.split("_proof_from_token")[2][:900],
      "and it refuses a proof older than the grace, so this restores a "
      "moment that is still standing rather than creating one")
check("guest.token_digest = invites.digest_of(guest_token)" in source,
      "a returning connection is keyed to the token it returned on")
check(source.count("guest.token_digest = invites.digest_of") >= 3,
      "and so is every other way of making one, or the fix would apply to "
      "some arrivals and not others")

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
