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


print("reading what is in front")
# getwindowclassname is not in every xdotool -- it is missing from the one on
# the console this runs on, which left the class half silently empty and every
# decision being made on the window's title alone. A fullscreen game with no
# title then read as nothing in front, and nothing in front holds every
# controller in the session.
asked = []


def fake_sh(*argv):
    asked.append(argv)
    if argv[:2] == ("xdotool", "getactivewindow"):
        return "12345"
    if argv[:2] == ("xdotool", "getwindowclassname"):
        return ""                      # this machine has no such command
    if argv[0] == "xprop":
        return 'WM_CLASS(STRING) = "broforce.x86_64", "Broforce.x86_64"'
    if argv[:2] == ("xdotool", "getwindowname"):
        return ""                      # a fullscreen window with no title
    return ""


real_sh = screen.sh
screen.sh = fake_sh
front = screen.foreground()
screen.sh = real_sh
check("broforce" in front,
      "the class is found through xprop when xdotool cannot give it: %r" % front)
check(any(a[0] == "xprop" for a in asked), "xprop was asked")
check(not screen.is_shell(front), "and a game read that way is not a shell")

print("\nwhat counts as a shell")
# Steam deliberately does not count as a shell any more. It was one, on the
# reasoning that its own window is a store and a settings screen and no guest's
# business -- and what that cost was every controller in the session, because
# Steam's loader, its overlay and Big Picture come to the front constantly
# while a game runs, and Big Picture could not be driven from a phone at all.
# It is a thing you play now, like the emulator.
check(not screen.is_shell("steamwebhelper steam big picture mode"),
      "Steam's own interface does not: it is driven with a controller, and "
      "holding it held every guest in the middle of a game")
check(screen.is_shell("kodi.bin kodi"),
      "and Kodi, which guests could already drive and should not have been able to")
# Moonlight is the awkward one: its chooser and its stream are the same
# window, so this costs guests a streamed game they could otherwise have
# played together. It is here because the other way round hands somebody in
# another house the keyboard and mouse of a second machine in this one, and
# that is not a thing to do by accident. The way out is the host naming a
# guest who may, not a blanket allow.
check(screen.is_shell("com.moonlight_stream.moonlight moonlight"),
      "Moonlight does, for now, and its flatpak's class matches as well")
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
check("_tell_about_the_hold()" in held,
      "the guests are told, rather than left guessing")

told = source.split("def _tell_about_the_hold")[1].split("\n    def ")[0]
# Where the fields are actually worked out. They moved out of the broadcast
# and into hold_state() when a guest who rejoins started being told the same
# thing on the way in -- one place deciding it, two places sending it.
state = source.split("def hold_state")[1].split("\n    def ")[0]
check('"t": "hold"' in told and "notify_one" in told,
      "one page at a time, because 'may you drive' is a different answer for "
      "each of them")

# And that the thing it calls exists. This suite used to check only that the
# name appeared at the call site, which it did -- while the method itself had
# never been written, because the edit that was meant to add it aborted before
# saving. What that produced was an AttributeError inside the sweeper, which
# is also the dead-man switch and the launch deadline, so the first symptom
# was pads not being released.
import ast  # noqa: E402
klass = next(node for node in ast.parse(source).body
             if isinstance(node, ast.ClassDef) and node.name == "LiveSession")
methods = {node.name for node in klass.body
           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
for name in ("notify_one", "notify", "say", "name_a_driver", "_hold_input"):
    check(name in methods, "LiveSession really has %s(), not just calls to it"
          % name)
check("self.hold_state(guest)" in told,
      "and the broadcast sends what that works out rather than a second copy "
      "of it, which is how two answers to one question start disagreeing")
sweeper = source.split("async def _sweep_forever")[1].split("\n    async def ")[0]
check("except Exception" in sweeper,
      "and a fault in the newest thing on the sweeper cannot end the sweeper: "
      "the dead-man switch rides on it")
check('"driving": guest.slot == self.driver' in state,
      "and the answer is worked out here rather than by a page comparing slot "
      "numbers it cannot be sure of")

print("one guest may be named to drive what is in front")
named = source.split("def name_a_driver")[1].split("\n    def ")[0]
check("self.driver = slot" in named and "raise ValueError" in named,
      "a seat with nobody in it cannot be named")
check("self.driver_shell = self.hold_reason" in named,
      "and what it is granted against is remembered with it")
# The gate itself moved out of feed() when Steam games arrived: "may this
# guest drive" stopped being one question with one answer and became two, and
# two places working it out separately is how a page ends up saying "controls
# paused" over a controller that works. So there is one method, and feed()
# calls it.
holding = source.split("def holding(self, guest)")[1].split("\n    def ")[0]
check("self.driver != guest.slot" in holding,
      "which is the whole of the gate: everybody is held except the one named")
feed2 = source.split("def feed(self, data)")[1].split("\n    def ")[0]
check("self.session.holding(self)" in feed2,
      "and feed asks that one method rather than working it out again")
gone = source.split("def forget_driver_if")[1].split("\n    def ")[0]
check("self.driver = None" in gone,
      "leaving takes it away, and nobody inherits it with the seat")
lapse = source.split("def _hold_input")[1].split("\n    def ")[0]
check("why != self.driver_shell" in lapse,
      "and so does something else coming to the front -- closing one program "
      "and opening another is not what anybody would call revoking a "
      "permission, which is why it has to be done for them")

feed = source.split("def feed(self, data)")[1].split("\n    def ")[0]
check(feed.index("self.last_input = time.monotonic()")
      < feed.index("self.session.holding(self)"),
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
