"""A tray icon, so a Windows host looks like it is running.

A service with no window is indistinguishable from a service that is not
there. On Linux that is what a journal is for; on Windows the honest answer is
an icon by the clock, and a menu behind it.

It runs in its own process rather than the server's, deliberately. A tray icon
is a GUI event loop and this program's is asyncio; marrying them means one can
wedge the other, and the thing that would wedge is the one guests are using.
A separate process can be closed, crash, or never start at all, and the
session carries on regardless -- which is the right order of importance.

Everything it offers goes through the control channel, so it is exactly as
privileged as `fourth-player status` and no more: the same user, the same
file permissions, no new surface.
"""
import logging
import sys
import threading
import webbrowser

log = logging.getLogger("fourthplayer.tray")

# Drawn rather than shipped as a file. It is a controller in the smallest
# number of shapes that still reads as one at 16 pixels, and an asset that
# cannot go missing is one less thing an installer has to get right.
ICON_SIZE = 64


def _image(running):
    """The icon: a pad, lit when a session is open and grey when it is not."""
    from PIL import Image, ImageDraw

    lit = (255, 212, 71) if running else (120, 126, 136)
    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # The body: a rounded bar, which is what a pad is from a distance.
    draw.rounded_rectangle((6, 20, 58, 48), radius=13, fill=lit)
    # A d-pad and two buttons, in the background colour, so the shape reads
    # at the size this is actually seen at.
    hole = (28, 30, 36)
    draw.rectangle((14, 32, 24, 36), fill=hole)
    draw.rectangle((17, 29, 21, 39), fill=hole)
    draw.ellipse((40, 27, 47, 34), fill=hole)
    draw.ellipse((47, 34, 54, 41), fill=hole)
    return image


def _ask(request):
    """One question for the running server, or a plain answer if none is."""
    import json
    from . import control as control_channel
    try:
        with control_channel.connect(timeout=5) as sock:
            sock.sendall((json.dumps(request) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        return json.loads(data or b"{}")
    except (OSError, ValueError):
        return {"ok": False, "error": "no server is running"}


def run():
    """Show the icon until it is told to go away. Blocks."""
    try:
        import pystray
    except ImportError:
        print("pystray is missing, so there is no tray icon "
              "(py -m pip install pystray)", file=sys.stderr)
        return 1

    state = {"open": False}

    def status_line(_item):
        if not state.get("reachable"):
            return "Fourth Player — not running"
        return ("Fourth Player — session open" if state["open"]
                else "Fourth Player — idle")

    def open_setup(_icon, _item):
        answer = _ask({"cmd": "setup"})
        if answer.get("ok"):
            webbrowser.open(answer["url"])
        else:
            log.warning("could not open the setup page: %s",
                        answer.get("error"))

    def open_guest(_icon, _item):
        answer = _ask({"cmd": "status"})
        url = (answer or {}).get("join_url") or (answer or {}).get("example_url")
        if url:
            webbrowser.open(url)

    def quit_tray(icon, _item):
        # The icon only. Stopping the host from a menu that anybody walking
        # past the machine can click is not a convenience.
        icon.stop()

    icon = pystray.Icon(
        "fourth-player", _image(False), "Fourth Player",
        menu=pystray.Menu(
            pystray.MenuItem(status_line, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Set up this machine…", open_setup, default=True),
            pystray.MenuItem("Open the guest page…", open_guest),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Hide this icon", quit_tray),
        ))

    def watch():
        """Follow the session, so the icon means something."""
        import time
        while True:
            answer = _ask({"cmd": "status"})
            state["reachable"] = bool(answer.get("ok"))
            state["open"] = bool(answer.get("open"))
            try:
                icon.icon = _image(state["open"])
                icon.title = status_line(None)
            except Exception:
                pass                       # the icon has gone away
            time.sleep(5)

    threading.Thread(target=watch, name="tray-watch", daemon=True).start()
    icon.run()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(run())
