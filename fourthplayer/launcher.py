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

# The Steam game this server last started, if any. A Steam game has no process
# name worth looking for -- it is whatever the developer called their binary --
# so the appid is remembered and Steam's own marker is looked for instead.
# Cleared when it is stopped, and when it is found to have gone on its own.
_steam_appid = None


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
    # And a Steam game, which is a game on the television by every measure
    # that matters even though it is nothing like the others. Asked of the
    # machine rather than of what this server remembers starting: most games
    # on this box are started from the television, and one of those is still
    # a game somebody may want to end from a phone.
    global _steam_appid
    playing = steam_game_now()
    if playing:
        _steam_appid = playing
        return True
    # Nothing is there. Forgetting what we started stops the next question
    # being answered with a game that ended half an hour ago.
    _steam_appid = None
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


# ---- what is in the way of a game -------------------------------------------
#
# Steam and Moonlight are not games and are not in GAME_PROCESSES: `running()`
# answers "is something playing", and a client sitting on its own menu is not
# that. They are still in the way, so they are closed before a guest's game
# starts.
#
# Closed rather than pushed behind: neither needs to be there. Kodi starts
# them when somebody asks, which is the whole of what those two entries on the
# menu are for. Left running they hold the screen, a GPU context and a few
# hundred megabytes for nothing, and they argue with the game about which of
# them is fullscreen.
#
# The difference between them is what a polite ask costs. Steam's own
# -shutdown closes the game it started, syncs the cloud saves and writes its
# library down, so it is worth waiting on: a guest starting a game here must
# not cost the person who was on the sofa their save. Moonlight is a client
# with nothing of its own to lose -- the game is on another machine, and the
# stream ending is what that machine is already prepared for -- so it gets a
# signal and a shorter wait.
STEAM_PROCESSES = ("steam", "steamwebhelper", "steamerrorreporter")
MOONLIGHT_PROCESSES = ("moonlight", "moonlight-qt")
STEAM_GRACE = 12.0
# A game gets longer than Steam does: this is the window in which it writes
# whatever it writes on the way out.
STEAM_GAME_GRACE = 10.0
STEAM_LIMIT = 20.0
MOONLIGHT_GRACE = 5.0
MOONLIGHT_LIMIT = 10.0


def _any_running(names):
    for name in names:
        try:
            done = subprocess.run(["pgrep", "-x", name], capture_output=True,
                                  timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            return True
    return False


def _close(label, names, graceful, grace, limit):
    """Close something that is in the way. True when it is gone.

    Ask, wait, insist -- the same shape the game stop has, and for the same
    reason: an ask that can be ignored for ever is not a stop, and a guest
    cannot be left watching a list that will not answer.
    """
    if not _any_running(names):
        return True
    gone = lambda: not _any_running(names)          # noqa: E731
    log.info("closing %s before starting a game", label)
    if graceful:
        _sh(graceful, timeout=15)
        if _wait_gone(time.time() + grace, gone=gone):
            log.info("%s closed itself", label)
            return True
        log.warning("%s did not close within %.0fs; asking less politely",
                    label, grace)
    for name in names:
        _sh(["pkill", "-TERM", "-x", name])
    if _wait_gone(time.time() + (limit - grace) / 2, gone=gone):
        return True
    for name in names:
        _sh(["pkill", "-KILL", "-x", name])
    left = _wait_gone(time.time() + (limit - grace) / 2, gone=gone)
    if not left:
        log.error("%s is still running after SIGKILL", label)
    return left


def steam_running():
    """Whether any part of the Steam client is up."""
    return _any_running(STEAM_PROCESSES)


def moonlight_running():
    """Whether Moonlight is up, streaming or on its own list of machines."""
    return _any_running(MOONLIGHT_PROCESSES)


def stop_steam():
    """Close Steam completely, and wait until it is actually gone."""
    exe = shutil.which("steam") or "/usr/games/steam"
    return _close("Steam", STEAM_PROCESSES, [exe, "-shutdown"],
                  STEAM_GRACE, STEAM_LIMIT)


def stop_moonlight():
    """Close Moonlight, ending whatever it was streaming.

    No graceful command: Moonlight has no -shutdown, and it does not need one.
    It is a client -- the game is on the other machine, which is already
    prepared for a stream that stops, because a stream that stops is what a
    network does on its own. TERM closes the session cleanly.
    """
    return _close("Moonlight", MOONLIGHT_PROCESSES, None,
                  MOONLIGHT_GRACE, MOONLIGHT_LIMIT)


def clear_the_screen():
    """Close everything standing between a guest and their game.

    Returns the names of whatever would not close, which is nearly always
    nothing and is worth saying when it is not.
    """
    stubborn = []
    for label, running_now, close in (
            ("Steam", steam_running, stop_steam),
            ("Moonlight", moonlight_running, stop_moonlight)):
        if running_now() and not close():
            stubborn.append(label)
    return stubborn


def stop_steam_game():
    """Close the Steam game this server started, and Steam with it.

    Asked of the game first and Steam second, in that order and not the other:
    Steam shutting down under a game that is still saving is how progress goes
    missing. TERM to the game, a moment to write whatever it writes, and then
    Steam is closed the way it is closed everywhere else here.

    Returns True if the screen is clear afterwards.
    """
    global _steam_appid
    appid = _steam_appid or steam_game_now()
    if appid is None:
        # No game, but Steam itself may still be sitting on Big Picture and
        # holding the television, which is the other half of what was asked.
        return stop_steam() if steam_running() else True
    log.info("closing Steam game %s", appid)
    _sh(["pkill", "-TERM", "-f", "AppId=%s" % appid])
    until = time.time() + STEAM_GAME_GRACE
    while time.time() < until:
        if not steam_game_running(appid):
            break
        time.sleep(0.3)
    else:
        log.warning("Steam game %s ignored TERM; killing it", appid)
        _sh(["pkill", "-KILL", "-f", "AppId=%s" % appid])
    _steam_appid = None
    # And Steam itself, which the guest asked to be rid of as much as the
    # game: a Steam left sitting on Big Picture is still holding the screen.
    return stop_steam()


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
    # A Steam game is closed its own way, and takes Steam with it. Asked of
    # the machine, so a game started from the television is stopped by this
    # too -- which is most of them.
    if steam_game_now() is not None:
        return stop_steam_game()

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


# The flag that opens the interface a controller can drive. Named here rather
# than written into a launch line, because Valve renamed it once already.
BIG_PICTURE = "-gamepadui"


def steam_game(row):
    """The appid, if this row is a Steam game rather than a ROM.

    Steam's own interface is not a game and has no appid to launch, however
    much its row looks like one -- `-applaunch bigpicture` would be nonsense.
    """
    if row.get("kind") != "steam" or row.get("shell"):
        return None
    return row.get("appid")


def steam_game_now():
    """The appid of whatever Steam game is playing, or None.

    Steam marks everything it launches -- `reaper SteamLaunch AppId=1671210`
    -- so this finds a game whoever started it, which is the point. Tracking
    only what this server launched meant a game somebody put on from the
    television was invisible here: the options tab said nothing was playing
    while it plainly was, and there was no way to end it from a phone.

    The pattern is bracketed so it cannot match the process asking. pgrep -f
    reads whole command lines, this program's included, and a search for
    "AppId=" written plainly finds itself.
    """
    try:
        done = subprocess.run(["pgrep", "-af", "AppId[=]"],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (done.stdout or "").splitlines():
        found = re.search(r"AppId=(\d+)", line)
        if found:
            return found.group(1)
    return None


def steam_game_running(appid):
    """Whether that Steam game is on the screen now.

    Named apart from steam_running(), which asks whether Steam itself is up.
    Two functions called the same thing in one file is how the later one
    silently replaces the earlier, and this file already has one that other
    code depends on.

    By the marker Steam puts on the command line of everything it starts --
    `reaper SteamLaunch AppId=274190 --` -- rather than by the game's own
    process name, which is whatever the developer called it and is not
    written down anywhere this can read.
    """
    try:
        done = subprocess.run(["pgrep", "-f", "AppId=%s" % appid],
                              capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def preflight(row):
    """Why this game cannot start, or None.

    Every one of these otherwise presents as the screen going black and coming
    straight back, with nothing said anywhere.
    """
    if row.get("kind") == "steam":
        if not (shutil.which("steam") or os.path.exists("/usr/games/steam")):
            return "Steam is not installed on this machine."
        return None
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
    if row.get("kind") == "steam" and row.get("shell"):
        # Steam's own interface, and nothing else on the line. `-gamepadui`
        # beside an `-applaunch` launches neither -- that pair is what stopped
        # games starting when Big Picture was first tried here -- so this row
        # exists precisely so that asking for Big Picture is its own request.
        exe = shutil.which("steam") or "/usr/games/steam"
        return [exe, BIG_PICTURE]

    appid = steam_game(row)
    if appid:
        # Steam starts itself if it is not already up, and then the game.
        # There is no picker and no shader: this is somebody else's program,
        # and what it does with a controller is between them and it.
        exe = shutil.which("steam") or "/usr/games/steam"
        return [exe, "-applaunch", str(appid)]

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
    appid = steam_game(row)
    if appid:
        # Remembered before the launch rather than after: `steam -applaunch`
        # returns immediately and the game arrives in its own time, so a
        # question asked in between has to be answered with "yes, that one".
        global _steam_appid
        _steam_appid = str(appid)
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
