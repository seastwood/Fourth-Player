"""Fourth Player as a Windows service, so it can run as LocalSystem.

Why a service at all: only LocalSystem may open the Winlogon desktop, which is
where the lock screen and the sign-in box are. Without it the stream goes black
the moment the machine locks and there is no way back in through Fourth Player
-- which is the thing this exists to fix.

A service is also the only way to be SYSTEM and stay SYSTEM across a reboot
without a signed-in user, so the host comes back on a machine nobody has
touched.

The service itself does almost nothing. It watches which session has the
console and keeps one host process running as SYSTEM inside it -- see
winsession.py for why being SYSTEM in session 0 is not enough. When somebody
signs out and somebody else signs in, the console session number changes, the
old host is left to die with its session and a new one is started in the new
one.

Written against advapi32 directly rather than on pywin32. The rest of this
project installs with a git clone and a Python, and a service wrapper is a
poor reason to make somebody find a binary wheel for their Python version.
"""
import logging
import os
import subprocess
import sys
import threading
import time

log = logging.getLogger(__name__)

NAME = "FourthPlayer"
DISPLAY = "Fourth Player"
DESCRIPTION = ("Streams this machine to browser guests, including the sign-in "
               "screen. Runs as LocalSystem so it can follow the desktop "
               "Windows puts the input on.")

SUPPORTED = sys.platform == "win32"

if SUPPORTED:
    import ctypes
    from ctypes import wintypes

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    SERVICE_WIN32_OWN_PROCESS = 0x00000010
    SERVICE_AUTO_START = 0x00000002
    SERVICE_ERROR_NORMAL = 0x00000001
    SC_MANAGER_ALL_ACCESS = 0xF003F
    SERVICE_ALL_ACCESS = 0xF01FF
    DELETE = 0x00010000

    SERVICE_CONTROL_STOP = 0x00000001
    SERVICE_CONTROL_SHUTDOWN = 0x00000005
    SERVICE_CONTROL_SESSIONCHANGE = 0x0000000E

    SERVICE_RUNNING = 0x00000004
    SERVICE_STOPPED = 0x00000001
    SERVICE_STOP_PENDING = 0x00000003
    SERVICE_START_PENDING = 0x00000002

    SERVICE_ACCEPT_STOP = 0x00000001
    SERVICE_ACCEPT_SHUTDOWN = 0x00000004
    SERVICE_ACCEPT_SESSIONCHANGE = 0x00000080

    class SERVICE_STATUS(ctypes.Structure):
        _fields_ = [("dwServiceType", wintypes.DWORD),
                    ("dwCurrentState", wintypes.DWORD),
                    ("dwControlsAccepted", wintypes.DWORD),
                    ("dwWin32ExitCode", wintypes.DWORD),
                    ("dwServiceSpecificExitCode", wintypes.DWORD),
                    ("dwCheckPoint", wintypes.DWORD),
                    ("dwWaitHint", wintypes.DWORD)]

    class SERVICE_TABLE_ENTRYW(ctypes.Structure):
        _fields_ = [("lpServiceName", wintypes.LPWSTR),
                    ("lpServiceProc", ctypes.c_void_p)]

    HANDLER_EX = ctypes.WINFUNCTYPE(wintypes.DWORD, wintypes.DWORD,
                                    wintypes.DWORD, ctypes.c_void_p,
                                    ctypes.c_void_p)
    SERVICE_MAIN = ctypes.WINFUNCTYPE(None, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.LPWSTR))


def python_for_service():
    """The interpreter to run the host with.

    pythonw rather than python: a service has no console, and a child that
    expects one gets a window nobody asked for on the sign-in screen.
    """
    here = os.path.dirname(sys.executable)
    windowless = os.path.join(here, "pythonw.exe")
    return windowless if os.path.exists(windowless) else sys.executable


def host_command():
    """The command line the service keeps alive inside the console session."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return '"%s" -m fourthplayer run' % python_for_service(), root


class Supervisor:
    """Keeps one host running as SYSTEM in whichever session has the console.

    Separated from the service plumbing below so it can be reasoned about --
    and tested -- without a service control manager in the room.
    """

    # How often to look. A session change raises a control message, so this is
    # the backstop rather than the mechanism: it catches a host that died and
    # a console that appeared while nothing was listening.
    EVERY = 3.0

    def __init__(self, start=None, alive=None, session=None):
        from . import winsession
        self._start = start or (lambda: winsession.start_in_console(
            host_command()[0]))
        self._alive = alive or _process_alive
        self._session = session or winsession.console_session
        self.pid = None
        self.session = None
        self.stopping = threading.Event()

    def look(self):
        """One pass. True if it started something."""
        now = self._session()
        if now is None:
            return False                  # nobody is signed in anywhere yet
        if self.pid is not None and self.session == now and self._alive(self.pid):
            return False
        if self.pid is not None and self.session != now:
            # The console moved to another session. The old host cannot follow
            # it -- a process belongs to the session it was made in -- and is
            # left to end with that session rather than killed, because it may
            # still be serving somebody on a switched-away desktop.
            log.info("the console moved from session %s to %s; starting a "
                     "host there", self.session, now)
        pid = self._start()
        if pid is None:
            return False
        self.pid, self.session = pid, now
        return True

    def run(self):
        while not self.stopping.wait(0):
            try:
                self.look()
            except Exception:
                log.exception("the supervisor stumbled; carrying on")
            if self.stopping.wait(self.EVERY):
                return


def _process_alive(pid):
    if not SUPPORTED or not pid:
        return False
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    SYNCHRONIZE = 0x00100000
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
    if not handle:
        return False
    try:
        # WAIT_TIMEOUT means it is still running.
        return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
    finally:
        kernel32.CloseHandle(handle)


# -- installing ------------------------------------------------------------

def install(python=None):
    """Register the service. Returns (ok, what happened).

    sc.exe rather than CreateServiceW, deliberately: it is the command an
    administrator would type, it reports its own errors in their language, and
    the arguments are visible in the log rather than buried in a struct.
    """
    if not SUPPORTED:
        return False, "not Windows"
    runner = python or python_for_service()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # binPath is one string to sc.exe, and the quoting is what everybody gets
    # wrong: the whole command goes in one pair of quotes, with the inner ones
    # escaped, or a path with a space in it silently becomes two arguments.
    binpath = '\\"%s\\" -m fourthplayer.winservice serve' % runner
    made = subprocess.run(
        ["sc.exe", "create", NAME, "binPath=", binpath, "start=", "auto",
         "obj=", "LocalSystem", "DisplayName=", DISPLAY],
        capture_output=True, text=True)
    if made.returncode != 0:
        return False, (made.stdout + made.stderr).strip()
    subprocess.run(["sc.exe", "description", NAME, DESCRIPTION],
                   capture_output=True, text=True)
    # Restart it if it falls over, rather than leaving the machine unreachable
    # until somebody notices -- which on a headless host is never.
    subprocess.run(["sc.exe", "failure", NAME, "reset=", "86400",
                    "actions=", "restart/5000/restart/10000/restart/30000"],
                   capture_output=True, text=True)
    return True, "installed, working directory %s" % root


def uninstall():
    if not SUPPORTED:
        return False, "not Windows"
    subprocess.run(["sc.exe", "stop", NAME], capture_output=True, text=True)
    gone = subprocess.run(["sc.exe", "delete", NAME],
                          capture_output=True, text=True)
    if gone.returncode != 0:
        return False, (gone.stdout + gone.stderr).strip()
    return True, "removed"


# -- being the service -----------------------------------------------------

_status_handle = None
_status = None
_supervisor = None


def _set_state(state, accepts=0, wait=0):
    if not SUPPORTED or _status_handle is None:
        return
    import ctypes
    status = SERVICE_STATUS()
    status.dwServiceType = SERVICE_WIN32_OWN_PROCESS
    status.dwCurrentState = state
    status.dwControlsAccepted = accepts
    status.dwWaitHint = wait
    _advapi32.SetServiceStatus(_status_handle, ctypes.byref(status))


def _handler(control, event, data, context):
    if control in (SERVICE_CONTROL_STOP, SERVICE_CONTROL_SHUTDOWN):
        _set_state(SERVICE_STOP_PENDING, 0, 5000)
        if _supervisor is not None:
            _supervisor.stopping.set()
        return 0
    if control == SERVICE_CONTROL_SESSIONCHANGE:
        # Somebody signed in, out, or switched. The supervisor would find it
        # within a few seconds anyway; this makes it immediate, which is the
        # difference between the stream coming back at once and after a pause
        # nobody can explain.
        if _supervisor is not None:
            try:
                _supervisor.look()
            except Exception:
                log.exception("session change")
        return 0
    return 0


def _service_main(argc, argv):
    global _status_handle, _supervisor
    import ctypes
    _advapi32.RegisterServiceCtrlHandlerExW.restype = ctypes.c_void_p
    handler = HANDLER_EX(_handler)
    _handler_ref.append(handler)              # kept, or it is collected
    _status_handle = _advapi32.RegisterServiceCtrlHandlerExW(
        NAME, handler, None)
    if not _status_handle:
        return
    _set_state(SERVICE_START_PENDING, 0, 10000)
    _supervisor = Supervisor()
    _set_state(SERVICE_RUNNING,
               SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN
               | SERVICE_ACCEPT_SESSIONCHANGE)
    try:
        _supervisor.run()
    finally:
        _set_state(SERVICE_STOPPED)


_handler_ref = []
_main_ref = []


def serve():
    """Hand this process to the service control manager. Blocks until stopped."""
    if not SUPPORTED:
        return 1
    import ctypes
    _log_to_file()
    main = SERVICE_MAIN(_service_main)
    _main_ref.append(main)
    table = (SERVICE_TABLE_ENTRYW * 2)()
    table[0].lpServiceName = NAME
    table[0].lpServiceProc = ctypes.cast(main, ctypes.c_void_p)
    table[1].lpServiceName = None
    table[1].lpServiceProc = None
    if not _advapi32.StartServiceCtrlDispatcherW(table):
        error = ctypes.get_last_error()
        log.error("not started by the service control manager (error %d). "
                  "This entry point is for the service; to run the host "
                  "directly use `fourth-player run`.", error)
        return 1
    return 0


def _log_to_file():
    """The service's own log, beside the host's.

    A service has no console at all, so without this everything it says goes
    nowhere -- and the whole point of it is to be running when nobody is
    looking.
    """
    try:
        base = os.environ.get("ProgramData") or r"C:\ProgramData"
        path = os.path.join(base, "fourth-player", "service.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > 2 * 1024 * 1024:
            os.replace(path, path + ".1")
        logging.basicConfig(
            level=logging.INFO, filename=path, filemode="a",
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    except OSError:
        logging.basicConfig(level=logging.INFO)


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if what == "serve":
        sys.exit(serve())
    if what == "install":
        ok, said = install()
        print(said)
        sys.exit(0 if ok else 1)
    if what == "uninstall":
        ok, said = uninstall()
        print(said)
        sys.exit(0 if ok else 1)
    print("usage: winservice.py [serve|install|uninstall]")
    sys.exit(2)
