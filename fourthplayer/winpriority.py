"""Ask Windows to schedule this host like something that has to keep up.

The streaming host was running at BELOW_NORMAL priority on the Windows
machine, and had been since it was installed. Nothing chose that: a scheduled
task whose settings do not name a priority gets task priority 7, and 7 maps to
BELOW_NORMAL_PRIORITY_CLASS. The installer never named one.

That is the whole shape of the fault it produced. A capture and encode running
at 60 frames a second is soft-real-time work: it has to be given the CPU
sixty times a second or it misses, and nothing about missing is graceful --
the picture stops, the audio ring buffer overruns ("Can't record audio fast
enough", 91 times in one log, which is also why the microphone indicator kept
flickering in the tray), and the data channel carrying the picture misses its
retransmission deadline and gives up. Scheduled below every ordinary process
on the machine, it loses that race to anything that wants the CPU -- and the
thing that wants the CPU is the game.

Which is exactly how it was reported: "the freeze ups seem to only happen
while I am playing a game... if I put the game in the background, the freeze
ups happen less frequently."

Two things are asked for here, and neither needs administrator rights:

  * ABOVE_NORMAL rather than HIGH. Above the game is the point; above the
    window manager and the audio service is not, and HIGH on a machine
    somebody is also playing on is how a streaming host makes the game stutter
    instead of itself.

  * Out of EcoQoS. Windows 11 throttles processes it judges to be background
    work -- onto efficiency cores, at reduced clocks -- and a windowless
    pythonw.exe started at logon is precisely its idea of background work.
    Being told not to is a separate switch from priority and is just as
    capable of causing this on its own.

Best effort throughout. A host that cannot raise its own priority should still
serve; it will simply serve the way it has been serving all along.
"""
import ctypes
import logging
import sys

log = logging.getLogger("fourthplayer.priority")

ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
ProcessPowerThrottling = 4

# What the numbers mean when reading one back, for the log line.
CLASSES = {
    0x00000040: "idle",
    0x00004000: "below normal",
    0x00000020: "normal",
    0x00008000: "above normal",
    0x00000080: "high",
    0x00000100: "realtime",
}


class _PowerThrottlingState(ctypes.Structure):
    _fields_ = [("Version", ctypes.c_uint32),
                ("ControlMask", ctypes.c_uint32),
                ("StateMask", ctypes.c_uint32)]


def _kernel32():
    """kernel32 with every signature declared.

    Not optional, and the reason is a pseudo-handle. GetCurrentProcess returns
    -1, and ctypes defaults a return type to a 32-bit int -- so on 64-bit
    Windows the handle is truncated on its way back out and every call made
    with it fails. Quietly: GetPriorityClass answers 0, which is not a
    priority class, and the first version of this reported "(0, 0)" and
    changed nothing while looking like it had worked.
    """
    lib = ctypes.WinDLL("kernel32", use_last_error=True)
    lib.GetCurrentProcess.restype = ctypes.c_void_p
    lib.GetCurrentProcess.argtypes = []
    lib.GetPriorityClass.restype = ctypes.c_uint32
    lib.GetPriorityClass.argtypes = [ctypes.c_void_p]
    lib.SetPriorityClass.restype = ctypes.c_bool
    lib.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.SetProcessInformation.restype = ctypes.c_bool
    lib.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                          ctypes.c_void_p, ctypes.c_uint32]
    return lib


def raise_priority():
    """Returns (what it was, what it is now), or None where this does not apply."""
    if not sys.platform.startswith("win"):
        return None
    try:
        kernel32 = _kernel32()
        handle = kernel32.GetCurrentProcess()
        was = kernel32.GetPriorityClass(handle)
        if was == ABOVE_NORMAL_PRIORITY_CLASS:
            return CLASSES.get(was, was), CLASSES.get(was, was)
        if not kernel32.SetPriorityClass(handle, ABOVE_NORMAL_PRIORITY_CLASS):
            log.info("could not raise this host's priority (error %d); it will "
                     "run at %s", ctypes.get_last_error(),
                     CLASSES.get(was, was))
            return CLASSES.get(was, was), CLASSES.get(was, was)
        now = kernel32.GetPriorityClass(handle)
        return CLASSES.get(was, was), CLASSES.get(now, now)
    except Exception:                                     # noqa: BLE001
        log.debug("could not ask about this process's priority", exc_info=True)
        return None


def leave_eco_mode():
    """Tell Windows this is not background work. True if it was accepted."""
    if not sys.platform.startswith("win"):
        return False
    try:
        kernel32 = _kernel32()
        state = _PowerThrottlingState(
            Version=PROCESS_POWER_THROTTLING_CURRENT_VERSION,
            # Say which knob is being set, and set it to off. Clearing the
            # control mask instead would mean "use the system default", which
            # is the throttling being escaped.
            ControlMask=PROCESS_POWER_THROTTLING_EXECUTION_SPEED,
            StateMask=0)
        ok = kernel32.SetProcessInformation(
            kernel32.GetCurrentProcess(), ProcessPowerThrottling,
            ctypes.byref(state), ctypes.sizeof(state))
        return bool(ok)
    except Exception:                                     # noqa: BLE001
        # Older Windows has no such call. Nothing is lost that was there.
        log.debug("this Windows does not take power-throttling hints",
                  exc_info=True)
        return False


def apply():
    """Do both, and say what happened. Never raises."""
    changed = raise_priority()
    if changed is None:
        return
    was, now = changed
    if was == now:
        log.info("scheduling priority is %s", now)
    else:
        log.info("scheduling priority raised from %s to %s -- a capture at 60 "
                 "frames a second loses to a foreground game at anything less",
                 was, now)
    if leave_eco_mode():
        log.info("and asked Windows not to treat this as background work")
