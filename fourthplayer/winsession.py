"""Starting the host as SYSTEM inside the session somebody is actually using.

This is the piece that lets the sign-in screen be streamed, and it exists
because of two Windows rules that pull against each other.

The first: only LocalSystem may open the Winlogon desktop, which is where the
lock screen, the sign-in box and UAC live. An administrator is refused. So the
host has to be SYSTEM.

The second: a service runs in session 0, which has no desktop at all and never
has since Vista. So being SYSTEM is not enough -- a SYSTEM process in session 0
can capture nothing.

The way through is the one every remote-desktop tool uses. A service, which is
SYSTEM, takes its own token, copies it, moves the copy to whichever session has
the console, and starts the host with it. The result is a SYSTEM process inside
the interactive session: allowed to open Winlogon, and in a session that has
desktops to open.

Nothing here decides whether that is a good idea. It is a real widening --
a SYSTEM host means anyone holding `desk` is acting as SYSTEM rather than as
the signed-in user -- and that is a decision for whoever installs it, not for
this file.
"""
import logging
import sys

log = logging.getLogger(__name__)

SUPPORTED = sys.platform == "win32"

if SUPPORTED:
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)

    TOKEN_DUPLICATE = 0x0002
    TOKEN_QUERY = 0x0008
    TOKEN_ASSIGN_PRIMARY = 0x0001
    TOKEN_ADJUST_DEFAULT = 0x0080
    TOKEN_ADJUST_SESSIONID = 0x0100
    TOKEN_ALL = (TOKEN_DUPLICATE | TOKEN_QUERY | TOKEN_ASSIGN_PRIMARY
                 | TOKEN_ADJUST_DEFAULT | TOKEN_ADJUST_SESSIONID)

    SecurityImpersonation = 2
    TokenPrimary = 1
    TokenSessionId = 12

    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    CREATE_NO_WINDOW = 0x08000000
    CREATE_NEW_CONSOLE = 0x00000010

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE),
                    ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD),
                    ("dwThreadId", wintypes.DWORD)]


def console_session():
    """Which session has the physical screen and keyboard, or None.

    0xFFFFFFFF means there is no console session at all, which happens for a
    moment during a fast user switch and while the machine is starting. It is
    not an error, it is a "not yet" -- the caller waits and asks again.
    """
    if not SUPPORTED:
        return None
    _wtsapi32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
    found = _wtsapi32.WTSGetActiveConsoleSessionId()
    return None if found == 0xFFFFFFFF else int(found)


def _system_token_for(session):
    """A copy of this process's token, moved into `session`. None on refusal.

    Refusal here is nearly always the same thing: this process is not SYSTEM.
    An administrator can duplicate its own token and cannot move one between
    sessions -- that needs SE_TCB_NAME, which only SYSTEM has.
    """
    import ctypes
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.GetCurrentProcess.argtypes = []
    _advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    _advapi32.DuplicateTokenEx.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, ctypes.c_int,
        ctypes.c_int, ctypes.POINTER(wintypes.HANDLE)]
    _advapi32.SetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]

    mine = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(_kernel32.GetCurrentProcess(),
                                      TOKEN_ALL, ctypes.byref(mine)):
        log.warning("could not open this process's token: error %d",
                    ctypes.get_last_error())
        return None
    try:
        copy = wintypes.HANDLE()
        if not _advapi32.DuplicateTokenEx(mine, TOKEN_ALL, None,
                                          SecurityImpersonation, TokenPrimary,
                                          ctypes.byref(copy)):
            log.warning("could not copy the token: error %d",
                        ctypes.get_last_error())
            return None
        wanted = wintypes.DWORD(session)
        if not _advapi32.SetTokenInformation(copy, TokenSessionId,
                                             ctypes.byref(wanted),
                                             ctypes.sizeof(wanted)):
            error = ctypes.get_last_error()
            _kernel32.CloseHandle(copy)
            log.warning("could not move the token into session %d: error %d "
                        "(1314 means this process is not SYSTEM, which is the "
                        "whole requirement)", session, error)
            return None
        return copy
    finally:
        _kernel32.CloseHandle(mine)


def start_in_console(command, desktop=r"winsta0\default", environment=None):
    """Start `command` as SYSTEM inside the console session. The pid, or None.

    `desktop` is which desktop the new process's first thread lands on.
    "winsta0\\default" is the ordinary one; the host reattaches its own threads
    to whichever desktop has the input once it is running, because that
    changes while it runs and this only decides where it starts.
    """
    if not SUPPORTED:
        return None
    import ctypes
    session = console_session()
    if session is None:
        log.info("no console session yet; nothing to start into")
        return None
    token = _system_token_for(session)
    if token is None:
        return None
    try:
        _advapi32.CreateProcessAsUserW.argtypes = [
            wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
            ctypes.c_void_p, wintypes.LPCWSTR,
            ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION)]
        start = STARTUPINFOW()
        start.cb = ctypes.sizeof(STARTUPINFOW)
        start.lpDesktop = desktop
        info = PROCESS_INFORMATION()
        # A mutable buffer: CreateProcessAsUserW writes to the command line.
        line = ctypes.create_unicode_buffer(command)
        if not _advapi32.CreateProcessAsUserW(
                token, None, line, None, None, False,
                CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW,
                None, None, ctypes.byref(start), ctypes.byref(info)):
            log.warning("could not start the host in session %d: error %d",
                        session, ctypes.get_last_error())
            return None
        _kernel32.CloseHandle(info.hThread)
        _kernel32.CloseHandle(info.hProcess)
        log.info("started the host as SYSTEM in session %d on %s (pid %d)",
                 session, desktop, info.dwProcessId)
        return int(info.dwProcessId)
    finally:
        _kernel32.CloseHandle(token)


def explain():
    """Whether this process could do the above, in one line for a log."""
    if not SUPPORTED:
        return "not Windows"
    from . import windesktop
    session = console_session()
    if not windesktop.running_as_system():
        return ("this process is not SYSTEM, so it cannot put anything into "
                "another session; the console session is %s"
                % ("unknown" if session is None else session))
    return ("SYSTEM, and the console session is %s"
            % ("not up yet" if session is None else session))
