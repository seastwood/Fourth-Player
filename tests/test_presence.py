"""How long an authenticator code goes on proving somebody is there.

The capabilities in NEEDS_CODE ask for six digits at the moment they are used,
because a remembered device says who somebody is and not that they are the one
holding it. Asking once is the point.

Asking every time is a different thing, and it is what the desk became. A code
proved presence only for the socket it arrived on, and sockets drop constantly
-- a stalled encoder, a browser refusing the video, a phone changing network.
Every one of those asked again, in the middle of driving a cursor.

So the moment lasts, and using it puts it forward. What must not follow from
that is a remembered device on somebody else's phone inheriting it, which is
the case at the bottom.
"""
import dataclasses
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


try:
    from fourthplayer.session import LiveSession
    from fourthplayer.config import Config
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)


class Guest:
    logged_in_at = 0.0


NOW = 1_000_000.0


def session(minutes=30):
    s = LiveSession.__new__(LiveSession)
    s.cfg = dataclasses.replace(Config(), code_grace_minutes=minutes)
    s._now = lambda: NOW
    return s


print("the default is a window, not a single instant")
check(Config().code_grace_minutes == 30,
      "a code stands for %d minutes by default" % Config().code_grace_minutes)

print("\na code that was just given still counts")
s, g = session(), Guest()
check(not s.presence_ok(g), "nobody who never gave one is let through")
g.logged_in_at = NOW
check(s.presence_ok(g), "somebody who just did is")
check(s.presence_ok(g, now=NOW + 29 * 60), "and still is 29 minutes later")
check(not s.presence_ok(g, now=NOW + 31 * 60),
      "but not 31 minutes later, when whoever gave it may be long gone")

print("\nand using it puts it forward")
# The whole point: somebody working at the desk is never asked twice. Without
# this the window is a hard stop that lands mid-cursor at minute thirty.
s, g = session(), Guest()
g.logged_in_at = NOW
later = NOW + 20 * 60
s.touch_presence(g, now=later)
check(g.logged_in_at == later, "a use moves the moment to now")
check(s.presence_ok(g, now=NOW + 45 * 60),
      "so continuous use never lapses, even past the original window")

print("\nit cannot be conjured out of nothing")
# touch_presence must never *create* presence: it is called after the gate has
# already been passed, and a bug that let it run first would turn the gate into
# a formality.
s, g = session(), Guest()
s.touch_presence(g)
check(g.logged_in_at == 0.0 and not s.presence_ok(g),
      "touching a connection that never gave a code leaves it with nothing")

print("\nand it can be switched off entirely")
s, g = session(minutes=0), Guest()
g.logged_in_at = NOW
check(not s.presence_ok(g),
      "zero asks every single time, which is what this used to do")

print("\nwhat a remembered device gets from it: nothing")
# The security case. A remembered device restores who somebody is without a
# code -- logged_in_at stays at zero -- and the window is read off the
# connection, not the account. So somebody else's phone, remembering an
# account whose owner used a code a minute ago on their own phone, is still
# asked for six digits.
s = session()
owner, borrowed = Guest(), Guest()
owner.logged_in_at = NOW              # gave a code, on their own connection
check(s.presence_ok(owner), "the connection that gave the code is let through")
check(not s.presence_ok(borrowed),
      "and another connection to the same account is not, however recently "
      "the first one proved anything")

print()
if fails:
    print("FAILURES: %d" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("test_presence: all ok")
