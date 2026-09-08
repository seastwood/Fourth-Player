"""Closing Steam must not put a dialogue on the television.

Ending a game raised "Steam now requires user namespaces to be enabled" every
time, and there were two reasons, of which the sandbox is the real one.

This service runs with NoNewPrivileges and RestrictNamespaces. A child cannot
drop either, so a Steam binary started directly by the server fails its
namespace check however the kernel is configured: it shows that dialogue and
exits without delivering the shutdown it was asked for. So the shutdown has to
be handed to the service manager, exactly as a game launch is.

The lesser reason: `steam -shutdown` with no client running starts one, which
is a strange way to stop a program even when it is harmless.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


try:
    from fourthplayer import launcher
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)

asked = []
real_running, real_close = launcher.steam_running, launcher._close
launcher._close = lambda *a, **k: asked.append(a) or True

print("with no client running")
launcher.steam_running = lambda: False
check(launcher.stop_steam() is True, "it says the screen is clear")
check(asked == [], "and asks Steam for nothing: %r" % (asked,))

print("\nwith a client running")
launcher.steam_running = lambda: True
launcher.stop_steam()
check(len(asked) == 1, "it closes it")
argv = [part for part in asked[0] if isinstance(part, list)][0]
check("-shutdown" in argv,
      "with -shutdown, which is the polite way: %r" % (argv,))
check(argv.index("-shutdown") == len(argv) - 1
      and argv[argv.index("-shutdown") - 1].endswith("steam"),
      "and -shutdown is an argument to steam, not to the wrapper: %r" % (argv,))

print("\nand it is the service manager that runs it")
check("systemd-run" in argv[0],
      "handed off rather than run under this service's confinement, which no "
      "child can drop and Steam cannot start under: %r" % (argv,))
check("--user" in argv, "as the user, where the running client is")
check(argv.count("--") == 1 and argv.index("--") < argv.index("-shutdown"),
      "with the command separated from systemd-run's own options")
unit = argv[argv.index("--unit") + 1]
check(unit.startswith(launcher.HELPER_PREFIX),
      "in a helper unit, %r" % unit)
check(not unit.startswith(launcher.UNIT_PREFIX),
      "and not one the game stop's glob would sweep up: stopping a game is "
      "precisely when this runs")
check(any(part == "DISPLAY=:0" for part in argv),
      "with a display to talk to")

print("\nwithout a service manager to hand it to")
real_which = launcher.shutil.which
launcher.shutil.which = lambda name: None if name == "systemd-run" else "/usr/games/steam"
plain = launcher.outside_sandbox(["/usr/games/steam", "-shutdown"])
check(plain == ["/usr/games/steam", "-shutdown"],
      "the command is passed through rather than mangled: %r" % (plain,))
launcher.shutil.which = real_which

launcher.steam_running, launcher._close = real_running, real_close

print("\nand the game is asked first, Steam second")
source = open(os.path.join(ROOT, "fourthplayer", "launcher.py"),
              encoding="utf-8").read()
stop = source.split("def stop_steam_game")[1].split("\ndef ")[0]
# The last stop_steam(), not the first: there is an early one for "no game,
# but Steam is still sitting on the television", which is a different case.
check(stop.index("pkill") < stop.rindex("stop_steam("),
      "TERM to the game before Steam is closed under it")
check(stop.index("pkill") > stop.index("if appid is None"),
      "and the no-game case is answered before any of that")

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
