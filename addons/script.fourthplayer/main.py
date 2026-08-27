"""Fourth Player, from the sofa.

Opens and closes sessions, shows the link and QR to send people, watches who is
connected, adds time, removes a player, and starts the service when it is not
running.

Everything is one JSON line over the server's Unix socket. No decision lives
here: how long a session may run, who may join, when a pad is released and what
a slot means all belong to the server, which can be tested without Kodi.
"""

import json
import os

import xbmc
import xbmcgui

import fpclient as C
import panels

ADDON_NAME = "Fourth Player"

DURATIONS = [30, 60, 120, 240]
EXTENSIONS = [15, 30, 60]

CONFIG_PATH = os.path.expanduser("~/.config/fourth-player/config.json")

# Frame rate costs more than resolution on the hardware this was built for, so
# the presets trade it first.
QUALITY = [
    ("Same network — 720p30, 6 Mb/s (default)",
     dict(width=1280, height=720, fps=30, bitrate_kbps=6000, keyframe_interval=0)),
    ("Sharper — 720p60, 8 Mb/s",
     dict(width=1280, height=720, fps=60, bitrate_kbps=8000, keyframe_interval=0)),
    ("Over the internet or a VPN — 540p30, 2 Mb/s",
     dict(width=960, height=540, fps=30, bitrate_kbps=2000, keyframe_interval=60)),
    ("As little as it can use — 480p30, 0.8 Mb/s",
     dict(width=854, height=480, fps=30, bitrate_kbps=800, keyframe_interval=90)),
]


def notify(message, error=False, seconds=4000):
    xbmcgui.Dialog().notification(
        ADDON_NAME, message,
        xbmcgui.NOTIFICATION_ERROR if error else xbmcgui.NOTIFICATION_INFO,
        seconds)


def duration_label(minutes):
    if minutes < 60:
        return "%d minutes" % minutes
    if minutes == 60:
        return "1 hour"
    return "%d hours" % (minutes // 60)


# -- the service ---------------------------------------------------------

def offer_to_start_service():
    dialog = xbmcgui.Dialog()
    if not C.service_installed():
        dialog.ok(ADDON_NAME,
                  "The Fourth Player service is not running, and systemd does "
                  "not know about it.\n\n"
                  "Run [B]install/install.sh[/B] from the fourth-player "
                  "repository once, then try again.")
        return False
    if not dialog.yesno(ADDON_NAME,
                        "The Fourth Player service is not running.\n\n"
                        "Start it now?"):
        return False
    code, output = C.start_service()
    if code != 0:
        dialog.ok(ADDON_NAME, "It would not start:\n\n" + (output or "no reason given"))
        return False
    # systemd returns as soon as it has forked; the socket appears shortly after.
    monitor = xbmc.Monitor()
    for _ in range(20):
        if monitor.waitForAbort(0.5):
            return False
        try:
            C.status()
            notify("Service started.")
            return True
        except C.NotRunning:
            continue
    xbmcgui.Dialog().ok(ADDON_NAME, "It started but is not answering yet. "
                                    "Try again in a moment.")
    return False


def stop_service():
    if not xbmcgui.Dialog().yesno(
            ADDON_NAME,
            "Stop the Fourth Player service?\n\n"
            "Any open session ends and nobody can join until it is started again."):
        return
    code, output = C.stop_service()
    notify("Service stopped." if code == 0 else (output or "Could not stop it."),
           error=code != 0)


# -- sessions ------------------------------------------------------------

def start_session():
    dialog = xbmcgui.Dialog()
    choice = dialog.select("How long should the session stay open?",
                           [duration_label(m) for m in DURATIONS])
    if choice < 0:
        return
    reply = C.start_session(DURATIONS[choice])
    if not reply.get("ok"):
        dialog.ok(ADDON_NAME, reply.get("error", "The session would not start."))
        return
    panels.show_invite(C.status)


def extend_session():
    choice = xbmcgui.Dialog().select(
        "Add how much time?", ["%s more" % duration_label(m) for m in EXTENSIONS])
    if choice < 0:
        return
    reply = C.extend(EXTENSIONS[choice])
    if reply.get("ok"):
        notify("%d minutes left." % (reply.get("remaining", 0) // 60))
    else:
        notify(reply.get("error", "Could not add time."), error=True)


def remove_player(status):
    guests = status.get("guests") or []
    if not guests:
        notify("Nobody has joined yet.")
        return
    labels = ["%s — %s" % (g["label"], "playing" if g["connected"] else "away")
              for g in guests]
    choice = xbmcgui.Dialog().select("Remove which player?", labels)
    if choice < 0:
        return
    guest = guests[choice]
    if not xbmcgui.Dialog().yesno(
            ADDON_NAME,
            "Remove %s?\n\nTheir controller stops immediately and the link will "
            "not let them back in." % guest["label"]):
        return
    reply = C.kick(guest["slot"])
    notify("%s removed." % guest["label"] if reply.get("ok")
           else reply.get("error", "Could not remove them."),
           error=not reply.get("ok"))


def close_session():
    if not xbmcgui.Dialog().yesno(
            ADDON_NAME,
            "Close the session?\n\nEveryone is disconnected and the link stops "
            "working."):
        return
    reply = C.stop_session()
    notify("Session closed." if reply.get("ok")
           else reply.get("error", "Could not close it."),
           error=not reply.get("ok"))


# -- quality -------------------------------------------------------------

def set_quality(session_open):
    dialog = xbmcgui.Dialog()
    choice = dialog.select("Picture quality", [name for name, _ in QUALITY])
    if choice < 0:
        return
    _, settings = QUALITY[choice]

    config = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as handle:
                config = json.load(handle)
        except (OSError, ValueError):
            config = {}
    config.update(settings)
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
    except OSError as exc:
        dialog.ok(ADDON_NAME, "Could not save it:\n\n%s" % exc)
        return

    # The pipeline is built when a session opens, so this takes effect then --
    # and the service reads its config at startup, so it needs a restart too.
    if not C.service_installed():
        notify("Saved. Restart the server for it to take effect.")
        return
    prompt = ("Restart the service now so it takes effect?\n\n"
              "The open session will end." if session_open else
              "Restart the service now so it takes effect?")
    if not dialog.yesno(ADDON_NAME, prompt):
        notify("Saved. It applies next time the service starts.")
        return
    code, output = C.restart_service()
    notify("Restarted." if code == 0 else (output or "Could not restart it."),
           error=code != 0)


# -- the menu ------------------------------------------------------------

def main():
    try:
        status = C.status()
    except C.NotRunning:
        if not offer_to_start_service():
            return
        try:
            status = C.status()
        except C.NotRunning:
            return

    if not status.get("ok"):
        xbmcgui.Dialog().ok(ADDON_NAME, status.get("error", "No answer from the service."))
        return

    if not status.get("open"):
        entries = [
            ("Open a session…", start_session),
            ("Picture quality…", lambda: set_quality(False)),
            ("Stop the service", stop_service),
        ]
        heading = "Fourth Player — nothing open"
    else:
        guests = len(status.get("guests") or [])
        remaining = status.get("remaining", 0)
        heading = ("Fourth Player — %d playing, %d min left"
                   % (guests, remaining // 60))
        entries = [
            ("Show the link, PIN and QR code", lambda: panels.show_invite(C.status)),
            ("Who is playing…", lambda: panels.show_monitor(C.status)),
            ("Add more time…", extend_session),
            ("Remove a player…", lambda: remove_player(C.status())),
            ("Close the session", close_session),
            ("Picture quality…", lambda: set_quality(True)),
        ]

    choice = xbmcgui.Dialog().select(heading, [label for label, _ in entries])
    if choice >= 0:
        entries[choice][1]()


if __name__ == "__main__":
    main()
