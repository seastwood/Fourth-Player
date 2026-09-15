"""Which Windows desktop has the input, and attaching to it.

Windows does not have one screen behind the scenes. A session has a window
station with several *desktops* in it, and only one of them has the input at
any moment:

  Default   the desktop somebody actually works on
  Winlogon  the sign-in and lock screens, and Ctrl-Alt-Delete
  Screen-saver  what a screensaver runs on

A process sees only the desktop its threads are attached to. Fourth Player
runs as the signed-in user and attaches to Default, so when Windows locks and
switches the input to Winlogon the capture is looking at a desktop nobody is
using -- which is the black screen, and why clicking the sign-in box does
nothing: the keystrokes go to Default while the credential box is on Winlogon.

Sunshine and the VNC servers solve it the same way: follow the input desktop.
Ask which one has the input, attach the thread to that one, and do it again
whenever it changes.

There is a privilege to it, and it is the whole cost of the feature.
OpenInputDesktop on Winlogon succeeds only for LocalSystem -- an ordinary
administrator is refused, and a process in another session is refused twice
over. So this is usable only from a host running as a service under SYSTEM.
Nothing here asks for that or arranges it; it reports honestly when it cannot,
so the caller can say why rather than showing a black rectangle.
"""
import logging
import sys

log = logging.getLogger(__name__)

# The desktops Windows makes for a window station. Named here because the
# names are the only way to tell what is happening from a log.
DEFAULT = "Default"
WINLOGON = "Winlogon"
SCREENSAVER = "Screen-saver"

# Nothing to attach to away from Windows, and saying so is better than
# pretending: the Linux hosts capture an X display, which has no equivalent.
SUPPORTED = sys.platform == "win32"


def supported():
    return SUPPORTED


if SUPPORTED:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)

    # DESKTOP_* rights. Reading the name needs ENUMERATE; drawing to it and
    # sending input need more, so the full set is asked for and a refusal is
    # reported rather than silently downgraded.
    DESKTOP_READOBJECTS = 0x0001
    DESKTOP_CREATEWINDOW = 0x0002
    DESKTOP_CREATEMENU = 0x0004
    DESKTOP_HOOKCONTROL = 0x0008
    DESKTOP_JOURNALRECORD = 0x0010
    DESKTOP_JOURNALPLAYBACK = 0x0020
    DESKTOP_ENUMERATE = 0x0040
    DESKTOP_WRITEOBJECTS = 0x0080
    DESKTOP_SWITCHDESKTOP = 0x0100
    DESKTOP_ALL = (DESKTOP_READOBJECTS | DESKTOP_CREATEWINDOW
                   | DESKTOP_CREATEMENU | DESKTOP_HOOKCONTROL
                   | DESKTOP_JOURNALRECORD | DESKTOP_JOURNALPLAYBACK
                   | DESKTOP_ENUMERATE | DESKTOP_WRITEOBJECTS
                   | DESKTOP_SWITCHDESKTOP)

    UOI_NAME = 2


def input_desktop(rights=None):
    """A handle to whichever desktop currently has the input, or None.

    The caller owns the handle and must close it. None means Windows refused,
    which on the sign-in screen means this process is not SYSTEM -- the
    ordinary and expected case for a host running as the signed-in user.
    """
    if not SUPPORTED:
        return None
    import ctypes
    _user32.OpenInputDesktop.restype = wintypes.HANDLE
    handle = _user32.OpenInputDesktop(
        0, False, DESKTOP_ALL if rights is None else rights)
    if not handle:
        log.debug("could not open the input desktop: error %d",
                  ctypes.get_last_error())
        return None
    return handle


def desktop_name(handle):
    """The name of a desktop handle -- "Default", "Winlogon" -- or ""."""
    if not SUPPORTED or not handle:
        return ""
    import ctypes
    needed = wintypes.DWORD(0)
    _user32.GetUserObjectInformationW(handle, UOI_NAME, None, 0,
                                      ctypes.byref(needed))
    if not needed.value:
        return ""
    buffer = ctypes.create_unicode_buffer(needed.value // 2 + 1)
    if not _user32.GetUserObjectInformationW(handle, UOI_NAME, buffer,
                                             needed.value,
                                             ctypes.byref(needed)):
        return ""
    return buffer.value


def close(handle):
    if SUPPORTED and handle:
        try:
            _user32.CloseDesktop(handle)
        except Exception:
            pass


def current_name():
    """What the input desktop is called right now, or "" if it cannot be told.

    Deliberately asks for the smallest right that can read a name, so this
    answers on an ordinary account for the Default desktop and says nothing
    for Winlogon rather than raising. That difference is itself the diagnosis:
    a host that can name Default and not Winlogon is a host running as a user.
    """
    handle = input_desktop(DESKTOP_READOBJECTS | DESKTOP_ENUMERATE)
    try:
        return desktop_name(handle)
    finally:
        close(handle)


def attach_this_thread():
    """Point this thread at whatever desktop has the input. The name, or "".

    Every thread that draws, captures or sends input has to do this for
    itself: a desktop is a per-thread thing, and attaching one thread leaves
    the others where they were.
    """
    if not SUPPORTED:
        return ""
    handle = input_desktop()
    if not handle:
        return ""
    try:
        if not _user32.SetThreadDesktop(handle):
            import ctypes
            log.debug("could not attach to the input desktop: error %d",
                      ctypes.get_last_error())
            return ""
        return desktop_name(handle)
    finally:
        # Closing the handle does not detach the thread: the thread holds its
        # own reference until it is pointed somewhere else or ends.
        close(handle)


def running_as_system():
    """Whether this process is LocalSystem, which is what Winlogon needs.

    Worked out from the token's user SID rather than from the account name,
    because the name is localised and S-1-5-18 is not.
    """
    if not SUPPORTED:
        return False
    import ctypes
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    TOKEN_QUERY, TokenUser = 0x0008, 1
    token = wintypes.HANDLE()
    # Signatures stated rather than left to ctypes' guesses. GetCurrentProcess
    # returns the pseudo-handle -1, and without these ctypes hands it on as a
    # plain int and refuses it as too large for a C int.
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = []
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD)]
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(),
                                     TOKEN_QUERY, ctypes.byref(token)):
        return False
    try:
        size = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, TokenUser, None, 0,
                                     ctypes.byref(size))
        if not size.value:
            return False
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, TokenUser, buffer,
                                            size.value, ctypes.byref(size)):
            return False
        # TOKEN_USER is a SID_AND_ATTRIBUTES: a pointer to the SID first.
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        text = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(ctypes.c_void_p(sid),
                                               ctypes.byref(text)):
            return False
        try:
            return text.value == "S-1-5-18"
        finally:
            kernel32.LocalFree(text)
    finally:
        kernel32.CloseHandle(token)


def explain():
    """One line for the log: where the input is, and whether we can follow it.

    The point of it is that "the stream went black when it locked" and "the
    host is not allowed to see the sign-in screen" look identical from a
    phone, and only one of them is a fault.
    """
    if not SUPPORTED:
        return "not Windows: there is one X display and no desktops to follow"
    name = current_name()
    system = running_as_system()
    if not name:
        return ("the input desktop cannot even be named from here, which "
                "means another session has it (this process is in session 0) "
                "or it is the sign-in screen and this is not SYSTEM")
    if name == DEFAULT:
        return ("the input is on the ordinary desktop%s"
                % ("" if system else ", and this host runs as the signed-in "
                   "user, so a lock screen will not be visible"))
    return ("the input is on %s%s" % (name, "" if system else
            ", which this host cannot follow because it is not SYSTEM"))
