"""A tray icon, so a Windows host looks like it is running -- and can be stopped.

A service with no window is indistinguishable from a service that is not
there. On Linux that is what a journal is for; on Windows the honest answer is
an icon by the clock, and a menu behind it.

It runs in its own process rather than the server's, deliberately. A tray icon
is a GUI event loop and this program's is asyncio; marrying them means one can
wedge the other, and the thing that would wedge is the one guests are using.

**It will also start and supervise the host**, which is what makes Restart,
Disable and Quit mean something exact rather than approximate. Two cases, and
the icon behaves the same in both:

  * It started the host. Then it owns the process, and stopping is stopping a
    child it can see -- no guessing whether a service manager will bring it
    back, no orphan left holding the encoder.
  * Something else did -- systemd, a scheduled task, a terminal. Then it asks
    over the control channel and the supervisor does what it was configured
    to do. `Restart` on a systemd host is the server going away and systemd
    putting it back, which is the same thing arrived at from the other end.

Everything it does goes through the control channel or through a process it
started itself, so it is exactly as privileged as `fourth-player status`: the
same user, the same file permissions, no new surface.

One deliberate friction. Stopping the host ends every guest's session, and the
menu is reachable by anyone walking past an unlocked machine, so Disable and
Quit ask first. Restart does not -- it comes back on its own, and a guest's
page reconnects by itself, which is a blip rather than an ending.
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


class Host:
    """The server, whether this process started it or merely found it."""

    def __init__(self, launch=True):
        self.may_launch = launch
        self.child = None

    # -- what is true right now --------------------------------------------

    def reachable(self):
        return bool(_ask({"cmd": "status"}).get("ok"))

    def ours(self):
        return self.child is not None and self.child.poll() is None

    # -- and what can be done about it -------------------------------------

    def start(self):
        """Start one, if there is not already one answering."""
        if self.reachable():
            return True, "already running"
        if not self.may_launch:
            return False, "this icon was told not to start the host"
        import subprocess
        try:
            self.child = subprocess.Popen(
                [sys.executable, "-m", "fourthplayer", "serve"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            return False, str(exc)
        # Long enough for it to bind and answer, short enough that a menu
        # click does not feel like a hang.
        import time
        for _ in range(30):
            time.sleep(0.5)
            if self.reachable():
                return True, "started"
        return False, "it did not answer within fifteen seconds"

    def stop(self):
        """Ask it to go, and make sure it went.

        Asking first matters. A killed server leaves virtual pads plugged in
        and a pipeline halfway to NULL, which on the Linux side is exactly the
        leak that took a day to find; the orderly path releases both.
        """
        import time
        answer = _ask({"cmd": "quit"})
        if answer.get("ok"):
            for _ in range(20):
                time.sleep(0.25)
                if not self.reachable():
                    break
        if self.ours():
            # It was ours and it is still here, so it did not listen.
            try:
                self.child.wait(timeout=5)
            except Exception:
                self.child.kill()
        self.child = None
        return not self.reachable()

    def restart(self):
        self.stop()
        return self.start()


def run(launch=True):
    """Show the icon until it is told to go away. Blocks."""
    try:
        import pystray
    except ImportError:
        print("pystray is missing, so there is no tray icon "
              "(py -m pip install pystray)", file=sys.stderr)
        return 1

    host = Host(launch=launch)
    state = {"open": False, "reachable": False, "busy": ""}

    def status_line(_item=None):
        if state["busy"]:
            return "Fourth Player - " + state["busy"]
        if not state["reachable"]:
            return "Fourth Player - not running"
        return ("Fourth Player - session open" if state["open"]
                else "Fourth Player - idle, no session")

    def confirm(question):
        """Ask before something that ends everybody's evening.

        The menu is reachable by anyone walking past an unlocked machine, and
        Disable and Quit both drop every guest. tkinter because it is in the
        standard library -- a confirmation box is not worth a dependency, and
        a machine without it still gets the action, just without the question.
        """
        try:
            import tkinter
            from tkinter import messagebox
            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            answer = messagebox.askyesno("Fourth Player", question, parent=root)
            root.destroy()
            return answer
        except Exception:
            return True

    def guests_now():
        answer = _ask({"cmd": "status"})
        return len(answer.get("guests") or []) if answer.get("ok") else 0

    # -- the menu ----------------------------------------------------------

    def open_setup(_icon=None, _item=None):
        answer = _ask({"cmd": "setup"})
        if answer.get("ok"):
            webbrowser.open(answer["url"])
        else:
            log.warning("could not open the setup page: %s", answer.get("error"))

    def open_guest(_icon=None, _item=None):
        answer = _ask({"cmd": "status"})
        url = answer.get("join_url") or answer.get("example_url")
        if url:
            webbrowser.open(url)

    def do_restart(icon, _item):
        state["busy"] = "restarting..."
        icon.title = status_line()
        ok, why = host.restart()
        state["busy"] = ""
        if not ok:
            log.warning("could not restart: %s", why)

    def do_enable(icon, _item):
        state["busy"] = "starting..."
        icon.title = status_line()
        ok, why = host.start()
        state["busy"] = ""
        if not ok:
            log.warning("could not start: %s", why)

    def do_disable(icon, _item):
        playing = guests_now()
        question = ("Stop Fourth Player?\n\n%s\n\nThe icon stays, so you can "
                    "start it again from here."
                    % ("%d guest%s will be disconnected."
                       % (playing, "" if playing == 1 else "s")
                       if playing else "Nobody is connected."))
        if not confirm(question):
            return
        state["busy"] = "stopping..."
        icon.title = status_line()
        host.stop()
        state["busy"] = ""

    def do_quit(icon, _item):
        playing = guests_now()
        question = ("Quit Fourth Player entirely?\n\n%s\n\nThe host stops and "
                    "this icon goes away."
                    % ("%d guest%s will be disconnected."
                       % (playing, "" if playing == 1 else "s")
                       if playing else "Nobody is connected."))
        if not confirm(question):
            return
        host.stop()
        icon.stop()

    icon = pystray.Icon(
        "fourth-player", _image(False), "Fourth Player",
        menu=pystray.Menu(
            pystray.MenuItem(status_line, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Set up this machine...", open_setup, default=True),
            pystray.MenuItem("Open the guest page...", open_guest,
                             visible=lambda _i: state["reachable"]),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Restart Fourth Player", do_restart,
                             visible=lambda _i: state["reachable"]),
            pystray.MenuItem("Stop Fourth Player", do_disable,
                             visible=lambda _i: state["reachable"]),
            pystray.MenuItem("Start Fourth Player", do_enable,
                             visible=lambda _i: not state["reachable"]),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", do_quit),
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
                icon.title = status_line()
                icon.update_menu()          # the items that come and go
            except Exception:
                pass                        # the icon has gone away
            time.sleep(3)

    threading.Thread(target=watch, name="tray-watch", daemon=True).start()
    if launch and not host.reachable():
        threading.Thread(target=host.start, name="tray-launch",
                         daemon=True).start()
    icon.run()
    return 0


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(
        prog="fourthplayer.tray", description=__doc__.splitlines()[0])
    parser.add_argument("--no-launch", action="store_true",
                        help="attach to a host that is already running rather "
                             "than starting one")
    parsed = parser.parse_args()
    sys.exit(run(launch=not parsed.no_launch))
