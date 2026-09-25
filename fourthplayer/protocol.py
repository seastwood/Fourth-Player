"""The wire format for one guest's pad, and the reasoning behind its shape.

Every frame is a *complete snapshot* of the pad rather than a press or release
event. That is the whole design, and it buys three things at once:

  * the channel can be unreliable and unordered, because a lost frame is
    superseded by the next one 8 ms later rather than desynchronising the pad
    forever -- a dropped "release" in an event stream leaves a button stuck
    down with nothing to correct it
  * a guest who reconnects is instantly correct: the first frame they send is
    the truth, with no replay or handshake
  * the dead-man switch in `pads` needs no cooperation from the client. Silence
    is unambiguous, because a held button keeps *arriving*; it is not implied
    by the absence of a release

Frames are 20 bytes, little-endian:

    u8   version      1
    u8   flags        bit 0: guest is deliberately releasing everything
    u16  seq          wraps at 65536; older frames are dropped, never applied
    u32  buttons      bit N set == W3C standard-mapping button N is pressed
    i16  axes[6]      LX, LY, RX, RY, LT, RT

Button numbering is the W3C Gamepad API "standard" mapping exactly, so the
browser sends `gamepad.buttons[i].pressed` straight into bit i with no table in
between. Anything that needs a different layout translates on this side, where
it can be tested.
"""

import struct
from dataclasses import dataclass, field

VERSION = 1
FRAME = struct.Struct("<BBHI6h")
FRAME_SIZE = FRAME.size          # 20

# Motion, as six more signed 16-bit values after the axes: gyro x, y, z then
# accelerometer x, y, z.
#
# A flag and a longer frame rather than a new VERSION, deliberately. The
# version is checked for equality and a mismatch is refused, so bumping it
# makes every page and every host an exact pair -- and the page is served by
# the host, so that would be survivable but pointless. A flag says "there is
# more here" and leaves a frame without it byte-for-byte what it always was,
# which means the pad path is untouched for every guest who sends no motion.
FLAG_RELEASE_ALL = 0x01
FLAG_MOTION = 0x02

MOTION = struct.Struct("<6h")
MOTION_SIZE = FRAME_SIZE + MOTION.size          # 32

# What the six numbers mean.
#
# Neither end's native units. A browser reports rotation in degrees per second
# and acceleration in m/s^2; a DualShock reports both as raw sensor counts
# whose scale is a property of the part Sony fitted. Carrying either of those
# on the wire would put one end's accident in the middle, and the middle is
# the one place that has to stay readable -- the same reason the button codes
# here are evdev's everywhere and translated at the edges.
#
# So: gyro in sixteenths of a degree per second, which fits +/-2048 deg/s in
# an int16 -- more than a hand can turn a phone -- and acceleration in
# thousandths of gravity, which fits +/-32 g. Both are plenty, both are exact
# at the resolution anybody can feel, and both are obvious to read in a log.
GYRO_PER_DEG_SEC = 16
ACCEL_PER_G = 1000
MOTION_LEN = 6

# W3C standard mapping. The names are ours; the indices are the spec's.
BTN_A, BTN_B, BTN_X, BTN_Y = 0, 1, 2, 3
BTN_LB, BTN_RB, BTN_LT, BTN_RT = 4, 5, 6, 7
BTN_BACK, BTN_START = 8, 9
BTN_LSTICK, BTN_RSTICK = 10, 11
BTN_UP, BTN_DOWN, BTN_LEFT, BTN_RIGHT = 12, 13, 14, 15
BTN_GUIDE = 16

BUTTON_COUNT = 17

AX_LX, AX_LY, AX_RX, AX_RY, AX_LT, AX_RT = range(6)

AXIS_MIN, AXIS_MAX = -32768, 32767
TRIGGER_MAX = 32767


class ProtocolError(ValueError):
    """A frame that cannot be trusted. Always dropped, never partially applied."""


@dataclass
class PadState:
    seq: int = 0
    buttons: int = 0
    axes: list = field(default_factory=lambda: [0] * 6)
    release_all: bool = False
    # Six values, or None when this guest is sending no motion at all. None
    # rather than zeros: a pad held perfectly still reads as zero rotation and
    # one gravity, and "no sensor" has to be tellable from "not moving" or a
    # host would keep feeding a game a stale attitude for ever.
    motion: list = None

    def pressed(self, button: int) -> bool:
        return bool(self.buttons & (1 << button))

    def axis(self, index: int) -> int:
        return self.axes[index]


def encode(state: PadState) -> bytes:
    """Mostly for tests and for the local echo tool -- the browser writes its
    own frames in JavaScript, and `tests/test_protocol.py` checks the two agree."""
    flags = FLAG_RELEASE_ALL if state.release_all else 0
    motion = state.motion
    # Released means released. A frame that lets go of everything carries no
    # attitude either: the guest has gone, and the last thing they were
    # pointing at is not where anything should be left aiming.
    if state.release_all:
        motion = None
    if motion:
        flags |= FLAG_MOTION
    body = FRAME.pack(VERSION, flags, state.seq & 0xFFFF,
                      state.buttons & 0xFFFFFFFF,
                      *(_clamp(v) for v in state.axes))
    if not motion:
        return body
    six = list(motion)[:MOTION_LEN] + [0] * max(0, MOTION_LEN - len(motion))
    return body + MOTION.pack(*(_clamp(v) for v in six))


def decode(data: bytes) -> PadState:
    if len(data) not in (FRAME_SIZE, MOTION_SIZE):
        raise ProtocolError(
            f"expected {FRAME_SIZE} or {MOTION_SIZE} bytes, got {len(data)}")
    version, flags, seq, buttons, *axes = FRAME.unpack(data[:FRAME_SIZE])
    if version != VERSION:
        raise ProtocolError(f"unsupported version {version}")
    motion = None
    if flags & FLAG_MOTION:
        # The flag and the length have to agree. A frame claiming motion and
        # not carrying it is a frame somebody built wrongly, and guessing which
        # half to believe is how a pad ends up aiming at something nobody
        # pointed at.
        if len(data) != MOTION_SIZE:
            raise ProtocolError(
                f"a frame claiming motion must be {MOTION_SIZE} bytes, "
                f"got {len(data)}")
        motion = list(MOTION.unpack(data[FRAME_SIZE:]))
    elif len(data) == MOTION_SIZE:
        # Long, without the flag. Not an error -- room at the end is how this
        # format is meant to grow -- but nothing here may read it.
        pass
    # Bits above the buttons we know about are not an error -- a newer client
    # may describe a pad with more of them -- but they are not passed on
    # either, because nothing downstream would know what to do with them.
    buttons &= (1 << BUTTON_COUNT) - 1
    return PadState(seq=seq, buttons=buttons, axes=list(axes),
                    release_all=bool(flags & FLAG_RELEASE_ALL),
                    motion=motion)


def is_newer(seq: int, than: int) -> bool:
    """Sequence comparison across the 16-bit wrap.

    Half the space is "newer" and half is "older", which is the usual trick and
    the only one that survives a counter that restarts at zero mid-session. A
    frame exactly half a cycle away is called older, arbitrarily but
    consistently: at 125 Hz that is over four minutes of silence, by which time
    the dead-man switch has released the pad anyway.
    """
    return ((seq - than) & 0xFFFF) != 0 and ((seq - than) & 0xFFFF) < 0x8000


def _clamp(value: int) -> int:
    return max(AXIS_MIN, min(AXIS_MAX, int(value)))
