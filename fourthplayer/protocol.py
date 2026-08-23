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

FLAG_RELEASE_ALL = 0x01

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

    def pressed(self, button: int) -> bool:
        return bool(self.buttons & (1 << button))

    def axis(self, index: int) -> int:
        return self.axes[index]


def encode(state: PadState) -> bytes:
    """Mostly for tests and for the local echo tool -- the browser writes its
    own frames in JavaScript, and `tests/test_protocol.py` checks the two agree."""
    flags = FLAG_RELEASE_ALL if state.release_all else 0
    return FRAME.pack(VERSION, flags, state.seq & 0xFFFF, state.buttons & 0xFFFFFFFF,
                      *(_clamp(v) for v in state.axes))


def decode(data: bytes) -> PadState:
    if len(data) != FRAME_SIZE:
        raise ProtocolError(f"expected {FRAME_SIZE} bytes, got {len(data)}")
    version, flags, seq, buttons, *axes = FRAME.unpack(data)
    if version != VERSION:
        raise ProtocolError(f"unsupported version {version}")
    # Bits above the buttons we know about are not an error -- a newer client
    # may describe a pad with more of them -- but they are not passed on
    # either, because nothing downstream would know what to do with them.
    buttons &= (1 << BUTTON_COUNT) - 1
    return PadState(seq=seq, buttons=buttons, axes=list(axes),
                    release_all=bool(flags & FLAG_RELEASE_ALL))


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
