"""The input-event vocabulary, from the kernel where there is one.

This package speaks evdev's language throughout -- `KEY_A`, `BTN_SOUTH`,
`ABS_HAT0X` -- and that is worth keeping even where there is no evdev. The
alternative, a second vocabulary for Windows, would mean every module that
names a button knowing which platform it is on, and two sets of names to keep
in agreement for ever. Far better that the vocabulary is one thing and the
*backends* translate at the edge: Linux hands these numbers to uinput, which
is what they are; Windows maps them onto ViGEm's buttons and SendInput's
virtual keys, which is a translation that happens in one place.

So: on Linux this is evdev's own `ecodes`, asked of the binding that will do
the writing. Everywhere else it is a generated copy of the same numbers. They
are a stable kernel ABI, so the copy cannot drift -- and `tools/gencodes.py`
regenerates it from a machine that has evdev if it ever needs to.
"""
import types

try:
    # Preferred wherever it exists, and not merely as a shortcut: these are
    # the numbers the very library that opens /dev/uinput believes in, so
    # asking it removes any chance of this file and that one disagreeing.
    from evdev import ecodes                       # noqa: F401
    HAVE_EVDEV = True
except ImportError:                                # Windows, and anywhere else
    from ._codes import CODES as _CODES, KEY as _KEY
    HAVE_EVDEV = False
    # A namespace rather than a class with __getattr__, so that `getattr(e,
    # "KEY_" + letter)` and a plain attribute both behave exactly as they do
    # against the real module -- including raising AttributeError for a name
    # that is not there, which is how keymap.py finds out a key has no code.
    ecodes = types.SimpleNamespace(KEY=_KEY, **_CODES)
