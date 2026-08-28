"""The one gesture the owner can make while a game is running.

Kodi is behind a fullscreen emulator and the overlay is click-through on
purpose -- it goes around the window manager so xfwm4 never reconsiders the
game underneath it -- so without this there is no way to say yes to a request
to start a game without quitting the game the request is about.

Kept out of the overlay module so it can be tested without a display and
without GTK. The overlay draws it; this decides it.
"""

import os

class Shoulders:
    """Both bumpers, held, on a controller that is in the room.

    The one gesture the owner can make while a game is running. Kodi is behind
    a fullscreen emulator and this window is click-through on purpose -- it
    goes around the window manager so that xfwm4 never reconsiders the game
    underneath it -- so without this there is no way to say yes to a request
    without quitting the game the request is about.

    Both bumpers together for a second and a half, because this is read
    passively: the game sees these presses too, and anything shorter would be
    approving a stranger's game by playing your own. There is deliberately no
    gesture for refusing. Refusing is what happens when the timer runs out.

    Guests' own pads are excluded by name. They are ordinary uinput gamepads
    with ordinary bumpers, and a guest who could approve their own request
    would have made this whole setting decorative.
    """

    HOLD_SECONDS = 1.5
    OURS = "Fourth Player"

    def __init__(self):
        self.devices = []
        self.down = set()
        self.since = None
        # Whether both bumpers are down *right now*, which is a different
        # question from how far through a hold we are. The caller arms on this
        # going false, because a hold that was already under way when the
        # request arrived must not count -- and at the first instant of a fresh
        # hold, progress is legitimately 0.0 too.
        self.holding = False
        self.ok = True
        try:
            import evdev
        except ImportError:
            self.ok = False
            self.evdev = None
        else:
            self.evdev = evdev
            self.rescan()

    def rescan(self):
        if not self.evdev:
            return
        wanted = {self.evdev.ecodes.BTN_TL, self.evdev.ecodes.BTN_TR}
        found = []
        for path in self.evdev.list_devices():
            try:
                device = self.evdev.InputDevice(path)
            except OSError:
                continue
            keys = set(device.capabilities().get(self.evdev.ecodes.EV_KEY, []))
            if not wanted <= keys or device.name.startswith(self.OURS):
                device.close()
                continue
            try:
                os.set_blocking(device.fd, False)
            except OSError:
                device.close()
                continue
            found.append(device)
        for old_device in self.devices:
            try:
                old_device.close()
            except OSError:
                pass
        self.devices = found

    def progress(self, now):
        """How far through the hold we are, 0 to 1."""
        if not self.evdev:
            return 0.0
        for device in list(self.devices):
            try:
                # read() drains what is buffered and raises when there is
                # nothing, which is what "non-blocking" means here.
                for event in device.read():
                    if event.type != self.evdev.ecodes.EV_KEY:
                        continue
                    if event.code in (self.evdev.ecodes.BTN_TL,
                                      self.evdev.ecodes.BTN_TR):
                        key = (device.path, event.code)
                        if event.value:
                            self.down.add(key)
                        else:
                            self.down.discard(key)
            except BlockingIOError:
                pass
            except OSError:                   # unplugged mid-read
                self.devices.remove(device)

        # Both bumpers, on the same controller.
        holding = any(
            (path, self.evdev.ecodes.BTN_TL) in self.down
            and (path, self.evdev.ecodes.BTN_TR) in self.down
            for path in {p for p, _ in self.down})
        self.holding = holding
        if not holding:
            self.since = None
            return 0.0
        if self.since is None:
            self.since = now
        return min(1.0, (now - self.since) / self.HOLD_SECONDS)

    def forget(self):
        self.since = None
