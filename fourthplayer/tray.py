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
import os
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

    UNIT = "fourth-player"

    def __init__(self, launch=True):
        self.may_launch = launch
        self.child = None
        self.unit = self._systemd_unit()
        self._log = None
        # Set by Stop and Quit, so a host somebody deliberately stopped is not
        # started again by the watchdog two seconds later.
        self.wanted = True
        self.failures = 0

    @staticmethod
    def _systemd_unit():
        """The user service, if this machine runs one.

        A tray that starts its own copy on a machine where systemd already
        manages one ends up with two hosts fighting over a port, a control
        socket and /dev/uinput. So where there is a unit, every button here
        drives *it* -- which also means Restart means what the machine's
        owner already means by it, including whatever Restart= is set to.
        """
        if sys.platform == "win32":
            return None
        import shutil
        import subprocess
        if not shutil.which("systemctl"):
            return None
        try:
            done = subprocess.run(
                ["systemctl", "--user", "is-enabled", Host.UNIT],
                capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        # "disabled" still counts: the unit exists and is the right way to
        # start it, whether or not it comes up at boot.
        if done.returncode == 0 or "disabled" in (done.stdout or ""):
            return Host.UNIT
        return None

    LOG_LIMIT = 4 * 1024 * 1024

    def log_path(self):
        base = (os.environ.get("LOCALAPPDATA")
                or os.path.join(os.path.expanduser("~"), ".local", "state"))
        return os.path.join(base, "fourth-player", "host.log")

    def _open_log(self):
        """Where the host's own output goes, so a crash leaves a trace.

        Started fresh once it is big enough to be unhelpful: this is for
        reading after something went wrong, and scrolling through four
        megabytes to find the end is its own obstacle.
        """
        path = self.log_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) > self.LOG_LIMIT:
                os.replace(path, path + ".1")
            return open(path, "ab", buffering=0)
        except OSError as exc:
            log.warning("no host log (%s); its output will be discarded", exc)
            return None

    def gone(self):
        """Whether a host we started has exited on its own."""
        return self.child is not None and self.child.poll() is not None

    def _systemctl(self, *what):
        import subprocess
        try:
            done = subprocess.run(["systemctl", "--user", *what, self.unit],
                                  capture_output=True, text=True, timeout=30)
            return done.returncode == 0, (done.stderr or done.stdout or "").strip()
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)

    # -- what is true right now --------------------------------------------

    def reachable(self):
        return bool(_ask({"cmd": "status"}).get("ok"))

    def ours(self):
        return self.child is not None and self.child.poll() is None

    # -- and what can be done about it -------------------------------------

    def stale(self):
        """Whether the host answering is running code that has since changed.

        This is the Windows deploy hazard made visible. `schtasks /End` ends
        the tray and not the host it started, so a host orphaned by a restart
        carries on answering on the port -- and `start()` below used to see
        that and say "already running", which is true and useless. The deploy
        looked perfect from every angle: git pulled, the task restarted, the
        log kept moving, and the code being run was three commits old.
        """
        from . import build
        answer = _ask({"cmd": "status"})
        if not answer.get("ok"):
            return False
        was = answer.get("build")
        if not was:
            # A host too old to say. It is by definition not this build.
            return True
        return was != build.stamp()

    def start(self):
        """Start one, if there is not already one answering."""
        self.wanted = True
        self.failures = 0
        if self.reachable():
            if (self.unit or self.may_launch) and self.stale():
                log.warning("the host that is running was started from code "
                            "that has since changed, so it is being replaced "
                            "rather than adopted")
                went = self.stop()
                self.wanted = True
                if not went:
                    # Still answering. Starting a second one only produces a
                    # port collision and a confusing log, and the old one is
                    # at least serving somebody.
                    log.warning("the old host would not go, so it is being "
                                "left alone; it is not this build")
                    return True, "already running (an older build)"
            else:
                return True, "already running"
        if self.unit:
            ok, why = self._systemctl("start")
            return (ok, "started" if ok else why)
        if not self.may_launch:
            return False, "this icon was told not to start the host"
        import subprocess
        try:
            # Not DEVNULL. The host crashed twice on this machine and its
            # output went nowhere, so there was nothing to read afterwards but
            # a Windows event log entry. A supervisor that discards what it
            # supervises is no supervisor.
            self._log = self._open_log()
            self.child = subprocess.Popen(
                [sys.executable, "-m", "fourthplayer", "serve"],
                stdout=self._log or subprocess.DEVNULL,
                stderr=subprocess.STDOUT if self._log else subprocess.DEVNULL)
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
        self.wanted = False              # do not undo this a moment later
        if self.unit:
            # Through systemd, so it stays stopped rather than being brought
            # straight back by the restart policy.
            ok, why = self._systemctl("stop")
            if not ok:
                log.warning("systemctl stop said: %s", why)
            for _ in range(20):
                time.sleep(0.25)
                if not self.reachable():
                    break
            return not self.reachable()
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
        if self.unit:
            ok, why = self._systemctl("restart")
            if not ok:
                return False, why
            import time
            for _ in range(30):
                time.sleep(0.5)
                if self.reachable():
                    return True, "restarted"
            return False, "it did not answer after restarting"
        self.stop()
        return self.start()


class Tray:
    """The icon's behaviour, with no opinion about who draws it.

    Two backends draw it, and which one is available decides. pystray on
    Windows; GTK's AppIndicator on Linux, where PyGObject and
    AyatanaAppIndicator3 are already installed for GStreamer and the on-screen
    card -- so a Linux host needs nothing new at all, which is worth more than
    one library serving both.
    """

    def __init__(self, launch=True):
        self.host = Host(launch=launch)
        self.open = False
        self.reachable = False
        self.busy = ""
        self.stopping = False

    # -- what it says ------------------------------------------------------

    def title(self):
        if self.busy:
            return "Fourth Player - " + self.busy
        if not self.reachable:
            return "Fourth Player - not running"
        return ("Fourth Player - session open" if self.open
                else "Fourth Player - idle, no session")

    def poll(self):
        answer = _ask({"cmd": "status"})
        self.reachable = bool(answer.get("ok"))
        self.open = bool(answer.get("open"))
        self._revive()

    # How long to wait before trying again, by how many times it has failed.
    # A host that dies on startup -- a missing plugin, a port already taken --
    # would otherwise be restarted several times a second for ever, filling a
    # disk with the same traceback.
    BACKOFF = (2, 5, 15, 60, 300)

    def _revive(self):
        """Start the host again if it died on its own.

        Only a host this icon started, and only one nobody asked to stop. The
        gap this fills: the host crashed, the icon went grey, and nothing
        brought it back -- the page simply stopped loading, with the tray
        sitting there saying "not running" as though that were a report rather
        than a problem.
        """
        if self.reachable or not self.host.wanted or not self.host.may_launch:
            return
        if self.host.unit or self.host.child is None:
            # systemd has its own opinion about restarting, and a host this
            # icon never started is not this icon's to resurrect.
            return
        if not self.host.gone():
            return                      # still alive, just not answering yet
        import time
        wait = self.BACKOFF[min(self.host.failures, len(self.BACKOFF) - 1)]
        if time.monotonic() - getattr(self, "_last_try", 0) < wait:
            return
        self._last_try = time.monotonic()
        self.host.failures += 1
        log.warning("the host is gone; starting it again (attempt %d, its "
                    "output is in %s)", self.host.failures, self.host.log_path())
        self.busy = "restarting after a crash..."
        started, why = self.host.start()
        self.busy = ""
        if started:
            self.host.failures = 0
        else:
            log.warning("could not start it: %s", why)

    def guests_now(self):
        answer = _ask({"cmd": "status"})
        return len(answer.get("guests") or []) if answer.get("ok") else 0

    # -- what it does ------------------------------------------------------

    def open_setup(self):
        answer = _ask({"cmd": "setup"})
        if answer.get("ok"):
            webbrowser.open(answer["url"])
        else:
            log.warning("could not open the setup page: %s", answer.get("error"))

    def open_guest(self):
        answer = _ask({"cmd": "status"})
        url = answer.get("url") or answer.get("example_url")
        if url:
            webbrowser.open(url)

    def restart(self):
        self.busy = "restarting..."
        ok, why = self.host.restart()
        self.busy = ""
        if not ok:
            log.warning("could not restart: %s", why)

    def enable(self):
        self._last_try = 0
        self.busy = "starting..."
        ok, why = self.host.start()
        self.busy = ""
        if not ok:
            log.warning("could not start: %s", why)

    def disable(self, confirm):
        if not confirm(self._ending("The icon stays, so you can start it "
                                    "again from here.")):
            return
        self.busy = "stopping..."
        self.host.stop()
        self.busy = ""

    def quit(self, confirm):
        if not confirm(self._ending("The host stops and this icon goes away.")):
            return False
        self.host.stop()
        self.stopping = True
        return True

    def _ending(self, tail):
        playing = self.guests_now()
        return ("Stop Fourth Player?\n\n%s\n\n%s"
                % ("%d guest%s will be disconnected."
                   % (playing, "" if playing == 1 else "s")
                   if playing else "Nobody is connected.", tail))


def _confirm(question):
    """Ask before something that ends everybody's evening.

    The menu is reachable by anyone walking past an unlocked machine, and Stop
    and Quit both drop every guest. tkinter because it is in the standard
    library -- a confirmation box is not worth a dependency, and a machine
    without it still gets the action, just without the question.
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


def _run_pystray(tray):
    import pystray

    icon = pystray.Icon(
        "fourth-player", _image(False), "Fourth Player",
        menu=pystray.Menu(
            pystray.MenuItem(lambda _i: tray.title(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Set up this machine...",
                             lambda _i, _m: tray.open_setup(), default=True),
            pystray.MenuItem("Open the guest page...",
                             lambda _i, _m: tray.open_guest(),
                             visible=lambda _i: tray.reachable),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Restart Fourth Player",
                             lambda _i, _m: tray.restart(),
                             visible=lambda _i: tray.reachable),
            pystray.MenuItem("Stop Fourth Player",
                             lambda _i, _m: tray.disable(_confirm),
                             visible=lambda _i: tray.reachable),
            pystray.MenuItem("Start Fourth Player",
                             lambda _i, _m: tray.enable(),
                             visible=lambda _i: not tray.reachable),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit",
                             lambda i, _m: tray.quit(_confirm) and i.stop()),
        ))

    def watch():
        import time
        while True:
            # The whole body, not just the drawing. tray.poll() restarts a
            # host that has died, and an exception anywhere in it used to end
            # this thread outright -- leaving the icon frozen on whatever it
            # last said and nothing watching the host at all. A watchdog that
            # can die quietly is worse than none, because it looks like one.
            try:
                tray.poll()
                icon.icon = _image(tray.open)
                icon.title = tray.title()
                icon.update_menu()
            except Exception:
                log.exception("the tray watch stumbled; carrying on")
            time.sleep(3)

    threading.Thread(target=watch, name="tray-watch", daemon=True).start()
    icon.run()
    return 0


# What the two icons are called, without the .png. AppIndicator takes an icon
# *name* and looks it up in a theme directory -- handing it a full path looks
# like it should work, produces no error, and the indicator then never
# registers at all: Gtk.main() runs, nothing appears, and nothing is logged.
# That cost an hour, so: a directory, and names.
ICON_NAMES = {False: "fourth-player-idle", True: "fourth-player-open"}


def _icon_files():
    """Write the two icons somewhere AppIndicator can look them up.

    Two files rather than one rewritten, because it reloads on the name
    changing and would not notice the same name with new bytes.

    Returns the directory to hand to set_icon_theme_path.
    """
    import tempfile
    where = os.path.join(tempfile.gettempdir(),
                         "fourth-player-tray-%d" % os.getuid()
                         if hasattr(os, "getuid") else "fourth-player-tray")
    os.makedirs(where, exist_ok=True)
    for lit, name in ICON_NAMES.items():
        _image(lit).save(os.path.join(where, name + ".png"))
    return where


def _run_appindicator(tray):
    """The Linux icon, on what a Mint or Ubuntu desktop already has.

    No new dependency: PyGObject is here for GStreamer and AyatanaAppIndicator3
    for the desktop itself, which is why this exists rather than asking for
    pystray on a machine that may not even have pip -- retro does not.
    """
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import Gtk, GLib, AyatanaAppIndicator3 as AppIndicator

    where = _icon_files()
    indicator = AppIndicator.Indicator.new(
        "fourth-player", ICON_NAMES[False],
        AppIndicator.IndicatorCategory.APPLICATION_STATUS)
    # Before set_status, so the first icon it looks for is already findable.
    indicator.set_icon_theme_path(where)
    indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

    items = {}

    def menu():
        made = Gtk.Menu()
        items["title"] = Gtk.MenuItem(label=tray.title())
        items["title"].set_sensitive(False)
        made.append(items["title"])
        made.append(Gtk.SeparatorMenuItem())

        def add(key, label, action):
            item = Gtk.MenuItem(label=label)
            # Off the GTK thread: stopping a host takes seconds, and doing it
            # in the click handler freezes the whole desktop's panel.
            item.connect("activate", lambda _w: threading.Thread(
                target=action, daemon=True).start())
            made.append(item)
            items[key] = item
            return item

        add("setup", "Set up this machine\u2026", tray.open_setup)
        add("guest", "Open the guest page\u2026", tray.open_guest)
        made.append(Gtk.SeparatorMenuItem())
        add("restart", "Restart Fourth Player", tray.restart)
        add("stop", "Stop Fourth Player", lambda: tray.disable(_confirm))
        add("start", "Start Fourth Player", tray.enable)
        made.append(Gtk.SeparatorMenuItem())
        add("quit", "Quit", lambda: tray.quit(_confirm) and GLib.idle_add(Gtk.main_quit))
        made.show_all()
        return made

    indicator.set_menu(menu())

    def refresh():
        """On the GTK thread, because everything here touches widgets."""
        items["title"].set_label(tray.title())
        indicator.set_icon_full(ICON_NAMES[tray.open], "Fourth Player")
        for key, want in (("guest", tray.reachable), ("restart", tray.reachable),
                          ("stop", tray.reachable), ("start", not tray.reachable)):
            items[key].set_visible(want)
        return False

    def watch():
        import time
        while True:
            try:
                tray.poll()
                GLib.idle_add(refresh)
            except Exception:
                log.exception("the tray watch stumbled; carrying on")
            time.sleep(3)

    threading.Thread(target=watch, name="tray-watch", daemon=True).start()
    Gtk.main()
    return 0


def run(launch=True):
    """Show the icon until it is told to go away. Blocks."""
    tray = Tray(launch=launch)

    backends = []
    try:
        import pystray                                       # noqa: F401
        backends.append(_run_pystray)
    except ImportError:
        pass
    if sys.platform != "win32":
        backends.append(_run_appindicator)

    if launch and not tray.host.reachable():
        threading.Thread(target=tray.host.start, name="tray-launch",
                         daemon=True).start()

    for backend in backends:
        try:
            return backend(tray)
        except Exception as exc:
            log.warning("%s could not draw the icon: %s",
                        backend.__name__, exc)
    print("no way to draw a tray icon here. On Linux this wants PyGObject "
          "with AyatanaAppIndicator3 (gir1.2-ayatanaappindicator3-0.1); "
          "on Windows, pystray.", file=sys.stderr)
    return 1


def _log_to_file():
    """The icon's own log, beside the host's.

    It runs under pythonw with no console, so anything it says otherwise goes
    nowhere -- which is how a watchdog that stopped watching went unnoticed.
    """
    try:
        base = (os.environ.get("LOCALAPPDATA")
                or os.path.join(os.path.expanduser("~"), ".local", "state"))
        path = os.path.join(base, "fourth-player", "tray.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > 2 * 1024 * 1024:
            os.replace(path, path + ".1")
        logging.basicConfig(
            level=logging.INFO, filename=path, filemode="a",
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        return path
    except OSError:
        logging.basicConfig(level=logging.INFO)
        return None


if __name__ == "__main__":
    import argparse

    _log_to_file()
    parser = argparse.ArgumentParser(
        prog="fourthplayer.tray", description=__doc__.splitlines()[0])
    parser.add_argument("--no-launch", action="store_true",
                        help="attach to a host that is already running rather "
                             "than starting one")
    parsed = parser.parse_args()
    sys.exit(run(launch=not parsed.no_launch))
