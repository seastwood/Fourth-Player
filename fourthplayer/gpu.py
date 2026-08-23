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
"""

import glob
import logging
import os
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
        try:
            subprocess.run(["sudo", "-n", HELPER, level], check=True,
                           capture_output=True, timeout=10)
            log.info("GPU power level set to %s via the helper", level)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.warning("the clock helper refused: %s", exc)
    else:
        log.info("no clock helper installed; leaving the GPU governor alone "
                 "(encoding will be slower until a game ramps the card)")
    return False
