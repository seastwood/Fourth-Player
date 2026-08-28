"""The one gesture the owner can make while a game is running.

Kodi is behind a fullscreen emulator and the overlay is click-through on
purpose -- it goes around the window manager so xfwm4 never reconsiders the
game underneath it -- so without this there is no way to say yes to a request
to start a game without quitting the game the request is about.

Kept out of the overlay module so it can be tested without a display and
without GTK. The overlay draws it; this decides it.
"""

import os
import time


class Shoulders:
    """Both shoulders held down, on a controller that is in the room.

    Either pair counts: the two bumpers, or the two triggers. The prompt says
    "L + R", and on a pad in someone's hands that reads as either -- so
    listening for only one of them is a gesture that does nothing for half the
    people who make it. Triggers are also reported two different ways, as
    buttons on some pads and as axes on others, and both are accepted here.

    A second and a half, because this is read passively: the game sees these
    presses too, and anything shorter would be approving a stranger's game by
    playing your own. There is deliberately no gesture for refusing. Refusing
    is what happens when the timer runs out.

    Guests' own pads are excluded by name. They are ordinary uinput gamepads
    with ordinary shoulders, and a guest who could approve their own request
    would have made this whole setting decorative.
    """

    HOLD_SECONDS = 1.5
    OURS = "Fourth Player"
    # A wireless pad that sleeps and wakes comes back on a different event
    # node, and a device opened once and never looked at again is a gesture
    # that works exactly until the first time the controller reconnects.
    RESCAN_SECONDS = 5.0
    # How far a trigger reported as an axis has to travel to count as held.
    AXIS_ON = 0.6

    def __init__(self):
        self.devices = []
        self.down = set()          # (path, code) for buttons that are down
        self.axes = {}             # (path, code) -> how far, 0 to 1
        self.limits = {}           # (path, code) -> (min, range)
        self.since = None
        # Whether a pair is held *right now*, which is a different question
        # from how far through a hold we are. The caller arms on this going
        # false, because a hold already under way when the request arrived must
        # not count -- and at the first instant of a fresh hold, progress is
        # legitimately 0.0 too.
        self.holding = False
        self.scanned = 0.0
        self.ok = True
        try:
            import evdev
        except ImportError:
            self.ok = False
            self.evdev = None
        else:
            self.evdev = evdev
            e = evdev.ecodes
            self.key_pairs = ((e.BTN_TL, e.BTN_TR), (e.BTN_TL2, e.BTN_TR2))
            self.axis_pair = (e.ABS_Z, e.ABS_RZ)
            self.rescan()

    def _watchable(self, device):
        """Whether this pad can express the gesture at all."""
        caps = device.capabilities()
        keys = set(caps.get(self.evdev.ecodes.EV_KEY, []))
        for pair in self.key_pairs:
            if set(pair) <= keys:
                return True
        axes = {code for code, _ in caps.get(self.evdev.ecodes.EV_ABS, [])}
        return set(self.axis_pair) <= axes

    def rescan(self, now=None):
        if not self.evdev:
            return
        self.scanned = now if now is not None else time.monotonic()
        # Whatever was held belonged to the devices being replaced.
        self.down.clear()
        self.axes.clear()
        self.limits.clear()
        self.since = None
        self.holding = False

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
            for code, info in device.capabilities().get(
                    self.evdev.ecodes.EV_ABS, []):
                if code in self.axis_pair:
                    span = info.max - info.min
                    self.limits[(device.path, code)] = (info.min, span or 1)
            found.append(device)
        for old_device in self.devices:
            try:
                old_device.close()
            except OSError:
                pass
        self.devices = found

    def _engaged(self, path):
        """Whether either pair is fully held on this one controller."""
        for pair in self.key_pairs:
            if all((path, code) in self.down for code in pair):
                return True
        return all(self.axes.get((path, code), 0.0) >= self.AXIS_ON
                   for code in self.axis_pair) and any(
            (path, code) in self.limits for code in self.axis_pair)

    def progress(self, now):
        """How far through the hold we are, 0 to 1."""
        if not self.evdev:
            return 0.0
        # Never mid-hold: re-opening the devices forgets which buttons are
        # down, and doing that under someone's thumb would restart the count
        # they are halfway through.
        if not self.holding and now - self.scanned > self.RESCAN_SECONDS:
            self.rescan(now)

        e = self.evdev.ecodes
        watched = {code for pair in self.key_pairs for code in pair}
        for device in list(self.devices):
            try:
                # read() drains what is buffered and raises when there is
                # nothing, which is what "non-blocking" means here.
                for event in device.read():
                    if event.type == e.EV_KEY and event.code in watched:
                        key = (device.path, event.code)
                        if event.value:
                            self.down.add(key)
                        else:
                            self.down.discard(key)
                    elif event.type == e.EV_ABS and event.code in self.axis_pair:
                        low, span = self.limits.get(
                            (device.path, event.code), (0, 1))
                        self.axes[(device.path, event.code)] = (
                            (event.value - low) / span)
            except BlockingIOError:
                pass
            except OSError:                   # unplugged mid-read
                self.devices.remove(device)

        holding = any(self._engaged(device.path) for device in self.devices)
        self.holding = holding
        if not holding:
            self.since = None
            return 0.0
        if self.since is None:
            self.since = now
        return min(1.0, (now - self.since) / self.HOLD_SECONDS)

    def forget(self):
        self.since = None
