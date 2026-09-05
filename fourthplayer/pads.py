"""Kernel-level virtual gamepads, one per guest.

Each guest gets its own `/dev/uinput` device rather than sharing one, because
that is the entire difference between a second player and a second hand on the
same controller. RetroArch's udev joypad driver indexes pads by their event
node, so a guest's pad is indistinguishable from a pad plugged into the front
of the machine -- which is what lets kodi-retrobox's existing player picker
handle remote guests without knowing they exist.

The devices identify as an Xbox 360 pad (045e:028e) because every autoconfig
profile, SDL mapping and libretro core already knows that layout. Announcing
something novel would be honest and would also mean writing a mapping for it in
every one of those places.

Nothing here is privileged: the `uaccess` udev rule that JoyShockMapper already
installs grants the logged-in user write access to /dev/uinput, and this was
verified on the target machine before any of it was written.
"""

import time

from evdev import UInput, AbsInfo, ecodes as e

from . import protocol as P

VENDOR, PRODUCT, BUSTYPE, VERSION = 0x045E, 0x028E, 0x0003, 0x0110

# How long a pad may hear nothing before it is forced open. Short enough that a
# dropped guest does not run into a wall for long, and long enough to survive
# an ordinary network hiccup at the 8 ms send interval.
DEADMAN_SECONDS = 0.25

_BUTTON_MAP = [
    (P.BTN_A, e.BTN_A), (P.BTN_B, e.BTN_B), (P.BTN_X, e.BTN_X), (P.BTN_Y, e.BTN_Y),
    (P.BTN_LB, e.BTN_TL), (P.BTN_RB, e.BTN_TR),
    (P.BTN_BACK, e.BTN_SELECT), (P.BTN_START, e.BTN_START), (P.BTN_GUIDE, e.BTN_MODE),
    (P.BTN_LSTICK, e.BTN_THUMBL), (P.BTN_RSTICK, e.BTN_THUMBR),
]

# Sticks pass straight through; the trigger axes are rescaled because an Xbox
# pad reports them in 0..255 and the protocol carries them at full precision.
_STICK_MAP = [
    (P.AX_LX, e.ABS_X), (P.AX_LY, e.ABS_Y),
    (P.AX_RX, e.ABS_RX), (P.AX_RY, e.ABS_RY),
]
_TRIGGER_MAP = [(P.AX_LT, e.ABS_Z), (P.AX_RT, e.ABS_RZ)]

TRIGGER_MAX = 255


def button_codes(guide=True):
    """The evdev codes a guest's pad declares, in the order it declares them.

    One list, because two things read it and they must not drift: this, which
    builds the device, and retroarch.py, which writes the profile RetroArch
    matches against it. RetroArch numbers buttons by ascending evdev code over
    exactly the codes declared here, so a code removed from this list renumbers
    every button above it -- drop BTN_MODE and the thumb sticks slide from 9
    and 10 to 8 and 9. Left to two hand-written lists, that would show up as
    pressing the left stick opening the emulator's menu.
    """
    codes = [code for _, code in _BUTTON_MAP]
    if not guide:
        codes = [code for code in codes if code != e.BTN_MODE]
    return codes


def capabilities(guide=True):
    """Exactly a gamepad, and deliberately nothing else.

    This dict is the security boundary the whole design leans on: there is no
    EV_KEY entry for a letter, no EV_REL for a mouse. A guest cannot type on
    this machine because the device they are wired to cannot express a
    keystroke -- not because a check refuses to forward one.

    `guide` is the same argument one button further. The guide button is the
    Steam button: held in front of a running game it opens the overlay, and the
    overlay is a store with a saved card in it. Withholding a guest's frames
    while Steam's own interface is in front does not cover that, because the
    game still has the foreground while the overlay is up. So the button is not
    declared, and a device that does not declare it cannot press it -- there is
    nothing to filter and nothing to get wrong. It is also what RetroArch binds
    its menu to, which a guest has no more business opening.
    """
    stick = AbsInfo(value=0, min=-32768, max=32767, fuzz=16, flat=128, resolution=0)
    trigger = AbsInfo(value=0, min=0, max=TRIGGER_MAX, fuzz=0, flat=0, resolution=0)
    hat = AbsInfo(value=0, min=-1, max=1, fuzz=0, flat=0, resolution=0)
    return {
        e.EV_KEY: button_codes(guide),
        e.EV_ABS: [
            (e.ABS_X, stick), (e.ABS_Y, stick), (e.ABS_RX, stick), (e.ABS_RY, stick),
            (e.ABS_Z, trigger), (e.ABS_RZ, trigger),
            (e.ABS_HAT0X, hat), (e.ABS_HAT0Y, hat),
        ],
    }


def to_events(state, guide=True):
    """Translate a decoded frame into (type, code, value) triples.

    Split out from the device so it can be tested without a kernel: given a
    PadState this is a pure function, and `tests/test_pads.py` leans on that.

    A guide press on a pad that never declared one is not written. The device
    would ignore it -- uinput drops codes outside the declared set -- but a
    write that is known to go nowhere is better not made than relied upon to
    be discarded.
    """
    out = []
    allowed = set(button_codes(guide))
    for bit, code in _BUTTON_MAP:
        if code not in allowed:
            continue
        out.append((e.EV_KEY, code, 1 if state.pressed(bit) else 0))
    for axis, code in _STICK_MAP:
        out.append((e.EV_ABS, code, state.axis(axis)))
    for axis, code in _TRIGGER_MAP:
        scaled = int(max(0, state.axis(axis)) * TRIGGER_MAX / P.TRIGGER_MAX)
        out.append((e.EV_ABS, code, scaled))
    # The D-pad arrives as four booleans and leaves as two signed axes. Pressing
    # both sides of an axis at once is physically impossible on a real pad and
    # some cores handle it badly, so opposing presses cancel rather than
    # arbitrarily picking a winner.
    x = state.pressed(P.BTN_RIGHT) - state.pressed(P.BTN_LEFT)
    y = state.pressed(P.BTN_DOWN) - state.pressed(P.BTN_UP)
    out.append((e.EV_ABS, e.ABS_HAT0X, x))
    out.append((e.EV_ABS, e.ABS_HAT0Y, y))
    return out


class VirtualPad:
    """One guest's pad. Writes only what changed, and opens on silence."""

    # Whether this pad offers a guide button at all. Class-level so a pad that
    # writes its own __init__ -- which is how the tests make one that records
    # instead of opening uinput -- still answers for it. False is the right
    # default twice over: it matches the configuration default, and a pad that
    # wrongly claims no guide button sends nothing, while one that wrongly
    # claims to have it hands guests the Steam menu.
    guide = False

    def __init__(self, name, now=None, guide=True):
        self.name = name
        self.guide = guide
        self._ui = UInput(capabilities(guide), name=name, vendor=VENDOR,
                          product=PRODUCT, version=VERSION, bustype=BUSTYPE)
        self._last = {}
        # Per sender, not per pad. One counter was enough while a pad had one
        # guest; several on one pad interleave their counters, and each
        # one's frames then look stale beside the other's -- so both would go
        # dead. Keyed by whoever is sending, their newest frame is kept here
        # and the pad writes the merge of them.
        self._senders = {}               # key -> [seq, PadState]
        self.last_seen = (now or time.monotonic)()
        self.released = True

    @property
    def path(self):
        """Where the kernel put this pad, or a placeholder if it will not say.

        evdev locates the node by scanning /dev/input after the device is
        created, and that lookup can come back empty on a busy machine. It is
        only ever used for logging and for the roster, so a pad that cannot
        name itself must not be allowed to take a whole session down -- which
        is exactly what it did before this, from inside a log line.
        """
        device = getattr(self._ui, "device", None)
        return getattr(device, "path", None) or "(node not resolved)"

    def apply(self, state, now=None, sender=None):
        """Apply a frame from one sender. False if it was stale and ignored.

        `sender` names who sent it, so that a pad being shared can tell two
        people's frames apart. Left out, everything shares one name and this
        behaves exactly as it did when a pad only ever had one guest.
        """
        key = "solo" if sender is None else sender
        stamp = (now or time.monotonic)()
        known = self._senders.get(key)
        if known is not None and not P.is_newer(state.seq, known[0]):
            # Still proof of life: an out-of-order frame means the guest is
            # talking, even though this particular one is not the newest truth.
            self.last_seen = stamp
            return False
        self.last_seen = stamp
        if state.release_all:
            # Only this sender lets go. Somebody else on the same pad may still
            # be holding a direction, and taking their hand off the controller
            # because a third person put theirs down is exactly the bug that
            # sharing a pad has to avoid.
            self._senders.pop(key, None)
            if not self._senders:
                self.release_all()
            else:
                self._write(to_events(self._merged(), self.guide))
            return True
        self._senders[key] = [state.seq, state]
        self._write(to_events(self._merged(), self.guide))
        self.released = False
        return True

    def forget(self, sender):
        """Drop a sender from a shared pad, and stop holding what they held."""
        if self._senders.pop(sender, None) is None:
            return
        if not self._senders:
            self.release_all()
        else:
            self._write(to_events(self._merged(), self.guide))

    def _merged(self):
        """One pad state from everybody currently on this pad.

        Buttons are or-ed and each axis takes whichever value is furthest from
        centre. Both rules exist so that somebody sitting still cannot cancel
        somebody playing: taking the newest frame instead would mean a
        passenger's idle stick, arriving between two of the driver's frames,
        straightened the car out.
        """
        states = [entry[1] for entry in self._senders.values()]
        if len(states) == 1:
            return states[0]
        buttons = 0
        axes = [0] * len(P.PadState().axes)
        for state in states:
            buttons |= state.buttons
            for i, value in enumerate(state.axes):
                if abs(value) > abs(axes[i]):
                    axes[i] = value
        return P.PadState(seq=0, buttons=buttons, axes=axes)

    def adopt_new_sender(self, sender=None):
        """Forget the sequence number, because a fresh browser restarts at zero.

        This is what made a reconnecting guest able to watch but not play. The
        pad remembers the last sequence it accepted; a reloaded page starts its
        counter at 0 again; and `is_newer(0, 41000)` is correctly False, so
        **every frame from the returning guest was discarded as stale** and the
        pad never moved again for the rest of the session. Silent, total, and
        indistinguishable from a broken controller.

        Called whenever a peer attaches to this pad, which is the only moment
        the sender can have changed.
        """
        if sender is None:
            self._senders.clear()
            self.release_all()
            return
        # Only the one who came back. Clearing the lot would take the pad away
        # from everybody else sharing it, in the middle of their game, because
        # somebody else reloaded a page.
        self.forget(sender)

    def release_all(self):
        """Centre every axis and lift every button.

        Called on a dead-man timeout, on a kick, and on shutdown. It must be
        safe to call repeatedly, because all three can happen at once.
        """
        if self.released:
            return
        self._senders.clear()
        self._write(to_events(P.PadState(), self.guide))
        self.released = True

    def _write(self, events):
        changed = False
        for etype, code, value in events:
            key = (etype, code)
            if self._last.get(key) == value:
                continue
            self._last[key] = value
            self._ui.write(etype, code, value)
            changed = True
        if changed:
            self._ui.syn()

    def close(self):
        try:
            self.release_all()
        finally:
            self._ui.close()


class PadSet:
    """The pads for one session, and the dead-man sweep over them.

    A seat here is not a device. The devices are made when somebody sits in
    them and unplugged when they leave, because a virtual pad that exists is a
    virtual pad the emulator gives a player port to -- whether or not anybody
    is holding it.

    That cost was invisible until somebody tried to mix real controllers with
    guests. Four seats meant four pads sitting on ports one to four from the
    moment a session opened, so a real controller plugged in afterwards was
    autoconfigured into port five, which no game here uses. It had been given
    player one by the picker and it drove nothing:

        Remote player 1..4 configured in ports 1..4    (nobody holding them)
        Xbox One S Controller configured in port 5     (the one that claimed P1)

    An empty seat costs nothing now, so the ports go to whoever is actually
    playing, in whatever mixture.
    """

    def __init__(self, count, label="Fourth Player", now=None, guide=True):
        self._now = now or time.monotonic
        self._label = label
        # Whether these pads have a guide button at all. See capabilities():
        # it is the Steam button and RetroArch's menu button, and a guest has
        # no business opening either.
        self._guide = guide
        # The names are fixed for the life of the session even though the
        # devices come and go: RetroArch's per-device profiles are written
        # against them, and the picker reads them out of pad-names.json.
        self.names = [f"{label} {i + 1}" for i in range(count)]
        self.pads = [None] * count

    def __len__(self):
        return len(self.pads)

    def name_for(self, index):
        """What the seat is called, whether or not anybody is sitting in it."""
        return self.names[index]

    def __getitem__(self, index):
        """The device for a seat, made on first use.

        Indexing is what the input path does, so arriving here means somebody
        is about to drive this seat and it needs to exist.
        """
        pad = self.pads[index]
        if pad is None:
            pad = VirtualPad(self.names[index], now=self._now,
                             guide=self._guide)
            self.pads[index] = pad
        return pad

    def existing(self, index):
        """The device for a seat if there is one, and None if there is not.

        The counterpart to __getitem__, which makes one. Anything that is
        tidying up rather than driving wants this: asking for a device in
        order to let go of it is how a controller gets plugged in on the way
        out, which Steam notices and a running game does not survive.
        """
        if not 0 <= index < len(self.pads):
            return None
        return self.pads[index]

    def live(self):
        """The seats that currently have a device, as (index, pad)."""
        return [(i, pad) for i, pad in enumerate(self.pads) if pad is not None]

    def release(self, index):
        """Unplug a seat's device. Harmless if there is not one."""
        pad = self.pads[index]
        if pad is None:
            return False
        self.pads[index] = None
        # Let go before unplugging: a pad removed mid-press otherwise leaves
        # the emulator holding whatever it held.
        try:
            pad.release_all()
        except OSError:
            pass
        pad.close()
        return True

    def sweep(self, timeout=DEADMAN_SECONDS):
        """Release any pad that has gone quiet. Returns the ones it opened.

        This is not a nicety. Input arrives as snapshots, so a guest whose
        connection dies mid-press leaves their pad holding whatever it held --
        a stuck direction walks the character into a wall until someone
        notices. Silence is the only signal that works here, because a
        disconnected browser cannot send an apology.
        """
        stamp = self._now()
        opened = []
        for _index, pad in self.live():
            if not pad.released and stamp - pad.last_seen > timeout:
                pad.release_all()
                opened.append(pad)
        return opened

    def close(self):
        for _index, pad in self.live():
            pad.close()
        self.pads = [None] * len(self.pads)
