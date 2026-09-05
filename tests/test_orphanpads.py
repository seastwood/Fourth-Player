"""A controller nobody is sitting on should not exist.

Three of them survived on the console with no guests connected and nothing
reconnecting. Whatever the route there, the state is always wrong: a device
takes a player port in RetroArch, and Steam may hand a running game to it --
which is how an evening went, with a game bound to a pad nobody could press.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


try:
    from fourthplayer.session import LiveSession
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)


class Pad:
    pass


class Seats:
    def __init__(self, count):
        self.devices = {i: Pad() for i in range(count)}
        self.names = ["Fourth Player %d" % (i + 1) for i in range(count)]

    def live(self):
        return list(self.devices.items())

    def name_for(self, index):
        return self.names[index]

    def release(self, index):
        return self.devices.pop(index, None) is not None


class Guest:
    def __init__(self, slot, pad_index):
        self.slot = slot
        self.pad_index = pad_index
        self.label = "guest %d" % slot


print("a seat nobody is on loses its device")
session = LiveSession.__new__(LiveSession)
session.pads = Seats(4)
session.guests = {1: Guest(1, 1)}
session._unplug_orphans()
check(sorted(session.pads.devices) == [1],
      "only the seat somebody is on keeps one: %r" % sorted(session.pads.devices))

print("\nand one that is occupied keeps it")
session.pads = Seats(4)
session.guests = {0: Guest(0, 0), 2: Guest(2, 2)}
session._unplug_orphans()
check(sorted(session.pads.devices) == [0, 2],
      "both occupied seats keep theirs: %r" % sorted(session.pads.devices))

print("\nwith nobody here at all, nothing is plugged in")
session.pads = Seats(4)
session.guests = {}
session._unplug_orphans()
check(session.pads.devices == {}, "every device goes: %r" % session.pads.devices)

print("\nand it is safe with no pads at all")
session.pads = None
session._unplug_orphans()
check(True, "no session, no complaint")

print("\nthe sweep runs it")
source = open(os.path.join(ROOT, "fourthplayer", "session.py"),
              encoding="utf-8").read()
tick = source.split("await asyncio.sleep(SWEEP_INTERVAL)")[1].split("\n    async def ")[0]
check("_unplug_orphans()" in tick,
      "on the sweep that is already ticking, beside the ghost reaper")

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
