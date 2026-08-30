"""The Kodi add-on, without Kodi.

Kodi's python modules only exist inside Kodi, and a new add-on is not visible
until Kodi restarts -- which is not something to do to a television somebody is
playing on. So the modules are stubbed and the add-on is exercised here: the
menu it builds in each state, the control socket it speaks, the config it
writes, and the QR it draws.

What this cannot check is that it looks right on a screen. That needs eyes.
"""
import json
import os
import socket
import sys
import tempfile
import threading
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ADDON = os.path.join(ROOT, "addons", "script.fourthplayer")
sys.path.insert(0, ADDON)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


# -- the stubs -----------------------------------------------------------

chosen = {"select": 0, "yesno": True, "calls": []}


class FakeDialog:
    def select(self, heading, options, **kwargs):
        chosen["calls"].append(("select", heading, list(options)))
        return chosen["select"]

    def ok(self, heading, message):
        chosen["calls"].append(("ok", heading, message))
        return True

    def yesno(self, heading, message, **kwargs):
        chosen["calls"].append(("yesno", heading, message))
        return chosen["yesno"]

    def notification(self, heading, message, icon=None, time=None):
        chosen["calls"].append(("notify", heading, message))


class FakeMonitor:
    def __init__(self): self.abortRequested = lambda: False
    def waitForAbort(self, seconds=0): return True


def _stub(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


PROFILE = tempfile.mkdtemp()

_stub("xbmc", Monitor=FakeMonitor, log=lambda *a, **k: None,
      executebuiltin=lambda *a, **k: None, LOGINFO=1)
_stub("xbmcgui", Dialog=FakeDialog, NOTIFICATION_INFO="i", NOTIFICATION_ERROR="e",
      WindowDialog=type("WindowDialog", (), {
          "__init__": lambda self, *a, **k: None,
          "getWidth": lambda self: 1280, "getHeight": lambda self: 720,
          "addControl": lambda self, c: None, "show": lambda self: None,
          "close": lambda self: None}),
      ControlLabel=lambda *a, **k: types.SimpleNamespace(setLabel=lambda s: None),
      ControlImage=lambda *a, **k: object())
_stub("xbmcvfs", translatePath=lambda p: PROFILE)
_stub("xbmcaddon", Addon=lambda *a: types.SimpleNamespace(
    getAddonInfo=lambda key: PROFILE))

import fpclient as C          # noqa: E402
import main                   # noqa: E402

import panels                 # noqa: E402


# -- a stand-in server ---------------------------------------------------

class FakeServer:
    def __init__(self, reply):
        self.reply = reply
        self.path = os.path.join(tempfile.mkdtemp(), "sock")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(4)
        self.requests = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                data = conn.recv(65536)
                if data:
                    self.requests.append(json.loads(data))
                conn.sendall((json.dumps(self.reply) + "\n").encode())


print("the control socket")
server = FakeServer({"ok": True, "open": False})
C.CONTROL_SOCKET = server.path
check(C.status() == {"ok": True, "open": False}, "status round-trips")
C.extend(15)
check(server.requests[-1] == {"cmd": "extend", "minutes": 15},
      "extend sends the right command, got %r" % (server.requests[-1],))
C.kick(2)
check(server.requests[-1] == {"cmd": "kick", "slot": 2}, "kick sends a slot")

print("\na server that is not there is not an error, it is a prompt")
C.CONTROL_SOCKET = "/nonexistent/fourth-player.sock"
try:
    C.status()
    check(False, "a missing socket was not reported")
except C.NotRunning:
    check(True, "a missing socket raises NotRunning")

print("\nthe menu depends on what is happening")
main.C.CONTROL_SOCKET = server.path
chosen["calls"] = []
chosen["select"] = -1
main.main()
heading, options = chosen["calls"][-1][1], chosen["calls"][-1][2]
check("nothing open" in heading, "with no session the heading says so: %r" % heading)
check(any("Open a session" in o for o in options), "and offers to open one")
check(not any("Add more time" in o for o in options),
      "and does not offer to extend a session that does not exist")

server.reply = {"ok": True, "open": True, "remaining": 754, "slots": 3,
                "url": "https://example.test/j/TOKEN", "pin": "123456",
                "guests": [{"slot": 0, "label": "Player 2", "connected": True,
                            "frames": 900, "pad": "/dev/input/event20"}]}
chosen["calls"] = []
main.main()
heading, options = chosen["calls"][-1][1], chosen["calls"][-1][2]
check("1 playing" in heading and "12 min" in heading,
      "an open session shows who and how long: %r" % heading)
for wanted in ("link, PIN and QR", "Who is playing", "Add more time",
               "Remove a player", "Close the session", "Picture quality"):
    check(any(wanted in o for o in options), "the menu offers %r" % wanted)

print("\na session whose code cannot be read back offers a new one")
# The clear link and PIN are deliberately never written to disk, so a service
# restart leaves a perfectly good session whose code nobody can read. The panel
# used to put a wall of grey on the television advising to close the session
# and start again -- which costs everybody their place. A new pair costs nobody
# anything, because a guest is held by their own token, not the invite's.
server.reply = {"ok": True, "open": True, "remaining": 754, "slots": 3,
                "url": None, "pin": None, "guests": []}
chosen["calls"] = []
main.main()
options = chosen["calls"][-1][2]
check(any("New link and PIN" in o for o in options),
      "the menu offers a new pair: %r" % options)

server.reply = {"ok": True, "open": False}
chosen["calls"] = []
main.main()
options = chosen["calls"][-1][2]
check(not any("New link and PIN" in o for o in options),
      "and does not, with no session to re-share: %r" % options)

print("\nand says what a new pair actually costs, which depends on the mode")
strict = main.reshare_warning({"require_link": True})
loose = main.reshare_warning({"require_link": False})
check("home screen" in strict,
      "with links required, a saved shortcut dies: %r" % strict)
check("still works" in loose,
      "without them, only the PIN changes: %r" % loose)
check(strict != loose, "the two are not the same sentence")

print("\nnothing tells anybody to close a session to get a code")
panel_source = open(os.path.join(ADDON, "panels.py")).read()
check("Close this session" not in panel_source,
      "the old advice is gone from the panel")

print("\nquality presets are written where the server reads them")
config_path = os.path.join(tempfile.mkdtemp(), "config.json")
main.CONFIG_PATH = config_path
chosen["select"] = 2                # the over-the-internet preset
chosen["yesno"] = False             # do not restart anything
main.set_quality(False)
with open(config_path) as handle:
    written = json.load(handle)
wanted = main.QUALITY[2][1]
check(all(written.get(k) == v for k, v in wanted.items()),
      "the chosen preset lands in the config: %r" % written)
check(written["bitrate_kbps"] < 3000,
      "and the remote preset really is thinner than the default")

print("\nevery preset is complete, so switching cannot leave a stale field")
keys = [set(settings) for _, settings in main.QUALITY]
check(all(k == keys[0] for k in keys),
      "all presets set the same fields: %r" % [sorted(k) for k in keys])

existing = {"public_url": "https://play.example.com", "slots": 2}
with open(config_path, "w") as handle:
    json.dump(existing, handle)
chosen["select"] = 1
main.set_quality(False)
with open(config_path) as handle:
    written = json.load(handle)
check(written.get("public_url") == "https://play.example.com" and written["fps"] == 60,
      "and does not trample settings it does not own: %r" % written)

print("\nthe QR is a real image of the real link")
try:
  path = panels.qr_png("https://example.test/j/TOKEN", size=240)
except ImportError:
  path = None
  print("  SKIPPED: python3-qrcode is not installed here; it is on the host")
if path is not None:
  check(os.path.exists(path) and os.path.getsize(path) > 200,
        "a PNG is produced (%d bytes)" % os.path.getsize(path))
  from PIL import Image
  image = Image.open(path)
  check(image.size == (240, 240), "at the size asked for, got %r" % (image.size,))
  colours = {c for _, c in image.convert("RGB").getcolors(maxcolors=100000)}
  check((0, 0, 0) in colours and (255, 255, 255) in colours,
        "and is black on white, not a smudge")

print("\nduration labels read like English")
check(main.duration_label(30) == "30 minutes", "30 minutes")
check(main.duration_label(60) == "1 hour", "1 hour, not 1 hours")
check(main.duration_label(240) == "4 hours", "4 hours")

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
