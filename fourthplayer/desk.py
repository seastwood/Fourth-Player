"""A keyboard and a mouse, for the one person allowed to operate the machine.

This is the deliberate exception to the rule `pads.capabilities` is built on.
That rule says a guest cannot type on this machine because the device they are
wired to cannot express a keystroke -- not because a check refuses to forward
one -- and it is worth being precise about how this does not weaken it:

  * these are *separate devices*, not extra capabilities on somebody's pad. A
    guest's pad is exactly as incapable of typing as it was before
  * they exist only while somebody is actually driving. Taking control creates
    them; releasing it, dropping out, or going quiet destroys them. There is no
    keyboard attached to this machine at rest
  * only an account holding `desk` can bring them into existence, and only one
    account at a time

So the boundary has not moved. It has one door in it, the door needs a key and
an authenticator code, and it is not there when nobody is holding it open.

The devices identify as themselves rather than impersonating hardware. A pad
has to claim to be an Xbox 360 controller because every mapping in the world is
written against that; a keyboard is a keyboard, and X will take it as one.
"""

import logging
import time

from evdev import AbsInfo, UInput, ecodes as e

from . import deskwire, keymap

log = logging.getLogger("fourthplayer.desk")

VENDOR, PRODUCT, BUSTYPE, VERSION = 0x1209, 0x4650, 0x0003, 0x0100

# How long the desk may hear nothing before everything it is holding is let
# go. Much longer than the pad's quarter second, because the two are answering
# different questions: a pad that goes quiet mid-game should open immediately
# so a character stops running into a wall, while a person operating a desktop
# can reasonably hold shift and think for a moment. Short enough that a browser
# tab that dies with a modifier down does not leave it down for long.
DEADMAN_SECONDS = 2.0

MOUSE_BUTTONS = sorted(set(keymap.BUTTONS.values()))


def keyboard_capabilities():
    """Every key we carry, and nothing about repeat.

    Holding a key still repeats, and it is worth writing down why nothing here
    arranges that. X does its own key repeat, server-side, for as long as it
    believes a key is down -- that is what `xset r rate` sets. So one down and
    one up is the whole story, and the repeat comes out at the console's own
    rate rather than at the rate of whatever machine the driver is sitting at.

    Which means the page must drop the browser's repeated keydown events. If
    it forwarded them we would be writing a down for a key already down; the
    kernel drops those as no change, so they would do nothing at all -- but a
    stray release between two of them would be a real key coming back up.

    (EV_REP cannot be declared through uinput anyway: there is no ioctl for
    it, and asking evdev for one is an EINVAL out of UInput's constructor.)
    """
    return {e.EV_KEY: list(keymap.KEY_CODES)}


def pointer_capabilities():
    """An absolute pointer, beside the relative one rather than instead of it.

    Two devices because they answer different questions and a single device
    claiming both axes is read as neither by libinput. A hand on a real mouse
    sends relative motion, which is what a game wants; a finger dragging on a
    video sends a position, which is what a page needs so it can keep the
    pointer in the middle of a zoomed picture.

    No BTN_TOUCH: that is what would make this a touchscreen rather than a
    pointer, and a touchscreen has no cursor to move. The buttons stay on the
    relative device -- X merges every pointer into one cursor, so a click sent
    there happens wherever this one last put it.
    """
    span = AbsInfo(value=0, min=0, max=deskwire.POINT_MAX,
                   fuzz=0, flat=0, resolution=0)
    return {
        e.EV_ABS: [(e.ABS_X, span), (e.ABS_Y, span)],
        e.EV_KEY: list(MOUSE_BUTTONS),
    }


def mouse_capabilities():
    """A relative pointer, which is what a trackpad is.

    Relative rather than absolute because that is what was asked for, and it
    carries a property worth naming: there is no coordinate on the wire, so
    there is nothing to get wrong when the video is scaled, letterboxed, or a
    different size than the screen it came from. The page sends "this far from
    where you were" and the host decides where that lands.
    """
    return {
        e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL],
        e.EV_KEY: list(MOUSE_BUTTONS),
    }


class Desk:
    """The devices, and everything currently held down on them."""

    def __init__(self, label="Fourth Player", now=None, display=":0"):
        self._clock = now or time.monotonic
        self.keyboard = UInput(keyboard_capabilities(), name=label + " Keyboard",
                               vendor=VENDOR, product=PRODUCT,
                               version=VERSION, bustype=BUSTYPE)
        self.mouse = UInput(mouse_capabilities(), name=label + " Mouse",
                            vendor=VENDOR, product=PRODUCT + 1,
                            version=VERSION, bustype=BUSTYPE)
        self.pointer = UInput(pointer_capabilities(), name=label + " Pointer",
                              vendor=VENDOR, product=PRODUCT + 2,
                              version=VERSION, bustype=BUSTYPE)
        self.held_keys = set()
        self.held_buttons = set()
        # How to reach each character on this console's keyboard. Read once,
        # here, rather than per keystroke: it is a subprocess, and somebody
        # typing a password should not pay for one between letters.
        self.chars = keymap.char_map(display)
        if not self.chars:
            log.warning("could not read the console's keyboard map; a phone's "
                        "on-screen keyboard will not be able to type")
        self.unknown_chars = 0
        self.last_seen = self._clock()
        # Counted rather than logged each time: a guest whose browser insists
        # on a key we do not carry would otherwise write a line per press.
        self.unknown_keys = 0
        self.refused_switches = 0
        log.info("desk devices open: %s", label)

    # -- doing what was asked --------------------------------------------

    def apply(self, actions):
        """Everything in one message, in order. Never raises."""
        self.last_seen = self._clock()
        moved = wheeled = False
        for action in actions:
            name = type(action).__name__
            if name == "Move":
                if action.dx:
                    self.mouse.write(e.EV_REL, e.REL_X, action.dx)
                if action.dy:
                    self.mouse.write(e.EV_REL, e.REL_Y, action.dy)
                moved = moved or bool(action.dx or action.dy)
            elif name == "Point":
                self.pointer.write(e.EV_ABS, e.ABS_X, action.x)
                self.pointer.write(e.EV_ABS, e.ABS_Y, action.y)
                self.pointer.syn()
            elif name == "Wheel":
                if action.dy:
                    self.mouse.write(e.EV_REL, e.REL_WHEEL, action.dy)
                if action.dx:
                    self.mouse.write(e.EV_REL, e.REL_HWHEEL, action.dx)
                wheeled = wheeled or bool(action.dx or action.dy)
            elif name == "Key":
                self._key(action)
            elif name == "Char":
                self._char(action)
            elif name == "Button":
                self._button(action)
            elif name == "ReleaseAll":
                self.release_all()
        if moved or wheeled:
            self.mouse.syn()

    def _key(self, action):
        code = keymap.key_for(action.name)
        if code is None:
            self.unknown_keys += 1
            return
        if action.down and keymap.switches_terminal(code, self.held_keys):
            # Ctrl+Alt+F-something. Not refused because a driver may not do
            # it -- they own the machine -- but because it is an ordinary
            # browser habit and an extraordinary thing to do to a television:
            # it changes virtual terminal, X goes with it, and the console is
            # then only reachable by walking over to it.
            self.refused_switches += 1
            if self.refused_switches in (1, 10):
                log.warning("not passing a terminal switch (%d so far)",
                            self.refused_switches)
            return
        self.keyboard.write(e.EV_KEY, code, 1 if action.down else 0)
        self.keyboard.syn()
        (self.held_keys.add if action.down else self.held_keys.discard)(code)

    def _char(self, action):
        """Type one character, shifting for it if that is how it is reached.

        Whatever is already held stays held: somebody holding Ctrl on the
        on-screen modifier row and then tapping a letter means Ctrl and that
        letter, and releasing their modifier for them would quietly turn
        every shortcut into plain typing.

        Shift is only added when the character needs it and is taken away
        again straight after -- unless the person is already holding shift,
        in which case it was theirs and stays theirs.
        """
        found = self.chars.get(action.ch)
        if found is None:
            self.unknown_chars += 1
            if self.unknown_chars in (1, 100):
                log.warning("no key on this console types %r (%d so far)",
                            action.ch, self.unknown_chars)
            return
        code, needs_shift = found
        held_shift = bool(self.held_keys & {e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT})
        borrow = needs_shift and not held_shift
        if borrow:
            self.keyboard.write(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
        self.keyboard.write(e.EV_KEY, code, 1)
        self.keyboard.write(e.EV_KEY, code, 0)
        if borrow:
            self.keyboard.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
        self.keyboard.syn()

    def _button(self, action):
        code = keymap.button_for(action.index)
        if code is None:
            return
        self.mouse.write(e.EV_KEY, code, 1 if action.down else 0)
        self.mouse.syn()
        (self.held_buttons.add if action.down else self.held_buttons.discard)(code)

    # -- letting go ------------------------------------------------------

    def release_all(self):
        """Let go of everything held. Safe to call when nothing is."""
        if self.held_keys:
            for code in sorted(self.held_keys):
                self.keyboard.write(e.EV_KEY, code, 0)
            self.keyboard.syn()
            self.held_keys.clear()
        if self.held_buttons:
            for code in sorted(self.held_buttons):
                self.mouse.write(e.EV_KEY, code, 0)
            self.mouse.syn()
            self.held_buttons.clear()

    def sweep(self, now=None):
        """Release everything if the driver has gone quiet. True if it did.

        The dead-man switch, and it matters more here than on a pad. A pad
        left holding a button runs a character into a wall. A keyboard left
        holding Ctrl, or a mouse left holding its left button, does it to
        somebody's actual computer -- and unlike a game, nothing on the
        machine will time out and put it right by itself.
        """
        if not (self.held_keys or self.held_buttons):
            return False
        stamp = now if now is not None else self._clock()
        if stamp - self.last_seen < DEADMAN_SECONDS:
            return False
        log.info("the desk went quiet holding %d key(s) and %d button(s); "
                 "letting go", len(self.held_keys), len(self.held_buttons))
        self.release_all()
        return True

    def close(self):
        """Put the devices away. Nothing is held down afterwards, by anybody."""
        try:
            self.release_all()
        finally:
            for device in (self.keyboard, self.mouse, self.pointer):
                try:
                    device.close()
                except Exception:
                    log.exception("could not close a desk device")
            log.info("desk devices closed")
