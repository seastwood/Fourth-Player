"""What an admin's keyboard and mouse look like on the wire.

Deliberately not shaped like `protocol.py`, and the difference is the whole
design. A pad frame is a *snapshot* sent over an unreliable channel, because a
lost pad frame is superseded 8 ms later and a late one is worse than none --
in a game, input that arrives wrong-but-now beats right-but-then.

None of that holds here. Mouse motion is a *delta*: lose one and the pointer
is permanently short by that much, send one twice and it moves twice. A key
release that goes missing leaves a key held down on somebody's computer. There
is no next frame that corrects any of it, because there is no state to
resend -- only things that happened.

So the desk channel is reliable and ordered, and these are events. What that
costs is that a lost packet makes the pointer briefly *late* rather than
briefly wrong, which for operating a desktop is the right way round.

Messages are JSON, one object or a list of them:

    {"t": "m", "dx": int, "dy": int}     pointer moved this far
    {"t": "p", "x": int, "y": int}       pointer is exactly here, 0..32767
    {"t": "w", "dx": int, "dy": int}     wheel turned this many notches
    {"t": "k", "c": "KeyA", "d": 1}      key down (0 for up)
    {"t": "c", "ch": "A"}                type this character, however it is
                                         reached on the console's keyboard
    {"t": "b", "b": 0, "d": 1}           mouse button down (0 for up)
    {"t": "r"}                           release everything, now

JSON rather than a struct because this is a low-rate channel where the
messages differ in shape, and because the key names are strings anyway: the
table that turns "KeyA" into a kernel code lives on this side, in `keymap`,
where it can be tested. A binary format would mean numbering the keys and
keeping two hand-written tables in step, which is the one thing the pad
protocol's comment says will eventually drift.

Nothing here maps to the kernel, so it can be tested on any machine.
"""

import json
from dataclasses import dataclass

# One flick of a mouse at 60 Hz is a couple of hundred pixels. A thousand is
# generous for a real movement and small enough that a bad client cannot throw
# the pointer to the far side of the screen in one message.
MOTION_LIMIT = 1000
# A wheel reports notches, not pixels. Twenty in a single message is already a
# hard spin.
WHEEL_LIMIT = 20
# The longest key name we will even look at, so a client cannot make us hold a
# megabyte string while deciding we do not know it.
NAME_LIMIT = 32
# How many events one message may carry. A page batches a frame's worth, which
# is a handful; this is the ceiling on how much work one packet can ask for.
BATCH_LIMIT = 64


class DeskError(ValueError):
    """A message that cannot be trusted. Dropped whole, never half-applied."""


@dataclass(frozen=True)
class Move:
    dx: int
    dy: int


# Where an absolute pointer may be put, on both axes. Wider than any screen,
# so the page's idea of where the pointer is never has to be rounded to the
# console's pixels before it is sent.
POINT_MAX = 32767


@dataclass(frozen=True)
class Point:
    """Where the pointer is, rather than how far it moved.

    Both exist because they answer different questions. A hand on a mouse
    knows only that it moved, and relative motion is what a game wants -- it
    has no edges to run into.

    A finger dragging on a video knows exactly where the pointer should end
    up, and needs the *page* to know too: keeping the pointer in the middle of
    a zoomed picture means the page has to know where it is, and it cannot
    work that out from deltas it has sent, because the console applies its own
    pointer acceleration to those. Sending the position instead makes the
    page's copy right by construction.
    """
    x: int
    y: int


@dataclass(frozen=True)
class Wheel:
    dx: int
    dy: int


@dataclass(frozen=True)
class Key:
    name: str
    down: bool


@dataclass(frozen=True)
class Char:
    """One character to type, rather than one key to press.

    The two are not the same request and cannot be collapsed. A physical
    keyboard knows which key was struck and says so; a phone's on-screen
    keyboard does not -- both iOS and Android report a keydown with no usable
    `code` for their own keys -- and only knows what character came out. So
    the phone says what it meant and the console works out which of its keys
    produces that, which is also the only way a guest on one layout can type
    correctly into a console on another.
    """
    ch: str


@dataclass(frozen=True)
class Button:
    index: int
    down: bool


@dataclass(frozen=True)
class ReleaseAll:
    pass


def _whole(value, limit, what):
    """An integer within +/- limit. Floats are refused rather than rounded.

    A browser can hand out fractional movement on a high-resolution pointer,
    but rounding it here would silently swallow everything below one pixel and
    a slow drag would move nothing at all. The page accumulates and sends
    whole pixels; if it sends anything else that is a bug worth seeing.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise DeskError("%s must be a whole number, got %r" % (what, value))
    if value < -limit or value > limit:
        raise DeskError("%s of %d is outside +/-%d" % (what, value, limit))
    return value


def _span(value, what):
    """A whole number on the absolute pointer's scale."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise DeskError("%s must be a whole number, got %r" % (what, value))
    if value < 0 or value > POINT_MAX:
        raise DeskError("%s of %d is outside 0..%d" % (what, value, POINT_MAX))
    return value


def _one(item):
    if not isinstance(item, dict):
        raise DeskError("expected an object, got %r" % type(item).__name__)
    kind = item.get("t")
    if kind == "m":
        return Move(_whole(item.get("dx"), MOTION_LIMIT, "dx"),
                    _whole(item.get("dy"), MOTION_LIMIT, "dy"))
    if kind == "p":
        return Point(_span(item.get("x"), "x"), _span(item.get("y"), "y"))
    if kind == "w":
        return Wheel(_whole(item.get("dx"), WHEEL_LIMIT, "wheel dx"),
                     _whole(item.get("dy"), WHEEL_LIMIT, "wheel dy"))
    if kind == "k":
        name = item.get("c")
        if not isinstance(name, str) or not name or len(name) > NAME_LIMIT:
            raise DeskError("not a key name: %r" % (name,))
        return Key(name, bool(item.get("d")))
    if kind == "c":
        ch = item.get("ch")
        # One character. A string of them would be a second way to do the
        # same thing, and a batch already carries several of these.
        if not isinstance(ch, str) or len(ch) != 1:
            raise DeskError("not a single character: %r" % (ch,))
        return Char(ch)
    if kind == "b":
        index = item.get("b")
        if isinstance(index, bool) or not isinstance(index, int):
            raise DeskError("not a button: %r" % (index,))
        return Button(index, bool(item.get("d")))
    if kind == "r":
        return ReleaseAll()
    raise DeskError("unknown message type %r" % (kind,))


def decode(data):
    """Every event in one message, in order. Raises DeskError on anything odd.

    All or nothing on purpose: half of a batch is a keyboard in a state
    neither end agreed on, and the caller has no way to tell which half.
    """
    if isinstance(data, (bytes, bytearray)):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DeskError("not utf-8: %s" % exc)
    try:
        parsed = json.loads(data)
    except (ValueError, TypeError) as exc:
        raise DeskError("not JSON: %s" % exc)
    items = parsed if isinstance(parsed, list) else [parsed]
    if len(items) > BATCH_LIMIT:
        raise DeskError("%d events in one message, over the %d limit"
                        % (len(items), BATCH_LIMIT))
    return [_one(item) for item in items]
