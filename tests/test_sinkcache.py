"""Drawing a dropdown must not stall the picture.

The setup page refreshes every five seconds, and every refresh asked the
machine to enumerate its audio outputs -- twice, once for the list and once
for the suggestion drawn from the same list. Starting a GstDeviceMonitor is
not a local operation: it drives WASAPI on the very machine that is capturing
the sound it is enumerating, and it ran on the event loop, so the pause was
every guest's signalling and data channel as well as the audio device's.

Reported as opening the setup page making the stream freeze repeatedly, which
is the shape exactly: a stall every five seconds for as long as it was open.
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


from fourthplayer import micsink

asked = []


class FakeProps:
    def get_string(self, key):
        return "strid-1" if key == "device.strid" else None


class FakeDevice:
    def get_properties(self):
        return FakeProps()

    def get_display_name(self):
        return "Speakers (VB-Audio Virtual Cable)"


class FakeMonitor:
    def __init__(self):
        self.stopped = False

    def add_filter(self, *a):
        pass

    def start(self):
        asked.append("start")

    def get_devices(self):
        return [FakeDevice()]

    def stop(self):
        self.stopped = True
        asked.append("stop")


made = []


class FakeGst:
    class DeviceMonitor:
        @staticmethod
        def new():
            m = FakeMonitor()
            made.append(m)
            return m


clock = [100.0]
micsink.forget_sinks()

print("the machine is asked once, not once per caller")
first = micsink.sinks(FakeGst, now=clock[0])
check(first and first[0][1] == "strid-1", "the first ask reads the devices")
check(asked.count("start") == 1, "and it really asked the machine")

micsink.sinks(FakeGst, now=clock[0] + 1)
micsink.sinks(FakeGst, now=clock[0] + 2)
check(asked.count("start") == 1,
      "asks inside the window are answered from what is remembered")

print("and every monitor it starts is stopped again")
check(all(m.stopped for m in made),
      "nothing is left holding a notification client open")

print("but it is not remembered for ever")
micsink.sinks(FakeGst, now=clock[0] + micsink.SINKS_TTL + 1)
check(asked.count("start") == 2, "past the window it asks again")

print("a caller cannot be handed the list to edit")
got = micsink.sinks(FakeGst, now=clock[0] + micsink.SINKS_TTL + 2)
got.append(("junk", "junk"))
again = micsink.sinks(FakeGst, now=clock[0] + micsink.SINKS_TTL + 3)
check(("junk", "junk") not in again,
      "what is remembered survives a caller mutating what it was given")

print("and something that changed can say so")
micsink.forget_sinks()
micsink.sinks(FakeGst, now=clock[0] + micsink.SINKS_TTL + 4)
check(asked.count("start") == 3, "forget_sinks makes the next ask a real one")

print("the window is long enough to be worth having")
check(micsink.SINKS_TTL >= 10,
      "%.0fs covers several refreshes of a page left open" % micsink.SINKS_TTL)

print("a monitor that throws still gets stopped")


class AngryMonitor(FakeMonitor):
    def get_devices(self):
        raise RuntimeError("no devices today")


class AngryGst:
    class DeviceMonitor:
        @staticmethod
        def new():
            m = AngryMonitor()
            made.append(m)
            return m


micsink.forget_sinks()
out = micsink.sinks(AngryGst, now=clock[0] + 100)
check(out == [], "a failure is an empty list, not an exception into the page")
check(made[-1].stopped, "and the monitor is stopped on the way out")

print("FAILED" if fails else "PASSED")
sys.exit(1 if fails else 0)
