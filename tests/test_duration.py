"""Sessions that run for a chosen number of minutes, or for no fixed time.

An unlimited session is an infinite deadline rather than a flag, so that every
comparison asking whether it is still alive keeps working without knowing about
it. The one thing infinity must never do is reach a wire: JSON has no
representation for it, and a browser refuses to parse the one Python writes. So
the checks here are that the deadline behaves, and that what leaves the process
is null.
"""
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from fourthplayer import invites

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


now = 1000.0

print("a session with a deadline still has one")
timed = invites.Session(slots=3, duration=1800, now=now)
check(not timed.unlimited, "an ordinary session is not unlimited")
check(timed.remaining(now) == 1800, "and reports the time it was given")
check(timed.alive(now + 1799) and not timed.alive(now + 1801),
      "and stops being alive when it runs out")

print("a session with no deadline never runs out")
forever = invites.Session(slots=3, duration=math.inf, now=now)
check(forever.unlimited, "it says so")
check(forever.alive(now + 60 * 60 * 24 * 365), "still alive a year later")
check(math.isinf(forever.remaining(now)), "with an infinite amount left")
try:
    forever.check_alive(now + 10 ** 9)
    check(True, "and never refuses a guest for being late")
except Exception as exc:
    check(False, "and never refuses a guest for being late: %r" % exc)

print("infinity does not reach anything that has to parse it")
snapshot = forever.snapshot(now)
check(snapshot["expires_in"] is None, "the saved state writes null, not Infinity")
text = json.dumps(snapshot)
check("Infinity" not in text, "so the file is JSON anything can read")
check(json.loads(text)["expires_in"] is None, "and reads back as null")

print("and it survives a restart as itself")
snapshot["saved_at"] = time.time() - 3600      # an hour ago
back = invites.Session.restore(snapshot, now=5000.0)
check(back is not None, "an unlimited session comes back")
check(back.unlimited, "still with no deadline, an hour later")

timed_snapshot = timed.snapshot(now)
timed_snapshot["saved_at"] = time.time() - 10
back = invites.Session.restore(timed_snapshot, now=5000.0)
check(back is not None and not back.unlimited, "a timed one comes back timed")
check(abs(back.remaining(5000.0) - 1790) < 2,
      "having lost the ten seconds it was away")

timed_snapshot["saved_at"] = time.time() - 100000
check(invites.Session.restore(timed_snapshot, now=5000.0) is None,
      "and one that ran out while away does not come back at all")

print(("FAILED: %d" % len(fails)) if fails else "test_duration: all ok")
sys.exit(1 if fails else 0)
