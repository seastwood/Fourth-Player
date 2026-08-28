"""Force the GPU awake for the duration of a session.

Measured on the machine this was built for: the Radeon idles at 300 MHz, the
lowest of its eight DPM states, and a VAAPI *encode* load does not wake it --
the encode block's demand never shows up in `gpu_busy_percent`, so the governor
leaves it asleep. The cost is not subtle. The same 1080p60 encode runs at 23 fps
asleep and 36 fps awake.

A running game is itself a 3D load and ramps the card, so this only matters for
the window between opening a session at the Kodi menu and actually starting
something -- which is exactly when a guest is most likely to be connecting and
forming their first impression.

Writing to `power_dpm_force_performance_level` needs root, so `install.sh`
drops a sudoers rule for one fixed script and nothing else. If that is not set
up, this degrades to a log line: the session still works, it is just slower
until a game starts.

The rule is necessary and was not sufficient. The service unit sets
NoNewPrivileges, under which sudo refuses to do anything at all -- so for as
long as that hardening has been there, this has failed at every session start,
logged one warning, and been ignored. See `_privileged`.
"""

import glob
import logging
import os
import shutil
import subprocess

log = logging.getLogger("fourthplayer.gpu")

HELPER = "/usr/local/libexec/fourth-player-clocks"


def _cards():
    return sorted(glob.glob("/sys/class/drm/card*/device/power_dpm_force_performance_level"))


def current():
    for path in _cards():
        try:
            with open(path) as handle:
                return handle.read().strip()
        except OSError:
            continue
    return None


def _privileged(level):
    """Ways to run the helper, best first.

    Straight sudo does not work from inside this service. The unit sets
    NoNewPrivileges, which is the whole point of it, and sudo refuses outright:
    "the no new privileges flag is set, which prevents sudo from running as
    root", exit status 1. That had been logged as a warning at every session
    start and otherwise ignored, so the clocks had never once been set -- and
    the card idles at 300 MHz, where the same encode runs at 23 fps instead of
    36. It shows up as the picture stalling the moment a game takes the GPU.

    So the helper is handed to the service manager instead, as its own
    transient unit outside this one's confinement -- the same escape the
    launcher uses to start games, for the same reason.
    """
    ways = []
    runner = shutil.which("systemd-run")
    if runner:
        ways.append(("a transient unit", [
            runner, "--user", "--wait", "--collect", "--quiet",
            "--unit", "fourth-player-clocks",
            "--", "sudo", "-n", HELPER, level]))
    # Still worth trying directly: outside a sandbox it is one process instead
    # of three, and it is what runs when this is used from a shell.
    ways.append(("sudo", ["sudo", "-n", HELPER, level]))
    return ways


def set_level(level):
    """Set every card's DPM level. Returns True if anything actually changed."""
    if level not in ("auto", "high", "low"):
        raise ValueError(f"refusing to write {level!r} to a kernel power control")

    paths = _cards()
    if not paths:
        return False

    # Direct write first: on a machine where the user already has permission
    # (some distributions), no helper is needed at all.
    wrote = False
    for path in paths:
        try:
            with open(path, "w") as handle:
                handle.write(level)
            wrote = True
        except OSError:
            pass
    if wrote:
        log.info("GPU power level set to %s", level)
        return True

    if os.path.exists(HELPER):
        for describe, argv in _privileged(level):
            try:
                subprocess.run(argv, check=True, capture_output=True, timeout=20)
                log.info("GPU power level set to %s (%s)", level, describe)
                return True
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                    OSError) as exc:
                log.debug("clock helper via %s did not work: %s", describe, exc)
        log.warning("the clock helper would not run; the GPU stays on whatever "
                    "the governor decides, and encoding is slower for it")
    else:
        log.info("no clock helper installed; leaving the GPU governor alone "
                 "(encoding will be slower until a game ramps the card)")
    return False
