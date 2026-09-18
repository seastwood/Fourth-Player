"""Holding a key must not time out while somebody is still holding it.

The host lets go of everything at the desk after DEADMAN_SECONDS of silence,
and that is right: a tab that dies with Ctrl down must not leave Ctrl down on
somebody's computer. What was wrong was the measurement. Silence meant "no
input events", and holding a key is silence -- the browser's repeats are
dropped on purpose -- so walking forward with W sent one message and then
nothing, and two seconds later the host let go of a key still being held.

The page now sends an empty batch on a timer while it holds the desk. These
checks are about the two ends agreeing: the heartbeat has to be fast enough
that the dead-man switch never fires under somebody's hand, and the host has
to treat an empty batch as contact.
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


page = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()

print("the page has a heartbeat at all")
found = re.search(r"const DESK_ALIVE_MS = (\d+);", page)
check(found is not None, "app.js declares DESK_ALIVE_MS")
if found is None:
    print("FAILED")
    sys.exit(1)
beat_ms = int(found.group(1))

try:
    from fourthplayer import desk as desklib, deskwire
except Exception as exc:
    print("SKIPPED the host half: cannot import it here (%s)" % exc)
    print("FAILED" if fails else "PASSED")
    sys.exit(1 if fails else 0)

deadman_ms = desklib.DEADMAN_SECONDS * 1000

print("the two ends agree, which is the whole bug")
check(beat_ms < deadman_ms,
      "a heartbeat (%dms) arrives sooner than the switch fires (%dms)"
      % (beat_ms, deadman_ms))
# Three, not one: a heartbeat that only just fits leaves no room for a
# message that is late or lost, and the cost of another one is two bytes.
check(beat_ms * 3 <= deadman_ms,
      "at least three fit inside it, so losing one drops nothing")

print("an empty batch is a message, not an error")
try:
    empty = deskwire.decode("[]")
    check(empty == [], "the host reads [] as a batch of nothing")
except Exception as exc:
    check(False, "the host reads [] as a batch of nothing (raised %s)" % exc)

print("and a batch of nothing counts as being heard from")
# A real Desk needs uinput devices this machine may not have, and none of
# them are touched by an empty batch: it sets the clock and loops over no
# actions. So the real methods run against an instance built by hand.
desk = desklib.Desk.__new__(desklib.Desk)
clock = [100.0]
desk._clock = lambda: clock[0]
desk.last_seen = clock[0]
desk.held_keys = {30}                      # A, held down
desk.held_buttons = set()
released = []
desk.release_all = lambda: released.append(clock[0])

clock[0] += desklib.DEADMAN_SECONDS + 1
check(desk.sweep() is True,
      "silence past the deadline still lets go -- the switch works")
check(released, "and it really released")

released.clear()
desk.held_keys = {30}
desk.last_seen = clock[0]
for _ in range(20):                        # twenty heartbeats, no input
    clock[0] += beat_ms / 1000.0
    desk.apply([])
    if desk.sweep():
        break
check(not released,
      "but a page sending empty batches holds its key for as long as it likes")

print("FAILED" if fails else "PASSED")
sys.exit(1 if fails else 0)
