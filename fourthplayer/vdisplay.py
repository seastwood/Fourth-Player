"""An optional virtual monitor on Windows, so the picture is not the panel's.

The capture is the machine's own desktop, so the desktop's resolution is the
most the stream can ever contain -- asking for more is the same pixels scaled
up at a larger picture's bitrate. That is fine when the console has a screen
worth sending. It is a problem in two cases: a host whose monitor is smaller
than the guest's, and a host with no monitor at all.

A virtual display answers both. SudoVDA -- the driver Sunshine ships, already
installed here -- makes a monitor that exists only in software, at whatever
size is asked for, on the GPU with the most memory. The desktop extends onto
it, the capture is pointed at it, and the picture is exactly the size that is
being sent with nothing scaled anywhere.

Driven over its documented IOCTL interface (Common/Include/sudovda-ioctl.h in
SudoMaker/SudoVDA). Two things about that interface shape this module:

  The monitor belongs to the open handle. Close it -- or end the process --
  and the monitor goes. That is the right way round for this: a host that
  crashes does not leave a phantom screen behind for somebody to find in their
  display settings a week later.

  There is a watchdog, three seconds by default, and a monitor whose owner
  stops pinging is removed. So this keeps a thread whose only job is to say it
  is still here. The alternative is turning the watchdog off in the registry,
  which would mean a crash *does* leave the monitor behind.

Windows only, and it says so rather than pretending: everything here returns
False or None on anything else.
"""
import logging
import os
import sys
import threading
import uuid

log = logging.getLogger(__name__)

# CTL_CODE(FILE_DEVICE_UNKNOWN=0x22, function, METHOD_BUFFERED=0, FILE_ANY_ACCESS=0)
def _ctl(function):
    return (0x22 << 16) | (function << 2)


IOCTL_ADD = _ctl(0x800)
IOCTL_REMOVE = _ctl(0x801)
IOCTL_WATCHDOG = _ctl(0x803)
IOCTL_PING = _ctl(0x888)
IOCTL_VERSION = _ctl(0x8FF)

# {e5bcc234-1e0c-418a-a0d4-ef8b7501414d} -- the driver's own control interface,
# not one of the standard display ones it also exposes.
INTERFACE_GUID = "{e5bcc234-1e0c-418a-a0d4-ef8b7501414d}"


# The monitors this program is allowed to make, named in advance.
#
# Not random, and that is the point. A monitor is removed by its GUID, so a
# random one can only be removed by the process that made it -- and when that
# process is killed the monitor stays, because the driver's watchdog is fed by
# any live client rather than per monitor. A host restarted eight times left
# eight screens behind, all called FourthPlyr, with no way to get rid of them
# short of a reboot.
#
# Derived from a fixed namespace instead, so every run works out the same
# names and can therefore clear up after every previous one, including after a
# crash and with no state file to lose.
_NAMESPACE = uuid.UUID("6f0a2a1e-5c4b-4d7a-9b3e-fourthplayer"[:36].replace(
    "fourthplayer", "8f4c2d1a6b0e"))
MAX_SCREENS = 8


def _names():
    return [uuid.uuid5(_NAMESPACE, "fourth-player-screen-%d" % i)
            for i in range(MAX_SCREENS)]


def available():
    """Whether this machine could make a virtual monitor at all."""
    if sys.platform != "win32":
        return False
    try:
        return _device_path() is not None
    except Exception:
        return False


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

        @classmethod
        def parse(cls, text):
            raw = uuid.UUID(text)
            fields = raw.fields
            return cls(fields[0], fields[1], fields[2],
                       (ctypes.c_ubyte * 8)(fields[3], fields[4],
                                            *raw.bytes[10:]))

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", ctypes.c_long)]

    class AddParams(ctypes.Structure):
        _fields_ = [("Width", ctypes.c_uint), ("Height", ctypes.c_uint),
                    ("RefreshRate", ctypes.c_uint), ("MonitorGuid", GUID),
                    ("DeviceName", ctypes.c_char * 14),
                    ("SerialNumber", ctypes.c_char * 14)]

    class RemoveParams(ctypes.Structure):
        _fields_ = [("MonitorGuid", GUID)]

    class AddOut(ctypes.Structure):
        _fields_ = [("AdapterLuid", LUID), ("TargetId", ctypes.c_uint)]

    class WatchdogOut(ctypes.Structure):
        _fields_ = [("Timeout", ctypes.c_uint), ("Countdown", ctypes.c_uint)]

    class VersionOut(ctypes.Structure):
        _fields_ = [("Major", ctypes.c_ubyte), ("Minor", ctypes.c_ubyte),
                    ("Incremental", ctypes.c_ubyte), ("TestBuild", ctypes.c_bool)]

    class _InterfaceData(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                    ("Flags", wintypes.DWORD), ("Reserved", ctypes.POINTER(ctypes.c_ulong))]

    DIGCF_PRESENT = 0x02
    DIGCF_DEVICEINTERFACE = 0x10
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def _device_path():
        """Where the driver's control interface is, or None if it is not there."""
        setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
        guid = GUID.parse(INTERFACE_GUID)
        setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
        handle = setupapi.SetupDiGetClassDevsW(
            ctypes.byref(guid), None, None,
            DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
        if handle in (None, INVALID_HANDLE_VALUE):
            return None
        try:
            data = _InterfaceData()
            data.cbSize = ctypes.sizeof(_InterfaceData)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                    ctypes.c_void_p(handle), None, ctypes.byref(guid), 0,
                    ctypes.byref(data)):
                return None
            # Asked twice on purpose: once for the size, once for the path.
            needed = wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                ctypes.c_void_p(handle), ctypes.byref(data), None, 0,
                ctypes.byref(needed), None)
            if not needed.value:
                return None
            buffer = ctypes.create_string_buffer(needed.value)
            # cbSize is the size of the *fixed* part of the struct, which on
            # 64-bit Windows is 8 -- not the size of the buffer. Getting this
            # wrong is the classic way this call fails with ERROR_INVALID_
            # USER_BUFFER while looking entirely correct.
            ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD))[0] = (
                8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6)
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                    ctypes.c_void_p(handle), ctypes.byref(data), buffer,
                    needed.value, None, None):
                return None
            return ctypes.wstring_at(ctypes.addressof(buffer) + 4)
        finally:
            setupapi.SetupDiDestroyDeviceInfoList(ctypes.c_void_p(handle))
else:
    def _device_path():
        return None


def monitors():
    """Every monitor Windows currently has, in the order the capture counts.

    d3d11screencapturesrc picks a screen by zero-based index, so the order
    here has to be the order it enumerates -- which is EnumDisplayMonitors',
    because that is what it uses. Returns a list of
    (index, handle, width, height, primary, name).

    Empty on anything but Windows, and empty rather than raising in a session
    with no desktop: a service running in session 0 sees no monitors at all,
    which is a fact about where it is running and not an error.
    """
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class _Rect(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class _Info(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _Rect),
                    ("rcWork", _Rect), ("dwFlags", wintypes.DWORD),
                    ("szDevice", ctypes.c_wchar * 32)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    found = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.POINTER(_Rect), ctypes.c_double)

    def each(handle, _dc, _rect, _data):
        info = _Info()
        info.cbSize = ctypes.sizeof(_Info)
        if user32.GetMonitorInfoW(ctypes.c_void_p(handle), ctypes.byref(info)):
            box = info.rcMonitor
            found.append((len(found), int(handle),
                          box.right - box.left, box.bottom - box.top,
                          bool(info.dwFlags & 1), info.szDevice))
        return 1

    try:
        user32.EnumDisplayMonitors(None, None, proc(each), 0)
    except Exception as exc:
        log.debug("could not enumerate monitors: %s", exc)
        return []
    return found


def remove_all():
    """Take away every virtual screen this program has ever made. How many.

    Wanted as a plain "turn it off and get rid of them" -- including the ones
    a previous run left behind, which is most of them, because a monitor whose
    maker has gone is not reaped while anything else is talking to the driver.

    Opens its own handle so it works when nothing is streaming.
    """
    if sys.platform != "win32":
        return 0
    screen = VirtualDisplay()
    if not screen._open_handle():
        return 0
    try:
        return screen._sweep(keep=None)
    finally:
        screen.close_handle()


class VirtualDisplay:
    """One virtual monitor, alive for as long as this object is open."""

    def __init__(self):
        self._handle = None
        self._guid = None
        self._stop = None
        self._pinger = None
        self.width = self.height = self.fps = 0
        # Which screen the capture should be pointed at. Worked out by seeing
        # which one appeared, rather than by trusting that the newest monitor
        # is last or that a size is unique -- two 1920x1080 screens are a very
        # ordinary thing to have.
        self.monitor_index = None
        self.monitor_handle = None

    def _open_handle(self):
        """Open the driver. True if it is there and would talk to us."""
        if sys.platform != "win32":
            return False
        import ctypes
        path = None
        try:
            path = _device_path()
        except Exception as exc:
            log.warning("could not look for the virtual display driver: %s", exc)
        if not path:
            log.info("no virtual display driver on this machine (SudoVDA is "
                     "not installed), so the desktop itself is what is sent")
            return False
        GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
        OPEN_EXISTING, FILE_SHARE = 3, 0x01 | 0x02
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = ctypes.c_void_p
        handle = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE,
                                      FILE_SHARE, None, OPEN_EXISTING, 0, None)
        if handle in (None, INVALID_HANDLE_VALUE):
            log.warning("the virtual display driver is installed but would not "
                        "open (error %d)", ctypes.get_last_error())
            return False
        self._handle = handle
        return True

    def close_handle(self):
        """Let go of the driver without touching any screens."""
        if self._handle is None:
            return
        try:
            import ctypes
            ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(self._handle))
        except Exception:
            pass
        self._handle = None

    def open(self, width, height, fps=60, name="FourthPlyr"):
        """Make the monitor. True if it is there afterwards.

        Never raises: a host that cannot make a virtual screen should stream
        the real one, not fail to start.
        """
        if not self._open_handle():
            return False
        version = self._ask(IOCTL_VERSION, None, VersionOut)
        if version is not None:
            log.info("virtual display driver protocol %d.%d.%d%s",
                     version.Major, version.Minor, version.Incremental,
                     " (test build)" if version.TestBuild else "")

        # Anything this program left behind, before making a new one.
        # Otherwise a host that has been restarted a few times accumulates a
        # screen per restart, all of them called FourthPlyr, none of them
        # showing anything.
        self._sweep(keep=None)
        before = {m[1] for m in monitors()}
        self._guid = _names()[0]
        params = AddParams()
        params.Width, params.Height = int(width), int(height)
        params.RefreshRate = int(fps)
        params.MonitorGuid = GUID.parse("{%s}" % self._guid)
        # Fourteen bytes including the terminator, so thirteen characters.
        params.DeviceName = name.encode("ascii", "ignore")[:13]
        params.SerialNumber = b"FP-" + ("%s" % self._guid).encode()[:10]
        out = self._ask(IOCTL_ADD, params, AddOut)
        if out is None:
            log.warning("the virtual display driver refused a %dx%d@%d monitor",
                        width, height, fps)
            self.close()
            return False
        self.width, self.height, self.fps = int(width), int(height), int(fps)
        log.info("made a virtual monitor: %dx%d @%d, target %d",
                 self.width, self.height, self.fps, out.TargetId)
        self._keep_alive()
        self._find_monitor(before)
        return True

    def _find_monitor(self, before):
        """Which screen the new monitor turned out to be.

        Windows takes a moment to attach it, so this waits rather than looking
        once and giving up -- a capture pointed at the wrong screen is a
        session showing somebody the desktop they did not ask for.
        """
        import time
        for _ in range(40):                       # up to four seconds
            now = monitors()
            fresh = [m for m in now if m[1] not in before]
            if fresh:
                # If more than one appeared, the one that is the size we asked
                # for is ours.
                mine = ([m for m in fresh
                         if m[2] == self.width and m[3] == self.height]
                        or fresh)[0]
                self.monitor_index, self.monitor_handle = mine[0], mine[1]
                log.info("the virtual monitor is screen %d (%s), %dx%d",
                         mine[0], mine[5], mine[2], mine[3])
                return
            time.sleep(0.1)
        log.warning("the virtual monitor was made but Windows has not "
                    "attached it to the desktop; capturing the usual screen")

    def _keep_alive(self):
        """Tell the driver we are still here, for as long as we are.

        The watchdog exists so a monitor cannot outlive the thing that asked
        for it. That is the behaviour worth having -- it means a crash cannot
        leave a phantom screen in somebody's display settings -- and the price
        is this thread.
        """
        watchdog = self._ask(IOCTL_WATCHDOG, None, WatchdogOut)
        timeout = getattr(watchdog, "Timeout", 0) or 0
        if not timeout:
            return                      # the watchdog is off; nothing to feed
        # Comfortably inside it: a third of the timeout, and never longer than
        # a second, so a busy host still answers in time.
        every = max(0.25, min(1.0, timeout / 3.0))
        self._stop = threading.Event()

        def beat():
            while not self._stop.wait(every):
                if self._ask(IOCTL_PING, None, None) is False:
                    log.warning("the virtual display driver stopped answering; "
                                "the monitor may go away")
                    return

        self._pinger = threading.Thread(target=beat, name="vdisplay-ping",
                                        daemon=True)
        self._pinger.start()

    def _ask(self, code, params, out_type):
        """One IOCTL. The out structure, True for no-output calls, or None."""
        if self._handle is None:
            return None
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        in_ref, in_size = (None, 0)
        if params is not None:
            in_ref, in_size = ctypes.byref(params), ctypes.sizeof(params)
        out = out_type() if out_type is not None else None
        out_ref, out_size = ((ctypes.byref(out), ctypes.sizeof(out))
                             if out is not None else (None, 0))
        returned = ctypes.c_ulong(0)
        ok = kernel32.DeviceIoControl(
            ctypes.c_void_p(self._handle), ctypes.c_ulong(code),
            in_ref, in_size, out_ref, out_size, ctypes.byref(returned), None)
        if not ok:
            log.debug("ioctl 0x%x failed: %d", code, ctypes.get_last_error())
            return None if out_type is not None else False
        return out if out is not None else True

    def _sweep(self, keep=None):
        """Remove every screen this program knows how to make.

        `keep` is one to leave alone. Removing a name that is not in use is
        expected to fail and is ignored: there is no way to ask the driver
        which of these exist, and trying all of them is both cheap and the
        only thing that can clear up after a process that is no longer here.
        """
        removed = 0
        for name in _names():
            if keep is not None and name == keep:
                continue
            try:
                params = RemoveParams()
                params.MonitorGuid = GUID.parse("{%s}" % name)
                if self._ask(IOCTL_REMOVE, params, None):
                    removed += 1
            except Exception:
                pass
        if removed:
            log.info("removed %d virtual screen(s) left over from before",
                     removed)
        return removed

    def close(self):
        """Take the monitor away. Safe to call more than once."""
        if self._stop is not None:
            self._stop.set()
            self._stop = None
        if self._handle is not None:
            try:
                # All of them, not just this one. The driver's watchdog is fed
                # by any live client rather than per monitor, so a screen whose
                # maker is gone is not reaped while this process is running --
                # which is how they piled up.
                self._sweep(keep=None)
            except Exception:
                pass
        self.close_handle()
        self._guid = None
        self.width = self.height = self.fps = 0
        self.monitor_index = self.monitor_handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
