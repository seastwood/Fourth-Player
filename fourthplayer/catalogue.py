"""The list of games a guest may ask for, and nothing else.

This is the whole security model of remote launching. A guest never sends a
path, a core, or a command line: they send an opaque id, and the only thing
that id can resolve to is a row that was already in this catalogue. Everything
launchable is therefore something the host put on disk and RetroArch already
knows how to run, and a guest who makes up an id gets nothing.

The data is kodi-retrobox's, read rather than imported. Fourth Player stays a
separate program with no dependency on that repository -- it looks for the
files sync_games.py maintains, and if they are not there the catalogue is
simply empty and remote launching is unavailable. Coupling through data means
the two can be installed together or apart, which was the point of splitting
them.
"""

import glob
import hashlib
import json
import logging
import os

log = logging.getLogger("fourthplayer.catalogue")

PLAYLIST_DIR = os.path.expanduser("~/.local/share/retroarch/plists")
THUMB_DIR = os.path.expanduser("~/.local/share/retroarch/thumbnails")
# Written by sync_games.py from the libretro databases, with hand-kept
# overrides beating them. counts[system][label] -> how many can play.
PLAYERS = os.path.expanduser("~/.local/share/gameplayers.json")

# RetroArch's system names are long. These are kodi-retrobox's own short forms,
# repeated rather than imported for the reason in the module docstring.
SHORT_NAMES = {
    "Nintendo - Super Nintendo Entertainment System": "Super Nintendo",
    "Nintendo - Nintendo Entertainment System": "NES",
    "Nintendo - Nintendo 64": "Nintendo 64",
    "Nintendo - Game Boy Advance": "Game Boy Advance",
    "Nintendo - Game Boy": "Game Boy",
    "Sega - Mega-CD - Sega CD": "Sega CD",
    "Sega - Mega Drive - Genesis": "Genesis",
}
PREFIXES = ("Nintendo - ", "Sega - ", "Sony - ", "Atari - ", "Microsoft - ")

# Above four the exact number stops mattering: it is already more people than a
# sofa holds. Same buckets the television front end offers.
BUCKET_MAX = 5


def short_name(system):
    if system in SHORT_NAMES:
        return SHORT_NAMES[system]
    for prefix in PREFIXES:
        if system.startswith(prefix):
            return system[len(prefix):]
    return system


def sanitize(name):
    """The thumbnail server's file naming, which sync_games.py follows.

    A label with one of these characters -- "Sonic & Knuckles" -- otherwise
    looks for art under a name nothing on disk uses, and shows none despite the
    file being right there.
    """
    for ch in '&*/:`<>?\\|"':
        name = name.replace(ch, "_")
    return name


def game_id(system, path):
    """A name for a game that carries nothing a guest could act on.

    Not the path: a path in the page is a path in somebody's browser console,
    and the point of this module is that the client cannot express a launch
    that the host did not already offer.
    """
    raw = (system + "\x00" + path).encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()[:16]


def bucket(players):
    """Which player-count filter a game answers to."""
    if not players:
        return None
    return str(players) if players < BUCKET_MAX else "%d+" % BUCKET_MAX


def _playlists():
    try:
        names = sorted(os.listdir(PLAYLIST_DIR))
    except OSError:
        return
    for name in names:
        if not name.endswith(".lpl"):
            continue
        try:
            with open(os.path.join(PLAYLIST_DIR, name)) as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            log.warning("could not read playlist %s: %s", name, exc)
            continue
        yield name[:-len(".lpl")], data.get("items") or []


def _counts():
    try:
        with open(PLAYERS) as handle:
            return json.load(handle).get("counts", {})
    except (OSError, ValueError):
        return {}


# RetroArch's automatic save state, which is what "continue where it was left"
# means. Found by globbing rather than by working the path out: whether these
# are filed under the core's name, under the content directory, or in neither,
# is three settings in retroarch.cfg that can change without this being told --
# and the name it sorts under is the core's short one, not the long one the
# playlist carries.
STATES_DIR = os.path.expanduser("~/.config/retroarch/states")


def saved_state(path):
    """The automatic save state for a ROM, or None."""
    stem = os.path.splitext(os.path.basename(path))[0]
    for pattern in (os.path.join(STATES_DIR, "*", stem + ".state.auto"),
                    os.path.join(STATES_DIR, stem + ".state.auto"),
                    os.path.join(STATES_DIR, "*", "*", stem + ".state.auto")):
        found = sorted(glob.glob(pattern))
        if found:
            return found[0]
    return None


def art_path(system, label):
    """The box art on disk, or None. Snaps and titles are not offered: this is
    a list to choose from on a phone, not a gallery."""
    for name in (sanitize(label), label):
        path = os.path.join(THUMB_DIR, system, "Named_Boxarts", name + ".png")
        if os.path.exists(path):
            return path
    return None


class Catalogue:
    """Everything launchable, rebuilt when the playlists change underneath."""

    def __init__(self):
        self._rows = {}
        self._stamp = None

    def _fingerprint(self):
        """Cheap enough to check on every request, exact enough to notice a
        game being added -- sync_games.py rewrites the playlist file."""
        marks = []
        for path in (PLAYLIST_DIR, PLAYERS):
            try:
                marks.append(os.stat(path).st_mtime_ns)
            except OSError:
                marks.append(0)
        try:
            for name in sorted(os.listdir(PLAYLIST_DIR)):
                if name.endswith(".lpl"):
                    marks.append(
                        os.stat(os.path.join(PLAYLIST_DIR, name)).st_mtime_ns)
        except OSError:
            pass
        return tuple(marks)

    def refresh(self, force=False):
        stamp = self._fingerprint()
        if not force and stamp == self._stamp:
            return
        counts = _counts()
        rows = {}
        for system, items in _playlists():
            for item in items:
                path, label = item.get("path"), item.get("label")
                core = item.get("core_path")
                if not (path and label and core):
                    continue
                players = counts.get(system, {}).get(label) or 0
                key = game_id(system, path)
                rows[key] = {
                    "id": key,
                    "label": label,
                    "system": system,
                    "short": short_name(system),
                    "players": int(players),
                    "path": path,
                    "core_path": core,
                }
        self._rows = rows
        self._stamp = stamp
        log.info("catalogue: %d games across %d systems",
                 len(rows), len({r["system"] for r in rows.values()}))

    def find(self, key):
        """The full row for an id, or None. The only way in."""
        self.refresh()
        return self._rows.get(key)

    def rows(self):
        """Every row, paths and all. For this program, not for a guest.

        listing() is what a page gets and deliberately carries no paths;
        this is for the host asking itself which of its own games is the one
        currently on the television.
        """
        self.refresh()
        return list(self._rows.values())

    def systems(self):
        self.refresh()
        seen = {}
        for row in self._rows.values():
            seen.setdefault(row["system"], row["short"])
        return [{"system": s, "short": n} for s, n in
                sorted(seen.items(), key=lambda pair: pair[1].lower())]

    def listing(self):
        """What the guest's page gets: no paths, no cores, nothing to act on
        except an id this same object will have to recognise again."""
        self.refresh()
        rows = []
        for row in self._rows.values():
            state = saved_state(row["path"])
            rows.append({
                "id": row["id"],
                "label": row["label"],
                "system": row["system"],
                "short": row["short"],
                "players": row["players"],
                "bucket": bucket(row["players"]),
                "art": bool(art_path(row["system"], row["label"])),
                # Whether there is anything to continue from, so the page can
                # offer that only when it is a real choice.
                "saved": bool(state),
                "saved_at": os.path.getmtime(state) if state else None,
            })
        rows.sort(key=lambda r: (r["short"].lower(), r["label"].lower()))
        return rows

    def art(self, key):
        row = self.find(key)
        return art_path(row["system"], row["label"]) if row else None
