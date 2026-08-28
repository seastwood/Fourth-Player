"""Starting a game on the host, on behalf of somebody who is not in the room.

Two things here are less obvious than they look.

The player picker must appear. A guest who cannot claim a slot is a guest
holding a controller that the game ignores, and from their side that is
indistinguishable from the whole thing being broken. It comes for free rather
than by asking for it: a session creates one virtual pad per slot the moment it
opens, so by the time anything can be launched RetroArch sees several pads,
which is exactly the condition kodi-retrobox's picker appears under. The test
beside this pins that, because it is a guarantee resting on somebody else's
`needs_picker`.

And the game must not inherit our sandbox. This server runs with ProtectHome
read-only and a short list of writable paths -- appropriate for something
listening to the internet, fatal for an emulator, which writes save files,
playlists and state all over the home directory. A child would inherit every
one of those restrictions, so the game is handed to the user's service manager
instead and started as its own transient unit, outside this process's
confinement entirely.
"""

import logging
import os
import shutil
import subprocess
import time

log = logging.getLogger("fourthplayer.launcher")

# kodi-retrobox's picker, which execs RetroArch once pads are claimed.
PICKER = os.path.expanduser("~/.local/bin/ra_players.py")
SYSTEM_DIR = os.path.expanduser("~/.local/share/retroarch/system")
SHADER_DIR = os.path.expanduser("~/.local/share/retroarch/shaders")
CRT = os.path.join(SHADER_DIR, "crt", "crt-easymode.glslp")

# Mirrored from the television front end so a game started from a phone looks
# the same as one started from the sofa. A scanline filter suits a console that
# was played on a television and is simply wrong on a handheld, whose screen
# never had them.
SHADERS = {
    "Nintendo - Game Boy": "",
    "Nintendo - Game Boy Advance": "",
}
# Systems where no game runs without a BIOS. The core's own metadata calls its
# firmware optional, which here does not mean what it says.
REQUIRED_BIOS = {
    "Sega - Mega-CD - Sega CD": ["bios_CD_U.bin", "bios_CD_E.bin", "bios_CD_J.bin"],
}

# What counts as "a game is already running" for the policy that requires the
# screen to be free first.
GAME_PROCESSES = ("retroarch", "ra_players.py", "pcgame_launch.py")


def running():
    """Whether something is already on the television.

    Deliberately a question about processes rather than about what this server
    started: a game the owner started from the sofa counts, and is the case
    that matters most.
    """
    for name in GAME_PROCESSES:
        try:
            done = subprocess.run(["pgrep", "-f", name], capture_output=True,
                                  timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            return True
    return False


def stop_running():
    """Ask whatever is playing to close, and wait a moment for it to.

    Only reached under the policy that lets a guest start a game over the top
    of one -- which is what that policy means, and why it is not the default.
    TERM rather than KILL because RetroArch writes its save memory on the way
    out, and the difference between the two signals is somebody's progress.
    """
    for name in ("retroarch", "ra_players.py"):
        try:
            subprocess.run(["pkill", "-TERM", "-f", name], timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
    for _ in range(20):                       # up to four seconds
        if not running():
            return True
        time.sleep(0.2)
    return not running()


def preflight(row):
    """Why this game cannot start, or None.

    Every one of these otherwise presents as the screen going black and coming
    straight back, with nothing said anywhere.
    """
    if not os.path.exists(PICKER):
        return "This box cannot start games from here."
    if not os.path.exists(row["path"]):
        return "The game file is missing."
    if not os.path.exists(row["core_path"]):
        return "The emulator for that game is not installed."
    wanted = REQUIRED_BIOS.get(row["system"])
    if wanted and not any(os.path.exists(os.path.join(SYSTEM_DIR, b))
                          for b in wanted):
        return "%s needs a BIOS that is not installed." % row["short"]
    return None


def build_argv(row):
    argv = [PICKER]
    # The picker sizes its board to the game rather than always offering four.
    # It does not suppress itself here: that only happens for one player with
    # one pad, and a session has already created one pad per slot.
    if row.get("players"):
        argv += ["--max-players", str(row["players"])]
    shader = SHADERS.get(row["system"], CRT)
    if not shader or os.path.exists(shader):
        argv += ["--shader", shader or "none"]
    return argv + ["-f", "-L", row["core_path"], row["path"]]


def launch(row, display=":0"):
    """Start the game outside this service's sandbox. Returns None, or why not."""
    problem = preflight(row)
    if problem:
        log.warning("refusing to launch %s: %s", row["label"], problem)
        return problem

    argv = build_argv(row)
    env = {"DISPLAY": display,
           "XAUTHORITY": os.path.expanduser("~/.Xauthority")}
    runner = shutil.which("systemd-run")
    if runner:
        command = [runner, "--user", "--collect", "--quiet",
                   "--unit", "fourth-player-game"]
        for key, value in env.items():
            command += ["--setenv", "%s=%s" % (key, value)]
        command += ["--"] + argv
    else:
        # No service manager to hand the game to. It will inherit whatever
        # confinement this process has, which is worth saying out loud rather
        # than leaving to be discovered as an emulator that cannot save.
        log.warning("systemd-run is not available; starting the game inside "
                    "this service's sandbox, which may stop it writing saves")
        command = argv

    try:
        done = subprocess.run(command, capture_output=True, timeout=20,
                              env=dict(os.environ, **env))
    except (OSError, subprocess.SubprocessError) as exc:
        log.exception("could not start %s", row["label"])
        return "The game could not be started: %s" % exc
    if done.returncode != 0:
        detail = (done.stderr or b"").decode("utf-8", "replace").strip()
        log.error("launch failed (%d): %s", done.returncode, detail[:400])
        return "The game could not be started."
    log.info("launched %s (%s) for a guest", row["label"], row["short"])
    return None
