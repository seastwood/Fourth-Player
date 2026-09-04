"""The keystroke that opens chat while a game is running.

Kodi is the obvious home for a chat window and the wrong thing to depend on:
kodi-retrobox closes Kodi to run a game. So a Kodi keymap gives you a shortcut
that works in the menus and does nothing at the one moment somebody wants it
-- reported exactly that way, and it is not a bug in the keymap. There is no
Kodi to press the key at.

This watches the keyboards directly, the way approve.py watches the pads, so
it answers whether Kodi is up, behind a game, or not running.

Everything here runs against a stub evdev: no keyboard, no display, no root.
What is held still is the shape of the thing rather than the plumbing -- once
per press and not while held, both sides of each modifier, one keyboard at a
time, and guests' own devices never watched.
"""
import importlib.machinery
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


class Codes:
    EV_KEY = 1
    KEY_C = 46
    KEY_A = 30
    KEY_LEFTCTRL = 29
    KEY_RIGHTCTRL = 97
    KEY_LEFTSHIFT = 42
    KEY_RIGHTSHIFT = 54


class Event:
    def __init__(self, code, value):
        self.type = Codes.EV_KEY
        self.code = code
        self.value = value


class Device:
    def __init__(self, path, name, keys, queue=None):
        self.path = path
        self.name = name
        # A real descriptor, because the watcher sets it non-blocking -- and a
        # device it cannot set is one it drops, which is the right behaviour
        # and made every test below fail silently until the stub was honest.
        self.fd = os.open(os.devnull, os.O_RDONLY)
        self._keys = keys
        self.queue = list(queue or [])
        self.closed = False

    def capabilities(self):
        return {Codes.EV_KEY: list(self._keys)}

    def read(self):
        out, self.queue = self.queue, []
        return out

    def close(self):
        self.closed = True


KEYBOARD = [Codes.KEY_C, Codes.KEY_A, Codes.KEY_LEFTCTRL, Codes.KEY_LEFTSHIFT,
            Codes.KEY_RIGHTSHIFT]


class FakeEvdev:
    ecodes = Codes

    def __init__(self, devices):
        self.devices = {d.path: d for d in devices}

    def list_devices(self):
        return list(self.devices)

    def InputDevice(self, path):
        return self.devices[path]


ldr = importlib.machinery.SourceFileLoader(
    "chatkey", os.path.join(ROOT, "fourthplayer", "chatkey.py"))
chatkey = importlib.util.module_from_spec(
    importlib.util.spec_from_loader("chatkey", ldr))
ldr.exec_module(chatkey)


def watcher(devices):
    key = chatkey.ChatKey.__new__(chatkey.ChatKey)
    key.devices = []
    key.down = set()
    key.scanned = 0.0
    key.fired = False
    key.ok = True
    key.evdev = FakeEvdev(devices)
    e = Codes
    key.key = e.KEY_C
    key.modifiers = ((e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL),
                     (e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT))
    key.rescan(now=100.0)
    return key


print("which devices are watched")
pad = Device("/dev/input/event9", "Xbox Wireless Controller", [304, 305])
board = Device("/dev/input/event3", "USB Keyboard", KEYBOARD)
guest = Device("/dev/input/event20", "Fourth Player 2", KEYBOARD)
key = watcher([pad, board, guest])
check([d.path for d in key.devices] == ["/dev/input/event3"],
      "keyboards, and only keyboards: a pad declares none of these codes")
check(guest.closed and pad.closed,
      "and a guest's own device is never watched -- opening the composer "
      "grabs the host's keyboard, and a guest reaching that is a guest "
      "reaching into the room")

print("once per press, not while held")
board.queue = [Event(Codes.KEY_LEFTCTRL, 1), Event(Codes.KEY_LEFTSHIFT, 1),
               Event(Codes.KEY_C, 1)]
check(key.pressed(now=101.0) is True, "the combination opens it")
board.queue = [Event(Codes.KEY_C, 2)]          # autorepeat while held
check(key.pressed(now=101.1) is False,
      "and holding it does not open it again -- a window that opens sixty "
      "times a second is a machine somebody reboots")
board.queue = [Event(Codes.KEY_C, 0)]
check(key.pressed(now=101.2) is False, "letting go opens nothing")
board.queue = [Event(Codes.KEY_C, 1)]
check(key.pressed(now=101.3) is True, "pressing it again does")

print("what does not count")
key = watcher([board])
board.queue = [Event(Codes.KEY_C, 1)]
check(key.pressed(now=102.0) is False, "the letter on its own is not the chord")
board.queue = [Event(Codes.KEY_LEFTCTRL, 1)]
check(key.pressed(now=102.1) is False, "nor ctrl and the letter")
board.queue = [Event(Codes.KEY_RIGHTSHIFT, 1)]
check(key.pressed(now=102.2) is True,
      "the right-hand shift counts as much as the left: a keyboard has two of "
      "each, and a shortcut that only works with one is reported as broken")

print("one keyboard at a time")
second = Device("/dev/input/event4", "Other Keyboard", KEYBOARD)
key = watcher([board, second])
board.queue = [Event(Codes.KEY_LEFTCTRL, 1), Event(Codes.KEY_LEFTSHIFT, 1)]
second.queue = [Event(Codes.KEY_C, 1)]
check(key.pressed(now=103.0) is False,
      "ctrl on one keyboard and C on another is not a chord anybody typed")

print("a keyboard that comes and goes")
key = watcher([board])
check(key.scanned == 100.0, "the scan is remembered")
key.pressed(now=100.0 + chatkey.ChatKey.RESCAN_SECONDS + 1)
check(key.scanned > 100.0,
      "and repeated, because a wireless keyboard that sleeps comes back on a "
      "different node")

print("and a machine with no evdev at all")
key = chatkey.ChatKey.__new__(chatkey.ChatKey)
key.evdev = None
key.ok = False
key.devices = []
check(key.pressed(now=104.0) is False,
      "answers no rather than raising: the overlay must never come down over "
      "a missing import")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("all good")
