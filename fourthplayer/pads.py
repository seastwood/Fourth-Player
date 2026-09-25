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

import logging
import time

from .codes import ecodes as e
from .virtual import UInput, AbsInfo

log = logging.getLogger("fourthplayer.pads")

from . import protocol as P

VENDOR, PRODUCT, BUSTYPE, VERSION = 0x045E, 0x028E, 0x0003, 0x0110

# What kind of pad a guest is given, as an identity rather than as a flag.
#
# A guest's controller reaches the host as buttons and axes with no brand on
# them, and what the machine then sees is decided here. Sunshine offers the
# same choice, and for the same reason: some games read the pad's identity and
# change what they show. A DualShock asks for Cross where an Xbox pad asks for
# A, and a game that has decided you are on an Xbox pad will tell you to press
# A for the rest of the evening whatever is actually in your hands.
#
# The kind is carried as the USB vendor and product it declares, not as a
# separate argument, and that is deliberate. evdev's UInput has a fixed
# signature with nowhere to put an extra keyword, and the identity is what
# every layer below already keys on: SDL matches a controller by vendor and
# product and applies its own mapping, and virtual.py -- which has no uinput
# to hand on Windows -- reads the same two numbers to decide which ViGEm
# target to open. One fact, declared once, understood the same way the whole
# way down.
#
# 054C:09CC is the second-generation DualShock 4, the one SDL, Steam and
# Windows all have mappings for.
KINDS = {
    "xbox360": {"vendor": 0x045E, "product": 0x028E, "version": 0x0110,
                "label": "Xbox 360 pad"},
    "ds4": {"vendor": 0x054C, "product": 0x09CC, "version": 0x8111,
            "label": "DualShock 4"},
}
DEFAULT_KIND = "xbox360"


# How a phone's rotations reach the pad: which of the wire's three each of the
# pad's takes, and whether it is negated.
#
# Signs matter and leaving them out was a real mistake. The rotation that
# actually makes a phone *be* a controller needs one. A DualShock's axes are x
# to the right, y up out of its face, z forward; a phone in portrait has x
# right, y up the screen, z out toward you. Tip the phone ninety degrees about
# x -- which is what turning a face-toward-you screen into a face-up pad takes
# -- and the pad's x is the phone's pitch, the pad's y is the phone's roll,
# and the pad's z is the phone's yaw *negated*.
#
# Excluding signs left only plain permutations, and the answer is not one, so
# every one of them was wrong in a way that moved whichever axis was not being
# looked at.
#
# What is allowed is any signed permutation whose determinant is +1 -- which
# is exactly the set of rotations. A determinant of -1 is a mirror: it looks
# like a fix for one axis and quietly disturbs another, which cost several
# rounds of asking somebody to tilt a phone and say what moved.
AXIS_NAMES = ("pitch", "yaw", "roll")
DEFAULT_GYRO_ORDER = "pitch,roll,-yaw"


def _determinant(spec):
    """Of the 3x3 the spec describes. +1 is a rotation, -1 a mirror."""
    rows = [[0, 0, 0] for _ in range(3)]
    for at, (index, sign) in enumerate(spec):
        rows[at][index] = sign
    a, b, c = rows
    return (a[0] * (b[1] * c[2] - b[2] * c[1])
            - a[1] * (b[0] * c[2] - b[2] * c[0])
            + a[2] * (b[0] * c[1] - b[1] * c[0]))


def parse_gyro_order(name):
    """"pitch,roll,-yaw" -> ((0, 1), (2, 1), (1, -1)), or None.

    None rather than an exception, and never a half-read one: a name that
    cannot be understood leaves the caller to fall back whole.
    """
    parts = [p.strip().lower() for p in str(name or "").split(",")]
    if len(parts) != 3:
        return None
    spec = []
    for part in parts:
        sign = 1
        if part.startswith("-"):
            sign, part = -1, part[1:].strip()
        elif part.startswith("+"):
            part = part[1:].strip()
        if part not in AXIS_NAMES:
            return None
        spec.append((AXIS_NAMES.index(part), sign))
    if len({index for index, _sign in spec}) != 3:
        return None                      # an axis used twice, another dropped
    if _determinant(spec) != 1:
        return None                      # a mirror, not a posture
    return tuple(spec)


def gyro_order(name):
    """The named order, or the default. Never an error."""
    return parse_gyro_order(name) or parse_gyro_order(DEFAULT_GYRO_ORDER)


def kind_or_default(kind):
    """The named kind, or the default -- never an error.

    A pad is made at the moment somebody sits down to play. A name that does
    not match anything must not be the reason they cannot: the wrong pad is a
    button prompt showing the wrong letter, and no pad is an evening lost.
    """
    name = str(kind or "").strip().lower()
    return name if name in KINDS else DEFAULT_KIND

# How long a pad may hear nothing before it is forced open. Short enough that a
# dropped guest does not run into a wall for long, and long enough to survive
# an ordinary network hiccup at the 8 ms send interval.
DEADMAN_SECONDS = 0.25

# How long a pad nobody is sitting on is kept before it is unplugged.
#
# Not a tidy-up delay: unplugging is something the machine on the other side
# *notices*, and some of what it notices it does not undo. An emulator binds
# a game's motion controls to a particular device, and a DualShock that goes
# away and comes back is a different device to it -- so the gyroscope stops
# working and stays stopped until the emulator is restarted. Buttons survive
# because they are re-matched by name; motion is not.
#
# Reported exactly that way: leave a stream, come back, and the gyro is dead
# in Suyu until Suyu is restarted.
#
# So a seat somebody has just left keeps its device for a while. Long enough
# to cover leaving and coming back -- a dropped connection, a reload, walking
# out of the room -- and short enough that a pad nobody returns to still stops
# taking a player port before it matters. The buttons are let go of
# immediately either way; it is only the device that lingers.
# Half an hour, and deliberately generous.
#
# The first version was two minutes, on the reasoning that it only had to cover
# leaving and coming back. It does not: "I may step away from a game for half
# an hour then come back, and I don't want to be disrupted." A pad kept for
# somebody who returns costs a player port that nobody else was going to use;
# a pad unplugged from somebody who returns costs them their game's motion
# controls until they restart it. Those are not the same size of mistake.
#
# There is a second guard above this -- nothing is unplugged at all while a
# game is in front -- but that reads the *foreground* window, and a game can
# lose focus to a search box or a desktop while somebody is out of the room.
# This is what covers that.
LINGER_SECONDS = 1800

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

    Every one of them, always, `guide` or not -- the argument is kept because
    callers pass it and is deliberately ignored here.

    This device says it is an Xbox 360 pad: vendor 045e, product 028e. SDL
    matches a controller by that identity and then applies its built-in
    mapping for it, and that mapping describes eleven buttons. Declaring ten
    made this a device claiming to be a 360 pad and not shaped like one, and
    SDL games treated it accordingly -- which is why DELTARUNE took a
    controller from Sunshine, whose virtual pad declares the lot, and not from
    this one.

    The guest still cannot press guide. to_events never writes it, which is
    where that has to be enforced anyway: a guest's own physical pad has a
    guide button whatever this device declares.

    One list, because two things read it and they must not drift: this, which
    builds the device, and retroarch.py, which writes the profile RetroArch
    matches against it. RetroArch numbers buttons by ascending evdev code over
    exactly the codes declared here, so a code removed from this list renumbers
    every button above it -- drop BTN_MODE and the thumb sticks slide from 9
    and 10 to 8 and 9. Left to two hand-written lists, that would show up as
    pressing the left stick opening the emulator's menu.
    """
    return [code for _, code in _BUTTON_MAP]


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

    A guide press from a guest is not written when `guide` is off. This is the
    only place that is enforced now: the device declares the button, because a
    device claiming to be an Xbox 360 pad and missing one of its buttons is
    not recognised as one by SDL, and it has to be recognised to be played.
    """
    out = []
    for bit, code in _BUTTON_MAP:
        # The one button a guest may not press. Filtered here rather than left
        # off the device: the device has to be a whole Xbox 360 pad for SDL to
        # recognise it as one, and a press that is never written is a press
        # that never happens.
        if code == e.BTN_MODE and not guide:
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

    # Which pad this is pretending to be. Class-level for the same reason
    # `guide` is: a test that builds one without calling __init__ still has to
    # be able to ask.
    kind = DEFAULT_KIND
    # Whether motion from a guest is carried at all. Class-level for the same
    # reason as the two above: a pad built by a test without __init__ is still
    # asked.
    motion = True

    def __init__(self, name, now=None, guide=True, kind=DEFAULT_KIND,
                 motion=True, order=None, accel=None):
        self.name = name
        self.guide = guide
        self.motion = motion
        self.kind = kind_or_default(kind)
        spec = KINDS[self.kind]
        self._ui = UInput(capabilities(guide), name=name,
                          vendor=spec["vendor"], product=spec["product"],
                          version=spec["version"], bustype=BUSTYPE)
        # Which way round a guest's rotations reach this pad. Set on the
        # device rather than passed to it: evdev's UInput has a fixed
        # signature, and this is one more thing with nowhere to go in it.
        if order is not None:
            try:
                self._ui.gyro_order(order)
            except AttributeError:
                pass                 # a device with no motion to order
        # And which accelerometer it reports: the phone's own, a steady one,
        # or none. Set the same way and for the same reason.
        if accel:
            try:
                if accel in getattr(self._ui, "ACCEL_WAYS", ()):
                    self._ui.accel_way = accel
            except AttributeError:
                pass                 # a device with no accelerometer to set
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
                merged = self._merged()
                self._write(to_events(merged, self.guide), merged.motion)
            return True
        self._senders[key] = [state.seq, state]
        merged = self._merged()
        self._write(to_events(merged, self.guide), merged.motion)
        self.released = False
        return True

    def forget(self, sender):
        """Drop a sender from a shared pad, and stop holding what they held."""
        if self._senders.pop(sender, None) is None:
            return
        if not self._senders:
            self.release_all()
        else:
            merged = self._merged()
            self._write(to_events(merged, self.guide), merged.motion)

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
        # Attitude does not add up. Two people cannot both be holding the
        # phone, and averaging or or-ing two attitudes describes a position
        # neither of them is in -- so whoever is actually moving wins, which
        # is the same rule the axes use and for the same reason: somebody
        # sitting still must not cancel somebody playing.
        motion = None
        best = -1
        for state in states:
            if not state.motion:
                continue
            turning = sum(abs(v) for v in state.motion[:3])
            if turning > best:
                best, motion = turning, state.motion
        axes = [0] * len(P.PadState().axes)
        for state in states:
            buttons |= state.buttons
            for i, value in enumerate(state.axes):
                if abs(value) > abs(axes[i]):
                    axes[i] = value
        return P.PadState(seq=0, buttons=buttons, axes=axes, motion=motion)

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

    def _write(self, events, motion=None):
        changed = False
        for etype, code, value in events:
            key = (etype, code)
            if self._last.get(key) == value:
                continue
            self._last[key] = value
            self._ui.write(etype, code, value)
            changed = True
        # Motion is not an evdev event and has no code to compare against, so
        # it is handed over separately and the device says whether it could
        # take it. False from a pad with no place to put a gyroscope -- which
        # is every Xbox pad, and every pad on Linux until the second device a
        # real DualShock uses is written -- so a guest tilting a phone at one
        # of those loses the tilt and keeps the pad.
        if motion and self.motion:
            try:
                if self._ui.motion(motion):
                    changed = True
            except AttributeError:
                # A device from before this existed. Nothing to do, and not
                # worth a broken pad.
                pass
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

    def __init__(self, count, label="Fourth Player", now=None, guide=True,
                 kind=DEFAULT_KIND, motion=True, order=None, accel=None):
        self._now = now or time.monotonic
        self._label = label
        # What a seat's pad declares itself to be. A default for the session;
        # a guest may be given a different one, which is why it is passed per
        # pad below rather than read from here when the device is made.
        self._kind = kind_or_default(kind)
        self._motion = bool(motion)
        self._order = order
        self._accel = accel
        # Whether these pads have a guide button at all. See capabilities():
        # it is the Steam button and RetroArch's menu button, and a guest has
        # no business opening either.
        self._guide = guide
        # The names are fixed for the life of the session even though the
        # devices come and go: RetroArch's per-device profiles are written
        # against them, and the picker reads them out of pad-names.json.
        self.names = [f"{label} {i + 1}" for i in range(count)]
        self.pads = [None] * count
        # Per seat, not per session. Two people playing the same game may want
        # different pads -- and on Windows the choice changes which ViGEm
        # target is opened, so it has to be settled before the device is made.
        self.kinds = [self._kind] * count
        # When each seat was first seen with nobody on it, so a device can be
        # kept for a moment rather than unplugged the instant somebody stands
        # up. See LINGER_SECONDS.
        self._empty_at = {}

    def __len__(self):
        return len(self.pads)

    def name_for(self, index):
        """What the seat is called, whether or not anybody is sitting in it."""
        return self.names[index]

    def allow_motion(self, on):
        """Carry guest motion, or do not. Takes effect on the pads that exist.

        No device is remade for this, unlike a change of kind: motion is not
        part of what a pad declares itself to be, it is something that arrives
        or does not, and a game reading a DualShock's gyroscope is perfectly
        happy for it to read zero.
        """
        want = bool(on)
        if want == self._motion:
            return False
        self._motion = want
        for pad in self.pads:
            if pad is not None:
                pad.motion = want
        log.info("guest motion is %s", "carried" if want else "not carried")
        return True

    def kind_for(self, index):
        """What this seat's pad says it is, whether or not one exists yet."""
        return self.kinds[index]

    def set_kind(self, index, kind):
        """Give a seat a different kind of pad. True if anything changed.

        An existing device is unplugged rather than altered. What a pad is
        cannot be changed once the kernel -- or ViGEm -- has it: the identity
        is read when the device is created and never again, and a game that has
        already seen it has already decided which buttons to name. So the
        device goes and the next frame makes a new one, which is the same thing
        that happens when somebody unplugs a controller and plugs another in,
        and is exactly what it should look like from the game's side.
        """
        want = kind_or_default(kind)
        if want == self.kinds[index]:
            return False
        self.kinds[index] = want
        if self.pads[index] is not None:
            log.info("%s becomes a %s; unplugging the old pad so the new "
                     "identity is the one anything sees",
                     self.names[index], KINDS[want]["label"])
            self.release(index)
        return True

    def __getitem__(self, index):
        """The device for a seat, made on first use.

        Indexing is what the input path does, so arriving here means somebody
        is about to drive this seat and it needs to exist.
        """
        pad = self.pads[index]
        if pad is None:
            pad = VirtualPad(self.names[index], now=self._now,
                             guide=self._guide, kind=self.kinds[index],
                             motion=self._motion, order=self._order,
                             accel=self._accel)
            self.pads[index] = pad
            # Said, because a controller appearing is not free: Steam
            # re-enumerates when one does and may hand a running game to it.
            # A pad appearing repeatedly while nobody is playing means
            # somebody is reconnecting in a loop, which is worth seeing.
            # Where it came from, kept on the pad itself.
            #
            # Making a device is rare by design, so the cost is nothing, and
            # the one fault this class keeps having -- a pad made and unmade
            # once a second, which RetroArch and Steam read as a controller
            # connecting and disconnecting forever -- is invisible without it.
            # The janitor can say who it is fighting instead of only that it
            # is fighting. Seen again on 2026-09-12 and not caught, because
            # restarting to look stopped it.
            try:
                import traceback
                pad.made_by = " <- ".join(
                    "%s:%s:%s" % (f.filename.rsplit("/", 1)[-1], f.lineno,
                                  f.name)
                    for f in traceback.extract_stack()[-5:-1])
            except Exception:
                pad.made_by = ""
            # By name alone. The name already carries a number and it is the
            # one everybody else uses: the picker, the panel, RetroArch's
            # profiles. Printing the array index beside it put two different
            # numbers for the same controller in one sentence -- "Fourth
            # Player 1 (seat 0)" -- and reading that as two controllers, one
            # of which the page would not offer, is the only sensible thing to
            # do with it.
            log.info("plugged in %s", self.names[index])
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
        # Said here, by the thing that actually does it, rather than by each
        # caller.
        #
        # Only the janitor used to say anything, so a path that unplugged
        # without going through it was invisible -- and one did: a guest
        # leaving. The log read "plugged in" five times in eight minutes with
        # not one unplug beside them, which looks like a pad being created for
        # no reason rather than a pad being destroyed for a bad one.
        log.info("unplugged %s", self.names[index])
        self.pads[index] = None
        # Let go before unplugging: a pad removed mid-press otherwise leaves
        # the emulator holding whatever it held.
        try:
            pad.release_all()
        except OSError:
            pass
        pad.close()
        return True

    def unplug_idle(self, taken, after=LINGER_SECONDS, now=None, hold=False,
                    keep=None):
        """Unplug pads for seats nobody has been on for `after` seconds.

        `taken` is the set of seat indices somebody is sitting on. Returns the
        (index, pad) pairs actually unplugged, so the caller can say so.

        A seat filled again inside the grace period keeps the very same
        device, which is the whole point: to an emulator, a controller that
        goes away and comes back is a different controller, and the motion
        binding does not survive it.

        `hold` says a game is running, and nothing is unplugged while one is.
        The clock is restarted rather than merely paused, so a guest who steps
        away mid-game still gets the whole grace period once the game ends,
        instead of the remains of one that expired while they were gone.
        """
        stamp = self._now() if now is None else now
        # `keep` is which seats somebody may still walk back into. None means
        # the caller is not tracking that, and then a game holds every empty
        # seat -- which is what this did before there was anything better to
        # ask.
        protected = None if keep is None else set(keep)
        gone = []
        for index, _pad in list(self.live()):
            if index in taken:
                self._empty_at.pop(index, None)
                continue
            # A game is running and this is a seat its guest may return to, so
            # nothing is unplugged and the clock starts again from now: the
            # grace period begins when the game *ends* rather than when
            # somebody walked away from it.
            #
            # Per seat, not for all of them at once. A guest who came back into
            # a different seat has abandoned the one they left, and holding
            # that one's controller means one person with two controllers in
            # the game -- which is exactly what happened.
            may_return = protected is None or index in protected
            if hold and may_return:
                self._empty_at[index] = stamp
                continue
            if may_return:
                since = self._empty_at.setdefault(index, stamp)
                if stamp - since < after:
                    continue
            # Otherwise there is nothing to wait for. The grace period exists
            # for somebody who might come back, and a seat with no claim on it
            # is one whose guest is demonstrably elsewhere -- they took another
            # seat, which is what consumed the claim. Waiting half an hour to
            # unplug that one is half an hour of somebody having two
            # controllers in the game.
            pad = self.pads[index]
            if self.release(index):
                self._empty_at.pop(index, None)
                gone.append((index, pad))
        return gone

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
