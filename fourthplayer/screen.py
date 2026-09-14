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
import re
import subprocess
import sys

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
# Steam is deliberately not here any more.
#
# It was, on the reasoning that Steam's own window is a shop and a settings
# screen and no guest's business. What that cost was the owner's own controller:
# Steam's loader, its overlay and Big Picture come to the front constantly
# while a game runs, and every time they did, every controller stopped. Big
# Picture could not be driven from a phone at all, which is the one interface
# on this machine designed to be driven by a controller.
#
# So Steam is a thing you play, like the emulator. What still holds a
# controller is Kodi's menu and the desktop behind it: places where a guest
# pad would be pressing buttons in somebody's file manager.
SHELLS = ("moonlight", "kodi", "xfdesktop",
          "xfce4-panel", "xfce4-appfinder", "thunar", "xfce4-session")

# The same idea on Windows, where the shell has different names. explorer is
# the desktop, the taskbar and every folder window; the rest are the places a
# pad could wander into that are not a game.
#
# Steam is deliberately absent here as it is above: Big Picture is a thing you
# play, and the console decided long ago that the answer to "a guest could
# reach the store" is the account system rather than a dead controller.
WINDOWS_SHELLS = ("explorer.exe", "shellexperiencehost", "searchhost",
                  "startmenuexperiencehost", "applicationframehost",
                  "taskmgr.exe", "systemsettings", "lockapp")


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


def _windows_foreground():
    """The focused window on Windows, as "process.exe title", lowercased.

    The same shape of answer as the X version, by a different route: there is
    no xdotool, and asking the desktop what is in front is three Win32 calls.

    Both halves for the same reason too. A game's title is whatever it feels
    like and its executable is not, while the shell's windows share
    explorer.exe and differ only by title.
    """
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    window = user32.GetForegroundWindow()
    if not window:
        return ""

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window, ctypes.byref(pid))

    name = ""
    if pid.value:
        # LIMITED_INFORMATION rather than QUERY_INFORMATION: it is the one a
        # process gets for an application it does not own, which is most of
        # them.
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if handle:
            try:
                size = wintypes.DWORD(1024)
                buffer = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(
                        handle, 0, buffer, ctypes.byref(size)):
                    name = os.path.basename(buffer.value)
            finally:
                kernel32.CloseHandle(handle)

    length = user32.GetWindowTextLengthW(window)
    title = ""
    if length:
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(window, buffer, length + 1)
        title = buffer.value
    return (" ".join(part for part in (name, title) if part)).lower()


def foreground():
    """The focused window, as "class name", lowercased. "" if there is none.

    Both halves, because neither is reliable alone: Kodi's class is `kodi.bin`
    and its name is the film you are watching, while Steam's Big Picture window
    is named "Steam Big Picture Mode" under a class of `steamwebhelper`.
    """
    if sys.platform == "win32":
        try:
            return _windows_foreground()
        except Exception as exc:
            log.warning("could not read what is in front (%s)", exc)
            return ""
    window = sh("xdotool", "getactivewindow")
    if not window:
        return ""
    # The class, by whichever route this machine has.
    #
    # `getwindowclassname` is not in every xdotool -- it is missing from the
    # one on this console, which is why the class half of this was silently
    # empty and every decision was being made on the window's title alone. A
    # fullscreen game with no title then read as nothing in front, and nothing
    # in front holds every controller in the session.
    #
    # xprop is older than xdotool and always has WM_CLASS.
    kind = sh("xdotool", "getwindowclassname", window)
    if not kind:
        kind = " ".join(re.findall(r'"([^"]*)"',
                                   sh("xprop", "-id", window, "WM_CLASS")))
    name = sh("xdotool", "getwindowname", window)
    return (" ".join(part for part in (kind, name) if part)).lower()


# Where the kernel says which virtual terminal is on the monitor. Readable by
# anybody, which is the whole reason this approach is possible without asking
# for a privilege.
ACTIVE_VT = "/sys/class/tty/tty0/active"


def front_display():
    """The X display currently on the monitor, or None if it cannot be told.

    Usually our own, and the interesting case is when it is not. A screen
    locker, a "switch user", or anything that asks the display manager for a
    greeter starts a *second* X server on a *second* virtual terminal and
    switches the monitor to it. Our own display is then still there, still
    capturable, and no longer the thing anybody is looking at -- which is why
    a guest saw a black rectangle and the console showed a login screen.

    Found by asking the kernel which terminal is in front and then which X
    server was started on it. Both halves are world-readable; nothing here
    needs a privilege, and being unable to answer is a perfectly ordinary
    result rather than an error.
    """
    try:
        with open(ACTIVE_VT, encoding="utf-8") as handle:
            active = handle.read().strip()
    except OSError:
        return None
    if not active.startswith("tty"):
        return None
    wanted = "vt" + active[3:]

    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % entry, "rb") as handle:
                argv = handle.read().split(b"\0")
        except OSError:
            continue
        if not argv or not argv[0].endswith(b"Xorg"):
            continue
        words = [word.decode("utf-8", "replace") for word in argv if word]
        if wanted not in words:
            continue
        for word in words:
            # ":0", ":1" -- the display it was started for.
            if len(word) > 1 and word[0] == ":" and word[1:].isdigit():
                return word
    return None


def can_capture(display, xauthority=None):
    """Whether this user may read that display at all.

    A greeter's X server is started by the display manager with its own
    cookie, kept where only root can read it, so the answer is no until
    something has granted it -- see the greeter hook in `bin/`. Asked rather
    than assumed, because "the picture is black" and "we are not allowed to
    look" are different things to tell somebody.
    """
    env = dict(os.environ, DISPLAY=display)
    if xauthority:
        env["XAUTHORITY"] = xauthority
    try:
        done = subprocess.run(["xdpyinfo"], capture_output=True, timeout=5,
                              env=env)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def is_shell(text, shells=SHELLS):
    """Whether that window is one a guest has no business driving.

    Nothing in front is a shell too: an empty desktop with no focused window
    is where a guest lands if the game crashes, and the answer there is the
    same -- their controller stops at the television.
    """
    if not text:
        return True
    return any(shell in text for shell in shells)


def desktop_size(display=":0"):
    """How big the screen being captured actually is, or None if unknown.

    Worth publishing because asking for a bigger picture than the desktop is
    not a sharper picture, it is the same pixels scaled up and more bitrate
    spent carrying them. Somebody on a 2560-wide laptop reasonably asks for a
    2560-wide stream; if the host's desktop is 1920 they get no more detail
    than 1920 would have given, and nothing anywhere says so.

    None rather than a guess when it cannot be read: a wrong number here would
    be worse than none, since the page would draw a limit nobody has.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            # Without this the numbers come back scaled by the DPI setting,
            # which on a laptop is routinely 125% or 150% -- so a 1920 screen
            # reads as 1536 and the picture is quietly capped below the panel.
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                user32.SetProcessDPIAware()
            # The whole virtual desktop, which is what the capture takes:
            # SM_CXVIRTUALSCREEN / SM_CYVIRTUALSCREEN, not the primary
            # monitor, or a second screen would be cropped away.
            width = user32.GetSystemMetrics(78)
            height = user32.GetSystemMetrics(79)
            if width > 0 and height > 0:
                return (int(width), int(height))
            width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
            return (int(width), int(height)) if width > 0 and height > 0 else None
        except Exception:
            return None
    # X11: ask the server rather than any toolkit, so this needs nothing
    # installed that the capture does not already need.
    out = sh("xdpyinfo", "-display", display)
    found = re.search(r"dimensions:\s+(\d+)x(\d+)", out or "")
    return (int(found.group(1)), int(found.group(2))) if found else None
