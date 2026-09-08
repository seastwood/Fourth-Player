"""Browser key names to kernel key codes, and nothing else.

The browser sends `KeyboardEvent.code`, not `.key`. That is deliberate and it
is the whole reason this table is small enough to trust: `.code` names a
*position* on the keyboard -- "KeyA" is the key where A sits on a US board,
whatever that key actually types -- and a kernel key code means the same thing.
So this is a straight position-to-position map, and the host's own keymap
decides what the key produces. A guest on an AZERTY laptop driving a console
set to US gets what the console would type, which is what somebody operating
another machine expects.

Mapping `.key` instead would mean reproducing every layout in the world here
and then fighting the host's layout for the result.

Nothing in this module touches the kernel, so it can be read and tested
anywhere.
"""

from evdev import ecodes as e

# Built rather than typed out: twenty-six lines of KeyA -> KEY_A is twenty-six
# chances to typo one of them, and a loop cannot get the alphabet wrong.
CODES = {}
for _letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    CODES["Key" + _letter] = getattr(e, "KEY_" + _letter)
for _digit in "0123456789":
    CODES["Digit" + _digit] = getattr(e, "KEY_" + _digit)
for _n in range(1, 25):
    CODES["F%d" % _n] = getattr(e, "KEY_F%d" % _n)
for _digit in "0123456789":
    CODES["Numpad" + _digit] = getattr(e, "KEY_KP" + _digit)

CODES.update({
    # The keys that are their own reward.
    "Escape": e.KEY_ESC, "Tab": e.KEY_TAB, "CapsLock": e.KEY_CAPSLOCK,
    "Space": e.KEY_SPACE, "Enter": e.KEY_ENTER, "Backspace": e.KEY_BACKSPACE,

    # Modifiers, left and right kept apart: some programs bind them
    # separately, and a right Alt is AltGr on a great many layouts.
    "ShiftLeft": e.KEY_LEFTSHIFT, "ShiftRight": e.KEY_RIGHTSHIFT,
    "ControlLeft": e.KEY_LEFTCTRL, "ControlRight": e.KEY_RIGHTCTRL,
    "AltLeft": e.KEY_LEFTALT, "AltRight": e.KEY_RIGHTALT,
    "MetaLeft": e.KEY_LEFTMETA, "MetaRight": e.KEY_RIGHTMETA,
    "ContextMenu": e.KEY_COMPOSE,

    # Punctuation, named for where it sits on a US board.
    "Minus": e.KEY_MINUS, "Equal": e.KEY_EQUAL,
    "BracketLeft": e.KEY_LEFTBRACE, "BracketRight": e.KEY_RIGHTBRACE,
    "Backslash": e.KEY_BACKSLASH, "Semicolon": e.KEY_SEMICOLON,
    "Quote": e.KEY_APOSTROPHE, "Backquote": e.KEY_GRAVE,
    "Comma": e.KEY_COMMA, "Period": e.KEY_DOT, "Slash": e.KEY_SLASH,

    # The extra key non-US boards have, and two Japanese ones. Cheap to carry
    # and impossible to add later from a guest's bug report.
    "IntlBackslash": e.KEY_102ND, "IntlRo": e.KEY_RO, "IntlYen": e.KEY_YEN,

    "Insert": e.KEY_INSERT, "Delete": e.KEY_DELETE,
    "Home": e.KEY_HOME, "End": e.KEY_END,
    "PageUp": e.KEY_PAGEUP, "PageDown": e.KEY_PAGEDOWN,
    "ArrowUp": e.KEY_UP, "ArrowDown": e.KEY_DOWN,
    "ArrowLeft": e.KEY_LEFT, "ArrowRight": e.KEY_RIGHT,

    "NumLock": e.KEY_NUMLOCK, "NumpadDecimal": e.KEY_KPDOT,
    "NumpadAdd": e.KEY_KPPLUS, "NumpadSubtract": e.KEY_KPMINUS,
    "NumpadMultiply": e.KEY_KPASTERISK, "NumpadDivide": e.KEY_KPSLASH,
    "NumpadEnter": e.KEY_KPENTER, "NumpadEqual": e.KEY_KPEQUAL,

    "PrintScreen": e.KEY_SYSRQ, "ScrollLock": e.KEY_SCROLLLOCK,
    "Pause": e.KEY_PAUSE,

    # Worth having on a machine that is a television most of the time.
    "AudioVolumeUp": e.KEY_VOLUMEUP, "AudioVolumeDown": e.KEY_VOLUMEDOWN,
    "AudioVolumeMute": e.KEY_MUTE,
    "MediaPlayPause": e.KEY_PLAYPAUSE, "MediaStop": e.KEY_STOPCD,
    "MediaTrackNext": e.KEY_NEXTSONG, "MediaTrackPrevious": e.KEY_PREVIOUSSONG,
})

# The mouse buttons a browser can report, by its own numbering.
BUTTONS = {
    0: e.BTN_LEFT, 1: e.BTN_MIDDLE, 2: e.BTN_RIGHT,
    3: e.BTN_SIDE, 4: e.BTN_EXTRA,
}

# Every code this device declares, sorted so the device is built the same way
# twice. A key not in here cannot be pressed through it at all -- the same
# argument the pad makes about the letters it does not declare, running the
# other way.
KEY_CODES = sorted(set(CODES.values()))

# Held together, these switch virtual terminal and take X -- and the console
# with it. Not a security measure: anybody driving already owns the machine.
# It is an accident guard, because Ctrl+Alt+F4 is a normal thing to press by
# habit in a browser and an abnormal thing to have happen to your television.
VT_KEYS = frozenset(getattr(e, "KEY_F%d" % n) for n in range(1, 13))
CTRL_KEYS = frozenset((e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL))
ALT_KEYS = frozenset((e.KEY_LEFTALT, e.KEY_RIGHTALT))


# X's names for the printable ASCII characters. Needed because a phone cannot
# tell us which key was pressed -- see char_map below -- so it sends the
# character and this is how a character is found on the console's keyboard.
KEYSYM_CHARS = {
    "space": " ", "exclam": "!", "quotedbl": '"', "numbersign": "#",
    "dollar": "$", "percent": "%", "ampersand": "&", "apostrophe": "'",
    "parenleft": "(", "parenright": ")", "asterisk": "*", "plus": "+",
    "comma": ",", "minus": "-", "period": ".", "slash": "/",
    "colon": ":", "semicolon": ";", "less": "<", "equal": "=",
    "greater": ">", "question": "?", "at": "@", "bracketleft": "[",
    "backslash": "\\", "bracketright": "]", "asciicircum": "^",
    "underscore": "_", "grave": "`", "braceleft": "{", "bar": "|",
    "braceright": "}", "asciitilde": "~",
}
for _c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
    KEYSYM_CHARS[_c] = _c

# X numbers its keycodes eight higher than the kernel does. The offset is
# historical and fixed, and it is the whole translation between what xmodmap
# prints and what a uinput device writes.
X_KEYCODE_OFFSET = 8


def char_map(display=":0", xauthority=None):
    """{character: (kernel key code, shift needed)} for the console's layout.

    Read from the console's own keyboard map rather than assumed, because the
    console is what decides. A phone's keyboard sends us characters, not key
    positions -- iOS and Android both report a keydown with no usable `code`
    for their on-screen keys -- so a character has to be turned back into the
    key that produces it *here*. Ask the wrong keyboard and somebody typing an
    apostrophe on a US console gets whatever sits in that spot on theirs.

    Levels beyond shift are left out. AltGr characters are real but they are
    not what somebody types into a login box from a phone, and a wrong guess
    at a third level is worse than not offering one.
    """
    import os
    import subprocess

    env = {"DISPLAY": display, "PATH": "/usr/bin:/bin",
           "XAUTHORITY": xauthority or os.path.expanduser("~/.Xauthority")}
    try:
        done = subprocess.run(["xmodmap", "-pke"], capture_output=True,
                              timeout=10, env=env)
    except (OSError, subprocess.SubprocessError):
        return {}
    if done.returncode != 0:
        return {}

    found = {}
    for line in done.stdout.decode("utf-8", "replace").splitlines():
        # "keycode  38 = a A a A"
        parts = line.split()
        if len(parts) < 4 or parts[0] != "keycode" or parts[2] != "=":
            continue
        try:
            code = int(parts[1]) - X_KEYCODE_OFFSET
        except ValueError:
            continue
        if code <= 0:
            continue
        for level, keysym in enumerate(parts[3:5]):
            char = KEYSYM_CHARS.get(keysym)
            # First one wins: the keymap lists a keycode once, but a character
            # can sit on more than one (the numpad repeats the digits), and
            # the main row comes first.
            if char is not None and char not in found:
                found[char] = (code, level == 1)
    return found


def key_for(name):
    """The kernel code for a browser key name, or None if we do not carry it."""
    return CODES.get(name)


def button_for(index):
    """The kernel code for a browser mouse button number, or None."""
    return BUTTONS.get(index)


def switches_terminal(code, held):
    """Whether pressing `code` with `held` down would change virtual terminal."""
    return (code in VT_KEYS
            and bool(held & CTRL_KEYS)
            and bool(held & ALT_KEYS))
