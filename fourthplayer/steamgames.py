"""Steam games the owner has chosen to put on the guest list.

Not every installed game, and deliberately. A Steam library is full of things
that are not games -- on the console this was written for, four of the six
installed "games" are Steam Linux Runtime and Proton -- and beyond that, which
of somebody's library a stranger on a phone may start is theirs to decide, not
this program's. So there is a list, the owner puts games on it, and a game is
offered only if it is on the list *and* actually installed.

What Steam knows is read rather than asked for. Every installed app leaves an
appmanifest_<appid>.acf beside the library it went into, and libraryfolders.vdf
says where the libraries are. Both are Valve's own text format; nothing here
parses more of it than the three fields it needs, because a full VDF parser
would be a lot of code standing between this and a name.
"""
import glob
import json
import logging
import os
import re

log = logging.getLogger("fourthplayer.steam")

# Where Steam keeps itself, in the order the Debian package and the older
# layouts use. The first one that exists is the one this machine has.
STEAM_ROOTS = (
    "~/.steam/debian-installation",
    "~/.steam/steam",
    "~/.local/share/Steam",
    "~/.var/app/com.valvesoftware.Steam/data/Steam",   # the Flathub build
)

# The owner's list. A file rather than a setting in the config, because it is
# edited far more often than anything else here and one game per line reads
# better than a JSON array inside a TOML value.
CHOSEN = os.path.expanduser("~/.local/share/fourth-player-steam.txt")

# Things Valve installs that are not games. They would never be chosen by
# name, but they are skipped from listings so that "what could I add?" is a
# short and honest answer.
NOT_GAMES = re.compile(
    r"^(steam(works)? linux runtime|proton|steam runtime|steamvr)\b", re.I)


def _roots():
    for root in STEAM_ROOTS:
        full = os.path.expanduser(root)
        if os.path.isdir(full):
            yield full


def _libraries():
    """Every steamapps directory this machine has, including extra drives."""
    seen = []
    for root in _roots():
        here = os.path.join(root, "steamapps")
        if os.path.isdir(here) and here not in seen:
            seen.append(here)
        # libraryfolders.vdf lists the others. Read for its "path" values and
        # nothing else: the rest of the file is of no interest here.
        for where in (os.path.join(here, "libraryfolders.vdf"),
                      os.path.join(root, "config", "libraryfolders.vdf")):
            try:
                with open(where, encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError:
                continue
            for path in re.findall(r'"path"\s+"([^"]+)"', text):
                other = os.path.join(path, "steamapps")
                if os.path.isdir(other) and other not in seen:
                    seen.append(other)
    return seen


def installed():
    """Every installed app, as {appid, name}, biggest libraries and all.

    Sorted by name so a listing reads the way somebody expects. Runtimes are
    left out: see NOT_GAMES.
    """
    found = {}
    for library in _libraries():
        for manifest in glob.glob(os.path.join(library, "appmanifest_*.acf")):
            try:
                with open(manifest, encoding="utf-8", errors="replace") as handle:
                    text = handle.read(8192)
            except OSError:
                continue
            appid = re.search(r'"appid"\s+"(\d+)"', text)
            name = re.search(r'"name"\s+"([^"]*)"', text)
            if not (appid and name):
                continue
            if NOT_GAMES.match(name.group(1)):
                continue
            found[appid.group(1)] = name.group(1)
    return [{"appid": appid, "name": name}
            for appid, name in sorted(found.items(), key=lambda kv: kv[1].lower())]


def chosen():
    """What the owner has put on the list: appids and names, as written."""
    try:
        with open(CHOSEN, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def write_chosen(entries):
    """Save the list, with a note at the top for whoever opens it next."""
    os.makedirs(os.path.dirname(CHOSEN), exist_ok=True)
    with open(CHOSEN, "w", encoding="utf-8") as handle:
        handle.write(
            "# Steam games guests may start, one per line, by name or by\n"
            "# appid. A game only appears if it is on this list and actually\n"
            "# installed. Lines starting with # are ignored.\n"
            "#\n"
            "# fourth-player steam installed   -- what is on this machine\n"
            "# fourth-player steam add NAME    -- put one on this list\n")
        for entry in entries:
            handle.write(entry + "\n")


def offered():
    """The games to put in front of guests: chosen and installed both.

    Matched on the appid first, then on the name without regard to case, then
    on the name being contained in it -- so "broforce" finds "Broforce" and
    somebody does not have to reproduce Valve's capitalisation from memory.
    """
    here = installed()
    by_id = {game["appid"]: game for game in here}
    out, seen = [], set()
    for want in chosen():
        game = by_id.get(want)
        if game is None:
            lowered = want.lower()
            game = next((g for g in here if g["name"].lower() == lowered), None)
        if game is None:
            lowered = want.lower()
            game = next((g for g in here if lowered in g["name"].lower()), None)
        if game is None:
            log.info("steam: %r is on the list and not installed", want)
            continue
        if game["appid"] in seen:
            continue
        seen.add(game["appid"])
        out.append(game)
    return out
