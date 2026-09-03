"""What a guest's controller may reach, and when it may not.

A guest's pad is a real input device on this machine. It is wired to the
machine and not to the game: whatever has the foreground reads it. That was
tolerable while the only thing a guest could reach was RetroArch, and stopped
being tolerable when Steam arrived -- Steam's gamepad interface is a mouse
pointer, an on-screen keyboard, a store with a saved card in it, the account
settings, a browser, and a button marked "switch to desktop".

No login fixes that. A login gates what the page offers; it cannot gate what
the pad does, because the pad is a kernel device and the reader is whichever
window has focus. So the frames stop at the television while the thing in
front is one guests have no business driving.

Held still here: which windows count as a shell, that a Steam *game* is not
one while Steam's own interface is, that holding lets go of the buttons rather
than unplugging the pad -- unplugging would take RetroArch's port binding with
it and the guest would come back as nobody -- and that a held guest still
counts as present, because the dead-man switch releases pads for silence and
being held is not silence.
"""
import importlib.machinery
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

ldr = importlib.machinery.SourceFileLoader(
    "screen", os.path.join(ROOT, "fourthplayer", "screen.py"))
screen = importlib.util.module_from_spec(
    importlib.util.spec_from_loader("screen", ldr))
ldr.exec_module(screen)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


print("what counts as a shell")
check(screen.is_shell("steamwebhelper steam big picture mode"),
      "Steam's own interface does: a store, a browser and a way to the desktop")
check(screen.is_shell("kodi.bin kodi"),
      "and Kodi, which guests could already drive and should not have been able to")
check(screen.is_shell(""),
      "and nothing at all, which is where a crashed game leaves somebody")
check(not screen.is_shell("retroarch mario golf - toadstool tour"),
      "a game does not: that is the whole point of the connection")
check(not screen.is_shell("portal2_linux portal 2"),
      "and neither does a Steam game, which is why the class is what is read "
      "rather than the fact that Steam started it")
check(not screen.is_shell("python3 fourth player: choose your seat"),
      "nor the player picker, which is how a guest claims a seat at all")

print("the list is a blocklist, and says why")
check("blocklist rather than an allowlist" in open(
          os.path.join(ROOT, "fourthplayer", "screen.py")).read(),
      "the trade is written down where the list is")

print("holding, in the session")
source = open(os.path.join(ROOT, "fourthplayer", "session.py")).read()
held = source.split("def _hold_input")[1].split("\n    def ")[0]
check("pad.release_all()" in held,
      "the buttons are let go of, so nothing is left held on the television")
check(".release(" not in held,
      "and the pad is not unplugged: that would take its port binding with it")
check('"t": "hold"' in held, "the guests are told, rather than left guessing")

feed = source.split("def feed(self, data)")[1].split("\n    def ")[0]
check(feed.index("self.last_input = time.monotonic()")
      < feed.index("if self.session is not None and self.session.input_held"),
      "presence is counted before the hold, or a held guest is reaped for "
      "silence that is not theirs")
check("self.held_frames += 1" in feed,
      "and what was withheld is counted, because somebody will ask")

print("the guest is told in the page")
app = open(os.path.join(ROOT, "web", "app.js")).read()
check('case "hold":' in app, "the page handles the message")
check("Controls paused" in app, "and says so in words")
check("held" in open(os.path.join(ROOT, "web", "style.css")).read(),
      "with the pad dimmed rather than taken away")

print("it can be turned off")
cfg = open(os.path.join(ROOT, "fourthplayer", "config.py")).read()
check("guest_input_needs_a_game: bool = True" in cfg,
      "on by default, because the exposure is real")
check("shell_windows" in cfg and "shell_poll_ms" in cfg,
      "and both the list and how often it is read are settings")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("all good")
