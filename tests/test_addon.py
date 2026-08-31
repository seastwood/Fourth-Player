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
import re
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

    def numeric(self, kind, heading, **kwargs):
        chosen["calls"].append(("numeric", heading, kind))
        return chosen.get("numeric", "")

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

print("\nthe invite shows what a guest actually has to be given")
# With the link not required, the address plus the PIN is the whole of it --
# and that address is short enough to read down a telephone, which the
# tokenised link is not. Showing the long one anyway gave no sign the setting
# had taken effect.
shown = {}
panels.xbmcgui.ControlLabel = lambda x, y, w, h, text, textColor=None: (
    shown.setdefault("labels", []).append(text)
    or types.SimpleNamespace(setLabel=lambda s: None))


def invite_labels(reply):
    shown["labels"] = []
    calls = {"n": 0}

    def get_status():
        calls["n"] += 1
        return reply if calls["n"] == 1 else {"ok": True, "open": False}

    panels.show_invite(get_status, lambda: True)
    return " | ".join(shown["labels"])


base = {"ok": True, "open": True, "remaining": None, "slots": 3,
        "url": "https://play.example.com/j/LONGTOKEN0123456789",
        "base_url": "https://play.example.com", "pin": "123456", "guests": []}

with_link = invite_labels(dict(base, require_link=True))
check("play.example.com/j/LONGTOKEN0123456789" in with_link,
      "with the link required, the link is shown: %r" % with_link)

without = invite_labels(dict(base, require_link=False))
check("play.example.com" in without, "without it, the address is shown")
check("LONGTOKEN" not in without,
      "and the token nobody needs is not: %r" % without)
check("go to" in without.lower(),
      "worded for reading out rather than opening: %r" % without)
check("123456" in without and "123456" in with_link, "the PIN either way")

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
# By name, not by position: the list is reordered whenever a new preset earns
# a place near the top, and an index silently starts meaning something else.
thin = next(i for i, (name, _) in enumerate(main.QUALITY)
            if "Poor connection" in name)
chosen["select"] = thin
chosen["yesno"] = False             # do not restart anything
main.set_quality(False)
with open(config_path) as handle:
    written = json.load(handle)
wanted = main.QUALITY[thin][1]
check(all(written.get(k) == v for k, v in wanted.items()),
      "the chosen preset lands in the config: %r" % written)
check(written["bitrate_kbps"] < main.QUALITY[0][1]["bitrate_kbps"],
      "and the poor-connection preset really is thinner than the default")

# The default is the first entry and says so, and it is the thirty-frame one:
# asking a slow encoder for sixty gets fewer frames, not more.
check("(default)" in main.QUALITY[0][0],
      "the first preset is marked as the default: %r" % main.QUALITY[0][0])
check(main.QUALITY[0][1]["fps"] == 30,
      "and it is the thirty-frame one: %r" % main.QUALITY[0][1]["fps"])
check(sum("(default)" in n for n, _ in main.QUALITY) == 1,
      "exactly one preset claims to be the default")
big = [s for n, s in main.QUALITY if "1080p" in n]
check(len(big) == 2 and {s["fps"] for s in big} == {30, 60},
      "both 1080p options exist, at thirty and sixty: %r" % big)
check(all(s["width"] == 1920 and s["height"] == 1080 for s in big),
      "and both really are 1080p")
# Both 1080p entries warn, and they are last. On the machine this was written
# for they hang the graphics card in ninety seconds -- vce_v3_0, the hardware
# encoder, taking the game and the stream with it -- so the warning belongs in
# the name somebody reads while choosing, not in a note further down.
# 1080p30 is the default: capturing a 1080p desktop at 720p downscales it
# before the encoder sees it, and that softness is lost detail rather than
# lost bitrate. The card's hangs turned out to be one particular game rather
# than a resolution, so the resolution is chosen on how it looks.
check(main.QUALITY[0][1]["height"] == 1080 and main.QUALITY[0][1]["fps"] == 30,
      "the default captures at the screen's own size, at thirty: %r"
      % main.QUALITY[0][1])
sixty = [n for n, s in main.QUALITY if s["height"] == 1080 and s["fps"] == 60]
# It used to say "delivers fewer frames here", which was measured on the
# Radeon this was written on and is a guess about anybody else's machine. The
# warning still has to be there -- 1080p60 is the one preset that asks more of
# an encoder than a modest one has -- but as a requirement, not a verdict on
# hardware the reader may not own.
check(sixty and "encoder" in sixty[0],
      "the sixty-frame one still warns what it needs: %r" % sixty)

print("\nevery preset is complete, so switching cannot leave a stale field")
keys = [set(settings) for _, settings in main.QUALITY]
check(all(k == keys[0] for k in keys),
      "all presets set the same fields: %r" % [sorted(k) for k in keys])

existing = {"public_url": "https://play.example.com", "slots": 2}
with open(config_path, "w") as handle:
    json.dump(existing, handle)
# By name again. This was index 1, which meant the sixty-frame preset until
# the list was reordered and then quietly meant something else -- the third
# time an index in this file has gone stale under a reordering.
sixty = next(i for i, (_n, st) in enumerate(main.QUALITY) if st["fps"] == 60)
chosen["select"] = sixty
main.set_quality(False)
with open(config_path) as handle:
    written = json.load(handle)
check(written.get("public_url") == "https://play.example.com"
      and written.get("slots") == 2,
      "and does not trample settings it does not own: %r" % written)
check(written["fps"] == 60, "while writing the preset that was chosen")

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

print("\nsetting a PIN from the television")
# Reading six new digits off the screen before anybody can join is the chore
# this removes. What it costs -- one secret that stops changing -- has to be
# said before it is done, not discovered later.
sent = []
main.C.set_pin = lambda pin: (sent.append(pin), {"ok": True})[1]

chosen.update(select=0, yesno=True, numeric="246813", calls=[])
main.choose_pin({"pin_fixed": False})
check(sent == ["246813"], "the digits typed are the ones sent: %s" % sent)
warned = [c for c in chosen["calls"] if c[0] == "yesno"]
check(warned and "stops changing" in warned[0][2],
      "and it says what the trade is first")
check(any(c[0] == "numeric" for c in chosen["calls"]),
      "asked on the number pad, which is what a remote has")

print("\nand backing out of that warning changes nothing")
sent.clear()
chosen.update(select=0, yesno=False, numeric="246813", calls=[])
main.choose_pin({"pin_fixed": False})
check(sent == [], "saying no sets nothing")

print("\ntyping nothing changes nothing either")
sent.clear()
chosen.update(select=0, yesno=True, numeric="", calls=[])
main.choose_pin({"pin_fixed": False})
check(sent == [], "an empty keypad is a cancelled dialog, not 'clear the PIN'")

print("\ngoing back to a random PIN each session")
sent.clear()
chosen.update(select=1, yesno=True, calls=[])
main.choose_pin({"pin_fixed": True})
check(sent == [""], "clearing it sends an empty PIN: %s" % sent)
sent.clear()
chosen.update(select=1, yesno=True, calls=[])
main.choose_pin({"pin_fixed": False})
check(sent == [], "and choosing what is already true asks the service nothing")

print("\nwhat the service refuses is what the television says")
main.C.set_pin = lambda pin: {"ok": False, "error": "A set PIN must be "
                              "between 4 and 12 digits."}
chosen.update(select=0, yesno=True, numeric="12", calls=[])
main.choose_pin({"pin_fixed": False})
told = [c for c in chosen["calls"] if c[0] == "notify"]
check(told and "4 and 12" in told[-1][2],
      "the service's own reason is shown, not a guess: %s" % (told[-1:],))

print("\nsharing a controller, from the television")
shared = []
main.C.set_share = lambda on: (shared.append(on), {"ok": True})[1]
chosen.update(select=1, calls=[])
main.choose_share({"share_pads": False})
check(shared == [True], "turning it on sends True: %s" % shared)
shared.clear()
chosen.update(select=0, calls=[])
main.choose_share({"share_pads": True})
check(shared == [False], "and turning it off sends False: %s" % shared)
shared.clear()
chosen.update(select=0, calls=[])
main.choose_share({"share_pads": False})
check(shared == [], "choosing what is already true asks nothing")

print("\nboth are reachable from the menu, open session or not")
src = open(os.path.join(ROOT, "addons", "script.fourthplayer", "main.py")).read()
# Twice in the menus, plus once where each is defined.
check(src.count("lambda: choose_pin(status)") == 2,
      "the PIN screen is offered whether or not a session is open")
check(src.count("lambda: choose_share(status)") == 2,
      "and so is the shared-controller screen")

print("\nthe controller question does not put a number on it")
share_src = src[src.index("def choose_share"):]
share_src = share_src[:share_src.index("\ndef ")]
check("two people" not in share_src and "two guests" not in share_src,
      "nothing in the screen says two: any number of guests can share a pad, "
      "and the wording should not be the thing that limits it")
check("Can players share a controller?" in src,
      "it asks about players, not a pair")


print("\nthe quality list says which one is in force")
# Six unmarked lines and no way to tell which you are on: the only way to find
# out was to pick one and see whether anything changed.
big, over_net, same_net = main.QUALITY[0][1], main.QUALITY[1][1], main.QUALITY[2][1]
check(main.current_quality(dict(big)) == 0, "the saved numbers name their preset")
check(main.current_quality(dict(over_net)) == 1, "and a different one, its own")
check(main.current_quality({}) is None, "nothing saved matches nothing")
check(main.current_quality({"width": 1234, "height": 5, "fps": 7,
                            "bitrate_kbps": 9}) is None,
      "and settings that are not a preset are not forced into one")

# Extra keys alongside a preset must not stop it matching: the file holds every
# other setting too, and a preset is only the handful of keys it writes.
mixed = dict(big)
mixed.update({"slots": 4, "fixed_pin": "", "tls": True})
check(main.current_quality(mixed) == 0,
      "a preset is still recognised beside the rest of the config")

print("\nand the marker is on the active line, not the first one")
main.CONFIG_PATH = os.path.join(PROFILE, "quality.json")
with open(main.CONFIG_PATH, "w") as fh:
    json.dump(dict(same_net), fh)
chosen.update(select=-1, calls=[])
main.set_quality(False)
listed = [c for c in chosen["calls"] if c[0] == "select"]
check(listed, "the picker was shown")
options = listed[-1][2] if listed else []
marked = [o for o in options if o.startswith("> ")]
check(len(marked) == 1, "exactly one line is marked, got %r" % marked)
check(marked and marked[0].lstrip("> ").startswith("Same network"),
      "and it is the one that is saved: %r" % (marked[:1],))
check(options and options[0].startswith("   "),
      "the default is not marked just for being first: %r" % (options[:1],))

print("\nsettings that are not a preset say so rather than lying")
with open(main.CONFIG_PATH, "w") as fh:
    json.dump({"width": 800, "height": 600, "fps": 24, "bitrate_kbps": 999}, fh)
chosen.update(select=-1, calls=[])
main.set_quality(False)
options = [c for c in chosen["calls"] if c[0] == "select"][-1][2]
check(not any(o.startswith("> ") for o in options),
      "nothing is claimed as active")
check(any("not one of these" in o for o in options),
      "and what is actually in force is shown: %r" % (options[-1:],))

print("\npicking the one already in force does not restart anything")
with open(main.CONFIG_PATH, "w") as fh:
    json.dump(dict(big), fh)
restarted = []
main.C.restart_service = lambda: (restarted.append(True), (0, ""))[1]
chosen.update(select=0, yesno=True, calls=[])
main.set_quality(False)
check(not restarted, "no restart for a setting that did not change")
told = [c for c in chosen["calls"] if c[0] == "notify"]
check(told and "Already" in told[-1][2], "and it says so: %r" % (told[-1:],))

print("\nno preset label claims a measurement from one particular machine")
# "(delivers fewer frames here)" was true of the Radeon this was written on and
# is a guess about anybody else's. A list shipped to other people should say
# what a setting needs, not what it did once on a GPU they do not have.
for name, _settings in main.QUALITY:
    check("here" not in name.split("(")[-1],
          "%r describes the setting, not this box" % name)

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
