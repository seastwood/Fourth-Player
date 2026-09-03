"""What is in front on the television, and whether a guest may reach it.

A guest's controller is a real input device on this machine. It is not wired
to the game: it is wired to the machine, and whatever has the foreground
consumes it. That was tolerable while the only thing a guest could reach was
RetroArch. It stopped being tolerable when Steam arrived, because Steam's
gamepad interface hands a controller a mouse pointer, an on-screen keyboard,
a store with a saved card in it, the account settings, a web browser, and a
button marked "switch to desktop".

No amount of asking guests to log in changes any of that. A login gates what
the *page* offers; it cannot gate what the pad does, because the pad is a
kernel device and the thing reading it is whichever window has focus. The only
enforceable answer is to stop delivering the frames while the thing in front
is one guests have no business driving.

A blocklist rather than an allowlist, and deliberately. An allowlist is the
safer shape in principle and the wrong trade here: the failure it produces is
a guest whose controller goes dead in the middle of a game nobody thought to
name, which on a couch console is worse than a guest reaching a menu. The
things worth naming are few, well known, and do not change: the Steam client's
own interface, Kodi, and the desktop.
"""

import logging
import os
import subprocess

log = logging.getLogger("fourthplayer.screen")

# Matched against the focused window's class and name, both lowercased.
#
# `steamwebhelper` is Big Picture and the client's own interface; a Steam
# *game* is its own window with its own class, which is the distinction that
# lets guests keep playing while the shell stays out of reach. `kodi` is the
# menu this all runs under. The rest is the desktop somebody would land on
# after "switch to desktop", which is the escape worth caring about.
#
# `moonlight` is the awkward one, and it is here for now rather than for ever.
# Its chooser and its stream are the same window -- there is no class to tell
# them apart -- so blocking it costs guests a streamed game they could
# otherwise have played together, and allowing it hands whoever is holding a
# pad in another house the keyboard and mouse of a second machine in this one.
# Between those two, the second is not a thing to do by accident. What it
# wants instead is the host naming a guest who may: see the note in
# session.py, and until then this is the safe half of the choice.
SHELLS = ("steamwebhelper", "steam", "moonlight", "kodi", "xfdesktop",
          "xfce4-panel", "xfce4-appfinder", "thunar", "xfce4-session")


def sh(*argv):
    """Ask X something. An answer we cannot get is not an error."""
    try:
        done = subprocess.run(list(argv), capture_output=True, text=True,
                              timeout=5, env=environment())
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip()


def environment():
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")
    return env


def foreground():
    """The focused window, as "class name", lowercased. "" if there is none.

    Both halves, because neither is reliable alone: Kodi's class is `kodi.bin`
    and its name is the film you are watching, while Steam's Big Picture window
    is named "Steam Big Picture Mode" under a class of `steamwebhelper`.
    """
    window = sh("xdotool", "getactivewindow")
    if not window:
        return ""
    kind = sh("xdotool", "getwindowclassname", window)
    name = sh("xdotool", "getwindowname", window)
    return (" ".join(part for part in (kind, name) if part)).lower()


def is_shell(text, shells=SHELLS):
    """Whether that window is one a guest has no business driving.

    Nothing in front is a shell too: an empty desktop with no focused window
    is where a guest lands if the game crashes, and the answer there is the
    same -- their controller stops at the television.
    """
    if not text:
        return True
    return any(shell in text for shell in shells)
