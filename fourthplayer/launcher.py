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

import glob
import logging
import os
import re
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


# How long a game gets to close politely, and how long the whole business is
# allowed to take. A GameCube core writes its memory card and shuts a GPU
# context down on the way out, which is seconds rather than the instant a
# Mega Drive core takes -- so the grace has to be generous enough for the
# slowest emulator here, and the limit short enough that a guest is not left
# looking at a list that does nothing.
STOP_GRACE = 8.0
STOP_LIMIT = 14.0
POLL = 0.25


def _sh(argv, timeout=5):
    """Run one command; nothing here is worth an exception."""
    try:
        subprocess.run(argv, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        pass


def _wait_gone(deadline, gone=None):
    gone = gone or (lambda: not running())
    while time.time() < deadline:
        if gone():
            return True
        time.sleep(POLL)
    return gone()


# ---- Steam ------------------------------------------------------------------
#
# Steam is not a game and is not in GAME_PROCESSES: `running()` answers "is
# something playing", and a client sitting on the menu is not that. It is
# still in the way, so it is closed before a guest's game starts.
#
# Closed, rather than pushed behind: it does not need to be there. Kodi starts
# it when somebody asks for it, which is the whole of what the add-on on the
# menu is for. Left running it holds a GPU context, a compositor surface and a
# few hundred megabytes for nothing, and it argues with the game about which
# of them is fullscreen.
STEAM_PROCESSES = ("steam", "steamwebhelper", "steamerrorreporter")
# Longer than a game gets. Steam's own shutdown syncs cloud saves and writes
# its library state, and cutting that short is somebody's progress in a game
# this program never even started.
STEAM_GRACE = 12.0
STEAM_LIMIT = 20.0


def steam_running():
    """Whether any part of the Steam client is up."""
    for name in STEAM_PROCESSES:
        try:
            done = subprocess.run(["pgrep", "-x", name], capture_output=True,
                                  timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            return True
    return False


def stop_steam():
    """Close Steam completely, and wait until it is actually gone.

    `steam -shutdown` first, because that is the client's own way out: it
    closes any game it started, syncs the cloud saves, and writes down where
    everybody was. A signal does none of that, and a guest starting a game
    here should not cost the person who was playing on the sofa their save.

    Then the usual insistence, for the same reason the game stop has it: an
    ask that can be ignored for ever is not a stop. TERM after the grace,
    KILL after that, and the truth reported either way.
    """
    if not steam_running():
        return True
    exe = shutil.which("steam") or "/usr/games/steam"
    log.info("closing Steam before starting a game")
    _sh([exe, "-shutdown"], timeout=15)
    if _wait_gone(time.time() + STEAM_GRACE, gone=lambda: not steam_running()):
        log.info("Steam closed itself")
        return True

    log.warning("Steam did not close within %.0fs; asking less politely",
                STEAM_GRACE)
    for name in STEAM_PROCESSES:
        _sh(["pkill", "-TERM", "-x", name])
    if _wait_gone(time.time() + (STEAM_LIMIT - STEAM_GRACE) / 2,
                  gone=lambda: not steam_running()):
        return True
    for name in STEAM_PROCESSES:
        _sh(["pkill", "-KILL", "-x", name])
    gone = _wait_gone(time.time() + (STEAM_LIMIT - STEAM_GRACE) / 2,
                      gone=lambda: not steam_running())
    if not gone:
        log.error("Steam is still running after SIGKILL")
    return gone


def stop_running():
    """Close whatever is playing, and do not come back until it is gone.

    TERM first, because RetroArch writes its save memory on the way out and
    the difference between the two signals is somebody's progress. But TERM
    on its own is a request, and this used to be nothing but requests: ask
    systemd to stop the unit, ask the processes to quit, wait four seconds,
    and report failure if anything was still there.

    Four seconds was not the half of it. `systemctl stop` blocks until the
    unit is really gone, and a transient unit's default TimeoutStopSec is
    ninety seconds -- so the call sat there until this gave up on it after
    ten, then the poll gave up after four more, and the guest was told the
    game "would not close" while systemd was still patiently waiting for it.
    Meanwhile nothing had escalated, so it never would have.

    So: ask, wait properly, and then insist. --no-block means systemd gets on
    with the stop while this watches the processes itself, which is the thing
    the answer actually depends on. Every game process is asked, not the two
    that were listed here while `running()` looked for three -- a game started
    through the PC-games wrapper answered "yes I am running" to one function
    and was not addressed by the other, which is a stalemate by construction.
    """
    systemctl = shutil.which("systemctl")
    if systemctl:
        # Fire and watch, rather than block: what matters is whether the game
        # is gone, and this process can see that for itself.
        _sh([systemctl, "--user", "stop", "--no-block", UNIT_PREFIX + "*"])
    for name in GAME_PROCESSES:
        _sh(["pkill", "-TERM", "-f", name])
    if _wait_gone(time.time() + STOP_GRACE):
        return True

    # It had its chance. A save that is lost here is worth less than a
    # television nobody can start a game on, and every second past the grace
    # is a second of a guest watching a list that will not answer.
    log.warning("nothing closed within %.0fs; killing what is left",
                STOP_GRACE)
    if systemctl:
        _sh([systemctl, "--user", "kill", "--signal=SIGKILL",
             UNIT_PREFIX + "*"])
    for name in GAME_PROCESSES:
        _sh(["pkill", "-KILL", "-f", name])
    gone = _wait_gone(time.time() + (STOP_LIMIT - STOP_GRACE))
    if not gone:
        log.error("something is still running after SIGKILL: %s",
                  ", ".join(GAME_PROCESSES))
    return gone


def player_ports():
    """Which pad is which player, according to the game that is running.

    The picker decides this and writes it into the config it hands RetroArch,
    so the only honest source is that file -- and it is nothing like a guess
    from the slot number. On the machine this was written for, "Fourth Player
    2" was player 1 and ports two to four were parked on an index that cannot
    exist, so a guest offered "player 3" would have been offered a seat that
    is not in the game at all.

    Returns {device name: player number}, empty when nothing is running.
    """
    # Every emulator on the machine, not the first one this happens to walk
    # into. It used to stop at the first process whose name ended in
    # "retroarch" whether or not that process had been given a config at all,
    # and /proc comes back in whatever order the kernel feels like -- so a
    # second copy, or one started by hand without the picker, was enough to
    # make this answer "nothing is playing" while a game was plainly playing.
    seen, paths = [], []
    for entry in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(entry, "rb") as handle:
                argv = handle.read().split(b"\0")
        except OSError:
            continue                  # it exited between the glob and the open
        if not argv or not argv[0].endswith(b"retroarch"):
            continue
        seen.append(argv)
        for i, arg in enumerate(argv):
            if arg == b"--appendconfig" and i + 1 < len(argv):
                paths.append(argv[i + 1].decode("utf-8", "replace"))
    return ports_from_paths(paths, len(seen))


def ports_from_paths(paths, emulators=0):
    """The first config among these that says which pad is which player."""
    for path in paths:
        ports = ports_from_config(path)
        if ports:
            return ports
        log.info("nothing about players could be read from %s", path)
    if emulators:
        log.info("%d emulator process(es) running and none named a config "
                 "this can read", emulators)
    return {}


def ports_from_config(path):
    """Read {device name: player number} out of a RetroArch config fragment."""
    ports = {}
    try:
        with open(path) as handle:
            for line in handle:
                match = re.match(
                    r'\s*input_player(\d+)_reserved_device\s*=\s*"(.*)"', line)
                if match and match.group(2):
                    ports[match.group(2)] = int(match.group(1))
    except OSError:
        return {}
    return ports


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


def build_argv(row, resume=False):
    """What to run. Fresh unless the guest asked to continue.

    The opposite default from the television front end, and deliberately so.
    There, picking a game is somebody choosing to carry on with their own save;
    here it is a guest starting a game on a machine they are not sitting at,
    and dropping them into the middle of somebody else's saved game -- then
    overwriting it on exit -- is not a thing to do without being asked.
    """
    argv = [PICKER]
    if not resume:
        argv += ["--fresh"]
    # The picker sizes its board to the game rather than always offering four.
    # It does not suppress itself here: that only happens for one player with
    # one pad, and a session has already created one pad per slot.
    if row.get("players"):
        argv += ["--max-players", str(row["players"])]
    shader = SHADERS.get(row["system"], CRT)
    if not shader or os.path.exists(shader):
        argv += ["--shader", shader or "none"]
    return argv + ["-f", "-L", row["core_path"], row["path"]]


UNIT_PREFIX = "fourth-player-game"


def new_unit_name():
    """A unit name no previous game can still be holding.

    Every game used to run as `fourth-player-game`, one fixed name. Starting a
    second game over the top of a first then failed outright -- systemd-run
    refuses a name that is still loaded, and a unit lingers for a moment after
    its process is gone, longer if the process is slow to go. The old game had
    already been told to quit by then, so the television kept its last frame
    and the new game never arrived: "it froze and the other one did not start".
    A fresh name each time cannot collide with anything, however slowly the
    last one is taking to be cleaned up.
    """
    return "%s-%d" % (UNIT_PREFIX, time.monotonic_ns())


def launch(row, display=":0", resume=False):
    """Start the game outside this service's sandbox. Returns None, or why not."""
    problem = preflight(row)
    if problem:
        log.warning("refusing to launch %s: %s", row["label"], problem)
        return problem

    argv = build_argv(row, resume)
    env = {"DISPLAY": display,
           "XAUTHORITY": os.path.expanduser("~/.Xauthority")}
    runner = shutil.which("systemd-run")
    if runner:
        command = [runner, "--user", "--collect", "--quiet",
                   "--unit", new_unit_name(),
                   # systemd's own escalation, matched to ours. Left at its
                   # default a transient unit gets ninety seconds between the
                   # TERM and the KILL, which is ninety seconds of a
                   # television holding the last frame of a game that has
                   # already been told to quit.
                   "-p", "TimeoutStopSec=%ds" % int(STOP_GRACE),
                   "-p", "KillMode=mixed"]
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
    log.info("launched %s (%s) for a guest, %s", row["label"], row["short"],
             "continuing from its save state" if resume else "from the start")
    return None
