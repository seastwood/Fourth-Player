"""The one keystroke that opens chat, whatever is on the screen.

Kodi is the obvious place to put a chat window and the wrong place to depend
on: kodi-retrobox *closes* Kodi to run a game. So the shortcut that mattered
-- the one somebody presses while playing -- was the one that could never
work, because there was no Kodi to receive it.

This reads the keyboards directly instead, the way `approve.py` reads the
pads, so it answers whether Kodi is up, behind a game, or not running at all.
Passive: it never grabs anything and never stops a keystroke reaching whatever
is in front. The grab happens later and only while somebody is typing a
message, which is the one moment a game must not also be hearing it.

Kept out of the overlay so it can be tested without a display, without GTK and
without a keyboard.
"""

import os
import time


class ChatKey:
    """Ctrl+Shift+C, on any keyboard in the room.

    Both sides of each modifier count, because a keyboard has two of each and
    a shortcut that only works with the left one is a shortcut somebody
    reports as broken.

    A combination rather than a single key, and the reason is written down in
    the keymap beside it: F8 was tried first and F8 is Kodi's screenshot key.
    Here the constraint is different and points the same way -- this key is
    read while a game is being played, and a bare letter would open the chat
    every time somebody typed that letter into a game.
    """

    # A wireless keyboard that sleeps comes back on a different event node,
    # and a device opened once and never looked at again is a shortcut that
    # works until the first time somebody's keyboard reconnects.
    RESCAN_SECONDS = 5.0
    # Guests' own devices are never watched. They are uinput devices with
    # ordinary capabilities, and a guest opening the host's chat composer --
    # which grabs the host's keyboard -- would be a guest reaching into the
    # room.
    OURS = "Fourth Player"

    def __init__(self):
        self.devices = []
        self.down = set()          # (path, code) currently held
        self.scanned = 0.0
        self.fired = False         # held since the last time this said yes
        self.taken = []            # devices grabbed while somebody is typing
        self.ok = True
        try:
            import evdev
        except ImportError:
            self.ok = False
            self.evdev = None
            self.key = self.modifiers = ()
            return
        self.evdev = evdev
        e = evdev.ecodes
        self.key = e.KEY_C
        # Either control and either shift.
        self.modifiers = ((e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL),
                          (e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT))
        self.rescan()

    def _watchable(self, device):
        """Whether this is a keyboard, rather than a pad or a mouse."""
        keys = set(device.capabilities().get(self.evdev.ecodes.EV_KEY, []))
        # The letter and both modifiers. A pad declares BTN_* codes and none
        # of these, so this is also what keeps controllers out.
        return self.key in keys and all(
            any(code in keys for code in pair) for pair in self.modifiers)

    def rescan(self, now=None):
        if not self.evdev:
            return
        if self.taken:
            # Not while somebody is typing: closing these devices would hand
            # the keyboard back to the game mid-message, and the next letter
            # would land in it.
            return
        self.scanned = now if now is not None else time.monotonic()
        self.down.clear()
        self.fired = False
        found = []
        for path in self.evdev.list_devices():
            try:
                device = self.evdev.InputDevice(path)
            except OSError:
                continue
            if device.name.startswith(self.OURS) or not self._watchable(device):
                device.close()
                continue
            try:
                os.set_blocking(device.fd, False)
            except OSError:
                device.close()
                continue
            found.append(device)
        for old in self.devices:
            try:
                old.close()
            except OSError:
                pass
        self.devices = found

    def _read(self):
        """Take whatever has happened since the last look."""
        for device in list(self.devices):
            try:
                for event in device.read():
                    if event.type != self.evdev.ecodes.EV_KEY:
                        continue
                    where = (device.path, event.code)
                    if event.value:          # 1 press, 2 autorepeat
                        self.down.add(where)
                    else:
                        self.down.discard(where)
            except BlockingIOError:
                continue
            except OSError:
                # Unplugged mid-read. The rescan below picks up what is left.
                self.scanned = 0.0

    def held(self, path):
        """Whether the whole combination is down on this one keyboard.

        On one keyboard, deliberately: ctrl on somebody's laptop and C on the
        console's keyboard is not a chord anybody typed.
        """
        if (path, self.key) not in self.down:
            return False
        return all(any((path, code) in self.down for code in pair)
                   for pair in self.modifiers)

    def hold(self):
        """Take the keyboards away from everything else, and say if it worked.

        EVIOCGRAB, not an X grab, and the difference is the whole point.
        RetroArch's input driver here is udev: it reads these devices directly
        and never asks X anything, so an X keyboard grab is invisible to it.
        The first version of this used one, and typing a message into a game
        pressed the game's hotkeys -- `h` is RetroArch's reset, so a word with
        an h in it restarted somebody's game.

        A kernel grab stops every other reader: the game, the window manager,
        Kodi if it is up. Which also means this must be given back reliably --
        and is, three ways: on close, on the idle timeout, and by the kernel
        itself if this process dies, because the grab lives on the open file
        and dies with it.
        """
        if not self.evdev:
            return False
        held = []
        for device in self.devices:
            try:
                device.grab()
            except (OSError, IOError):
                continue
            held.append(device)
        self.taken = held
        # All of them or none is not the test: a machine with two keyboards
        # and one grabbed is still a machine where the game hears the other.
        # It is worth saying, and it is not worth refusing over.
        return bool(held) and len(held) == len(self.devices)

    def let_go(self):
        """Give the keyboards back. Safe to call when holding nothing."""
        for device in self.taken:
            try:
                device.ungrab()
            except (OSError, IOError):
                pass
        self.taken = []

    def typed(self):
        """Key presses since the last look, as (code, pressed) pairs.

        Raw codes rather than characters: what a code means depends on the
        layout somebody chose, and the overlay has GDK to ask about that. This
        module's job is to read the wire.
        """
        out = []
        for device in list(self.devices):
            try:
                for event in device.read():
                    if event.type == self.evdev.ecodes.EV_KEY:
                        out.append((event.code, event.value))
                        where = (device.path, event.code)
                        if event.value:
                            self.down.add(where)
                        else:
                            self.down.discard(where)
            except BlockingIOError:
                continue
            except OSError:
                self.scanned = 0.0
        return out

    def pressed(self, now=None):
        """True once per press of the combination. False the rest of the time.

        Once per press rather than while held: this opens a window, and a
        window that opens sixty times a second is a machine somebody has to
        reboot.
        """
        if not self.evdev:
            return False
        now = time.monotonic() if now is None else now
        if now - self.scanned >= self.RESCAN_SECONDS:
            self.rescan(now)
        self._read()
        engaged = any(self.held(device.path) for device in self.devices)
        if engaged and not self.fired:
            self.fired = True
            return True
        if not engaged:
            self.fired = False
        return False
