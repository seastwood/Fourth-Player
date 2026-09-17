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

# Everything here is Windows-only. Named once rather than repeating the
# platform test, and named *before* anything that guards on it -- the
# first version of the mode-setting code was pasted in above this line
# and took the host down on import with a NameError.
SUPPORTED = sys.platform == "win32"


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


if SUPPORTED:
    class DEVMODEW(ctypes.Structure):
        _fields_ = [
            ("dmDeviceName", ctypes.c_wchar * 32),
            ("dmSpecVersion", ctypes.c_ushort),
            ("dmDriverVersion", ctypes.c_ushort),
            ("dmSize", ctypes.c_ushort),
            ("dmDriverExtra", ctypes.c_ushort),
            ("dmFields", ctypes.c_ulong),
            ("dmPositionX", ctypes.c_long), ("dmPositionY", ctypes.c_long),
            ("dmDisplayOrientation", ctypes.c_ulong),
            ("dmDisplayFixedOutput", ctypes.c_ulong),
            ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
            ("dmYResolution", ctypes.c_short),
            ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short),
            ("dmFormName", ctypes.c_wchar * 32),
            ("dmLogPixels", ctypes.c_ushort),
            ("dmBitsPerPel", ctypes.c_ulong),
            ("dmPelsWidth", ctypes.c_ulong), ("dmPelsHeight", ctypes.c_ulong),
            ("dmDisplayFlags", ctypes.c_ulong),
            ("dmDisplayFrequency", ctypes.c_ulong),
            ("dmICMMethod", ctypes.c_ulong), ("dmICMIntent", ctypes.c_ulong),
            ("dmMediaType", ctypes.c_ulong), ("dmDitherType", ctypes.c_ulong),
            ("dmReserved1", ctypes.c_ulong), ("dmReserved2", ctypes.c_ulong),
            ("dmPanningWidth", ctypes.c_ulong),
            ("dmPanningHeight", ctypes.c_ulong)]

    class DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong),
                    ("DeviceName", ctypes.c_wchar * 32),
                    ("DeviceString", ctypes.c_wchar * 128),
                    ("StateFlags", ctypes.c_ulong),
                    ("DeviceID", ctypes.c_wchar * 128),
                    ("DeviceKey", ctypes.c_wchar * 128)]

    DM_PELSWIDTH = 0x00080000
    DM_PELSHEIGHT = 0x00100000
    DM_DISPLAYFREQUENCY = 0x00400000
    ENUM_CURRENT_SETTINGS = -1
    CDS_UPDATEREGISTRY = 0x00000001
    DISP_CHANGE_SUCCESSFUL = 0


def adapters():
    r"""Every display adapter Windows has, as (device name, description).

    The device name is "\\.\DISPLAY1" and so on, which is what
    ChangeDisplaySettingsEx and EnumDisplayMonitors both speak. The
    description is the driver's own, which is how the virtual one is picked
    out: it says "SudoMaker Virtual Display Adapter" and nothing else does.
    """
    if not SUPPORTED:
        return []
    import ctypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    found, index = [], 0
    while True:
        device = DISPLAY_DEVICEW()
        device.cb = ctypes.sizeof(DISPLAY_DEVICEW)
        if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
            break
        # Only the ones actually attached to the desktop; the others have no
        # mode to set and no picture to capture.
        if device.StateFlags & 0x00000001:
            found.append((device.DeviceName, device.DeviceString))
        index += 1
    return found


def _settle(tries=30, pause=0.1):
    """Wait until the monitor list stops changing. True if it did.

    Stability rather than a fixed sleep: a fixed one is either too short on a
    slow machine or wasted on a fast one, and this is in the path of every
    capture that uses a virtual display.
    """
    import time
    last, steady = None, 0
    for _ in range(tries):
        now = [(m[1], m[2], m[3]) for m in monitors()]
        if now == last and now:
            steady += 1
            if steady >= 3:               # three readings the same
                return True
        else:
            steady = 0
        last = now
        time.sleep(pause)
    return False


def modes(device_name):
    """Every mode this adapter offers, as (width, height, hz).

    Asked of Windows rather than assumed, because a monitor's capabilities are
    not something a streaming host can guess: the panel here reports a maximum
    of 143Hz and was sitting at 59, and only the list says which rates in
    between actually exist.
    """
    if not SUPPORTED:
        return []
    import ctypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    found, index = [], 0
    while True:
        mode = DEVMODEW()
        mode.dmSize = ctypes.sizeof(DEVMODEW)
        if not user32.EnumDisplaySettingsW(device_name, index,
                                          ctypes.byref(mode)):
            break
        found.append((int(mode.dmPelsWidth), int(mode.dmPelsHeight),
                      int(mode.dmDisplayFrequency)))
        index += 1
    return found


def best_refresh(device_name, wanted):
    """The refresh rate to use for `wanted` frames a second, or None.

    The highest rate the screen offers at its current size that is no more
    than what was asked for -- so 60 frames a second on a 143Hz panel picks
    60 and not 143, and 120 picks 120 where it exists and 60 where it does
    not. Sending more frames than the screen draws is duplicates: bitrate
    spent to carry the same picture twice.
    """
    if not SUPPORTED:
        return None
    import ctypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    now = DEVMODEW()
    now.dmSize = ctypes.sizeof(DEVMODEW)
    if not user32.EnumDisplaySettingsW(device_name, ENUM_CURRENT_SETTINGS,
                                       ctypes.byref(now)):
        return None
    size = (int(now.dmPelsWidth), int(now.dmPelsHeight))
    rates = sorted({hz for w, h, hz in modes(device_name)
                    if (w, h) == size and hz > 0})
    if not rates:
        return None
    fits = [hz for hz in rates if hz <= wanted]
    # Nothing at or below what was asked for means the screen's slowest mode
    # is already faster, which is fine -- it just cannot go lower.
    return fits[-1] if fits else rates[0]


def set_refresh(device_name, hz):
    """Change only the refresh rate, leaving the size alone. True if it took.

    Separate from set_mode because a physical screen's size is the person's
    choice and none of this host's business -- only the rate is, and only
    because a screen that draws 59 frames a second cannot be captured at 120.
    """
    if not SUPPORTED:
        return False
    import ctypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    mode = DEVMODEW()
    mode.dmSize = ctypes.sizeof(DEVMODEW)
    if not user32.EnumDisplaySettingsW(device_name, ENUM_CURRENT_SETTINGS,
                                       ctypes.byref(mode)):
        return False
    if int(mode.dmDisplayFrequency) == int(hz):
        return True                       # already right; do not disturb it
    was = int(mode.dmDisplayFrequency)
    mode.dmDisplayFrequency = int(hz)
    mode.dmFields = DM_DISPLAYFREQUENCY
    result = user32.ChangeDisplaySettingsExW(device_name, ctypes.byref(mode),
                                             None, CDS_UPDATEREGISTRY, None)
    if result != DISP_CHANGE_SUCCESSFUL:
        log.warning("%s would not go from %dHz to %dHz (code %d)",
                    device_name, was, hz, result)
        return False
    log.info("%s refresh rate %dHz -> %dHz", device_name, was, hz)
    return True


def set_mode(device_name, width, height, hz):
    """Put one adapter into a given mode. True if Windows took it.

    SudoVDA makes the monitor but Windows attaches it at a mode of its own
    choosing -- on a machine with a 2560x1440 panel, a virtual display asked
    for at 2560x1610 arrived as 2560x1440. The size that was asked for has to
    be applied afterwards, which is what this does and what every other tool
    that matches a client's resolution is doing.
    """
    if not SUPPORTED:
        return False
    import ctypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    mode = DEVMODEW()
    mode.dmSize = ctypes.sizeof(DEVMODEW)
    if not user32.EnumDisplaySettingsW(device_name, ENUM_CURRENT_SETTINGS,
                                       ctypes.byref(mode)):
        log.debug("could not read the current mode of %s", device_name)
        return False
    if (mode.dmPelsWidth, mode.dmPelsHeight) == (width, height):
        return True                       # already right; nothing to disturb
    mode.dmPelsWidth, mode.dmPelsHeight = int(width), int(height)
    mode.dmDisplayFrequency = int(hz)
    mode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY
    result = user32.ChangeDisplaySettingsExW(device_name, ctypes.byref(mode),
                                             None, CDS_UPDATEREGISTRY, None)
    if result != DISP_CHANGE_SUCCESSFUL:
        # -2 is DISP_CHANGE_BADMODE: the driver will not do that size. Worth
        # naming, because it is the difference between "this size is
        # impossible here" and "something went wrong".
        log.warning("%s would not go to %dx%d@%d (code %d%s)", device_name,
                    width, height, hz, result,
                    "; the driver does not offer that mode" if result == -2
                    else "")
        return False
    log.info("%s set to %dx%d@%d", device_name, width, height, hz)
    return True


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
        # Windows attaches it at a mode of its own choosing, so the size that
        # was asked for is applied here. Without this a virtual display asked
        # for at 2560x1610 arrives as whatever the machine's other screen is,
        # and the capture scales one to the other -- which is a picture that
        # looks stretched and nothing that says why.
        self._set_requested_mode()
        # Let Windows finish rearranging before anything reads a handle.
        #
        # Adding a screen and then changing its mode both renumber the monitor
        # list and invalidate the HMONITORs in it. Reading one while that is
        # still settling gives a handle that is good for a moment and stale by
        # the time a capture pipeline starts with it -- which does not fail
        # politely: the capture refuses, the session cannot be restored, and
        # the PIN somebody is typing has nothing to let them into.
        _settle()
        if not self._find_monitor(before):
            # Made but unusable: Windows would not attach it at the size that
            # was asked for. Taken away again rather than left as a screen
            # nobody is looking at, and the caller streams the real desktop.
            self.close()
            return False
        return True

    def _set_requested_mode(self):
        """Put our adapter into the size that was asked for.

        Ours is picked out by the driver's own description rather than by
        size or by position: a machine can have two screens the same size, and
        only one of them is called a SudoMaker Virtual Display Adapter.
        """
        import time
        for _ in range(40):                       # it takes a moment to appear
            mine = [name for name, said in adapters()
                    if "sudomaker" in said.lower() or "sudovda" in said.lower()]
            if mine:
                for name in mine:
                    set_mode(name, self.width, self.height, self.fps)
                return
            time.sleep(0.1)
        log.debug("no virtual display adapter is attached to the desktop yet")

    def _find_monitor(self, before):
        """Which screen the new monitor turned out to be. True if found.

        Identified by its size, not by being new. Adding a display makes
        Windows rebuild its monitor list and the handles change with it, so
        the *existing* screens look new too -- and an earlier version of this
        took the first thing that appeared. On a machine with one 2560x1440
        monitor and a virtual display asked for at 2560x1610, that meant
        capturing the real monitor and scaling it up to a size it never had.
        Reported, correctly, as the picture looking stretched.

        A handle that was there before is still preferred against, because two
        screens of the same size is an ordinary thing to own. But nothing is
        chosen that is not the size that was asked for: a capture pointed at
        the wrong screen shows somebody a desktop they did not ask to see, and
        guessing is worse than saying so.
        """
        import time
        want = (self.width, self.height)
        for attempt in range(80):                 # up to eight seconds
            now = monitors()
            right = [m for m in now if (m[2], m[3]) == want]
            if right:
                # Prefer one that was not there before; fall back to size
                # alone, since a handle can be renumbered by the very act of
                # adding a screen.
                mine = ([m for m in right if m[1] not in before] or right)[0]
                self.monitor_index, self.monitor_handle = mine[0], mine[1]
                log.info("the virtual monitor is screen %d (%s), %dx%d",
                         mine[0], mine[5], mine[2], mine[3])
                return True
            time.sleep(0.1)
        sizes = ", ".join("%dx%d" % (m[2], m[3]) for m in monitors()) or "none"
        log.warning("the virtual monitor was asked for at %dx%d and no screen "
                    "of that size appeared (this machine has: %s). Not "
                    "capturing anything at a size it is not, because that is "
                    "a stretched picture rather than an error anybody can see.",
                    self.width, self.height, sizes)
        return False

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
