"""The Kodi front end: open a session, show the link, see who is on, close it.

Deliberately thin. Everything here is one JSON line over the server's Unix
socket, and every decision -- how long a session may run, who may join, when a
pad is released -- belongs to the server, which is testable without Kodi.

The socket is the reason this needs no authentication of its own: it is a file
in the user's runtime directory, reachable only by processes already running as
that user on this machine.
"""

import json
import os
import socket

import xbmc
import xbmcgui

ADDON_NAME = "Fourth Player"
CONTROL_SOCKET = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid()),
    "fourth-player.sock")

DURATIONS = [30, 60, 120, 240]
EXTENSIONS = [15, 30, 60]


def ask(request, timeout=10):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(CONTROL_SOCKET)
            sock.sendall((json.dumps(request) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        return json.loads(data or b"{}")
    except FileNotFoundError:
        return {"ok": False, "error": "The Fourth Player service is not running."}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def notify(message, icon=xbmcgui.NOTIFICATION_INFO, seconds=4000):
    xbmcgui.Dialog().notification(ADDON_NAME, message, icon, seconds)


def start():
    dialog = xbmcgui.Dialog()
    labels = ["%d minutes" % m if m < 60 else
              ("1 hour" if m == 60 else "%d hours" % (m // 60)) for m in DURATIONS]
    choice = dialog.select("How long should the session stay open?", labels)
    if choice < 0:
        return
    reply = ask({"cmd": "start", "minutes": DURATIONS[choice]})
    if not reply.get("ok"):
        dialog.ok(ADDON_NAME, reply.get("error", "The session would not start."))
        return
    show(reply)


def show(status):
    """The link and PIN, in a form that can be read off a television."""
    if not status.get("url"):
        xbmcgui.Dialog().ok(
            ADDON_NAME,
            "The session is open, but the link and PIN were forgotten when the "
            "service restarted.\n\nClose it and open a new one to get a fresh pair.")
        return
    remaining = status.get("remaining", 0)
    xbmcgui.Dialog().ok(
        ADDON_NAME,
        "Send this link to whoever is joining:\n\n"
        "[B]%s[/B]\n\n"
        "PIN: [B]%s[/B]\n\n"
        "%d of %d slots free · %d minutes left\n\n"
        "The code is on the television for as long as the session is open."
        % (status["url"], status["pin"],
           status.get("slots", 0) - len(status.get("guests") or []),
           status.get("slots", 0), remaining // 60))


def manage(status):
    guests = status.get("guests") or []
    if not guests:
        notify("Nobody has joined yet.")
        return
    labels = ["%s — %s, %d frames" % (g["label"],
                                      "connected" if g["connected"] else "away",
                                      g["frames"]) for g in guests]
    choice = xbmcgui.Dialog().select("Remove which player?", labels)
    if choice < 0:
        return
    guest = guests[choice]
    if not xbmcgui.Dialog().yesno(
            ADDON_NAME,
            "Remove %s?\n\nTheir controller stops working immediately and the "
            "link will not let them back in." % guest["label"]):
        return
    reply = ask({"cmd": "kick", "slot": guest["slot"]})
    notify("%s removed." % guest["label"] if reply.get("ok")
           else reply.get("error", "Could not remove them."))


def extend():
    dialog = xbmcgui.Dialog()
    labels = ["%d more minutes" % m if m < 60 else "1 more hour" for m in EXTENSIONS]
    choice = dialog.select("Add how much time?", labels)
    if choice < 0:
        return
    reply = ask({"cmd": "extend", "minutes": EXTENSIONS[choice]})
    if reply.get("ok"):
        notify("Session now has %d minutes left." % (reply.get("remaining", 0) // 60))
    else:
        notify(reply.get("error", "Could not extend it."), xbmcgui.NOTIFICATION_ERROR)


def stop():
    if not xbmcgui.Dialog().yesno(
            ADDON_NAME, "Close the session?\n\nEveryone is disconnected and the "
                        "link stops working."):
        return
    reply = ask({"cmd": "stop"})
    notify("Session closed." if reply.get("ok")
           else reply.get("error", "Could not close it."),
           xbmcgui.NOTIFICATION_INFO if reply.get("ok") else xbmcgui.NOTIFICATION_ERROR)


def main():
    status = ask({"cmd": "status"})
    if not status.get("ok"):
        xbmcgui.Dialog().ok(ADDON_NAME, status.get("error", "No answer from the service."))
        return

    if not status.get("open"):
        start()
        return

    guests = len(status.get("guests") or [])
    remaining = status.get("remaining", 0)
    choice = xbmcgui.Dialog().select(
        "Session open · %d playing · %d min left" % (guests, remaining // 60),
        ["Show the link and PIN", "Add more time", "Remove a player",
         "Close the session"])
    if choice == 0:
        show(status)
    elif choice == 1:
        extend()
    elif choice == 2:
        manage(status)
    elif choice == 3:
        stop()


if __name__ == "__main__":
    main()
