"""Steam games on the guest list, and getting rid of them again.

Not every installed game, deliberately. A Steam library is mostly things the
owner would not hand to a stranger on a phone, and beyond that it is full of
software that is not a game at all: on the console this was written for, four
of the six installed "apps" are Proton and the Steam Linux Runtime. So there
is a list the owner keeps, and a game is offered only if it is on that list
*and* actually installed.

Two things about a Steam game are unlike every other row here, and both are
where this could go wrong. It has no file and no emulator, so anything that
reaches for a path has to not. And it has no process name anybody can look
up -- it is whatever the developer called their binary -- so "is it still
playing" and "stop it" are answered through the marker Steam puts on the
command line of everything it launches.

Closing one closes Steam with it, which is what a guest asked for: a Steam
left sitting on Big Picture is still holding the television. The game goes
first and Steam second, because Steam shutting down under a game that is
still saving is how progress goes missing.
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from fourthplayer import steamgames                              # noqa: E402

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def a_library(games):
    """A Steam library on disk, as Steam actually lays one out."""
    root = tempfile.mkdtemp()
    apps = os.path.join(root, "steamapps")
    os.makedirs(apps)
    with open(os.path.join(apps, "libraryfolders.vdf"), "w") as handle:
        handle.write('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % root)
    for appid, name in games:
        with open(os.path.join(apps, "appmanifest_%s.acf" % appid), "w") as handle:
            handle.write('"AppState"\n{\n\t"appid"\t\t"%s"\n\t"name"\t\t"%s"\n}\n'
                         % (appid, name))
    return root


library = a_library([
    ("274190", "Broforce"),
    ("1671210", "DELTARUNE"),
    ("1070560", "Steam Linux Runtime 1.0 (scout)"),
    ("1493710", "Proton Experimental"),
    ("4183110", "Steam Linux Runtime 4.0"),
])
steamgames.STEAM_ROOTS = (library,)
chosen_file = os.path.join(tempfile.mkdtemp(), "steam.txt")
steamgames.CHOSEN = chosen_file

print("what Steam has, minus what is not a game")
here = steamgames.installed()
names = [g["name"] for g in here]
check(names == ["Broforce", "DELTARUNE"],
      "the runtimes and Proton are left out, so 'what could I add?' is a "
      "short and honest answer; got %s" % names)

print("\nnothing is offered until the owner says so")
check(steamgames.offered() == [],
      "an installed game is not a game guests may start")

print("\nand then only what is asked for")
steamgames.write_chosen(["Broforce"])
offered = [g["name"] for g in steamgames.offered()]
check(offered == ["Broforce"], "one on the list is one offered, got %s" % offered)

steamgames.write_chosen(["broforce"])
check([g["name"] for g in steamgames.offered()] == ["Broforce"],
      "matched without regard to case, so nobody has to reproduce Valve's "
      "capitalisation from memory")

steamgames.write_chosen(["1671210"])
check([g["name"] for g in steamgames.offered()] == ["DELTARUNE"],
      "or by appid, which is the one thing that cannot be spelled wrong")

steamgames.write_chosen(["Bro"])
check([g["name"] for g in steamgames.offered()] == ["Broforce"],
      "or by part of the name")

steamgames.write_chosen(["Undertale", "Broforce"])
check([g["name"] for g in steamgames.offered()] == ["Broforce"],
      "and a game on the list that is not installed is quietly not offered, "
      "rather than appearing and failing when somebody presses it")

steamgames.write_chosen(["Broforce", "broforce", "274190"])
check(len(steamgames.offered()) == 1,
      "the same game asked for three ways is offered once")

print("\nthe file is readable by whoever opens it")
steamgames.write_chosen(["Broforce"])
text = open(chosen_file).read()
check(text.lstrip().startswith("#"),
      "it explains itself at the top, since it is meant to be edited")
check("fourth-player steam add" in text,
      "and says which command does this without a text editor")
steamgames.write_chosen(["Broforce  # my favourite"])
check(steamgames.chosen() == ["Broforce"],
      "comments after an entry are ignored, not treated as part of the name")

print("\nhow one is launched and stopped")
from fourthplayer import launcher                                # noqa: E402

row = {"id": "x", "label": "Broforce", "system": "Steam", "short": "STEAM",
       "players": 0, "kind": "steam", "appid": "274190",
       "path": "", "core_path": ""}
check(launcher.steam_game(row) == "274190", "a Steam row is recognised as one")
check(launcher.steam_game({"kind": "rom", "path": "/x"}) is None,
      "and an ordinary one is not")

argv = launcher.build_argv(row)
check(argv[-2:] == ["-applaunch", "274190"],
      "it is started through Steam, by appid; got %s" % argv)
check(not any("ra_players" in part for part in argv),
      "and not through the player picker, which is for emulators and knows "
      "nothing about this")

rom = {"path": "/definitely/not/here", "core_path": "/nor/this",
       "system": "Nintendo - Game Boy", "short": "GB"}
check(launcher.preflight(rom) is not None,
      "an ordinary game with no file is still refused")
check("steam_game(row)" in open(
    os.path.join(ROOT, "fourthplayer", "launcher.py")).read(),
      "and the Steam check comes first, so a row with no file is not turned "
      "away for not having one")

print("\nand the two names that must not be one name")
source = open(os.path.join(ROOT, "fourthplayer", "launcher.py")).read()
check(source.count("def steam_running(") == 1
      and source.count("def steam_game_running(") == 1,
      "steam_running asks whether Steam is up and steam_game_running asks "
      "whether the game is; two functions of one name in one file is how the "
      "later silently replaces the earlier")
stop = source[source.index("def stop_steam_game("):]
stop = stop[:stop.index("\ndef ")]
check(stop.index("pkill") < stop.index("stop_steam()"),
      "the game is closed before Steam is, because Steam going down under a "
      "game that is still saving is how progress goes missing")
check("AppId=%s" in stop,
      "and it is found by the marker Steam puts on what it launches, since "
      "the game's own process name is whatever its developer chose")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_steamgames: all ok")
