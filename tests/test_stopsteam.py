"""Closing Steam must not start Steam.

`steam -shutdown` on a machine with no client running starts one: it comes up,
runs its start-up checks, puts its dialogue about user namespaces on the
television, and only then exits. Every "End game" was raising that dialogue,
which read as a fault in the game and was a fault in the way it was closed.
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
check(any("-shutdown" in str(part) for part in asked[0]),
      "with -shutdown, which is the polite way: %r" % (asked[0],))

launcher.steam_running, launcher._close = real_running, real_close

print("\nand the game is asked first, Steam second")
source = open(os.path.join(ROOT, "fourthplayer", "launcher.py"),
              encoding="utf-8").read()
stop = source.split("def stop_steam_game")[1].split("\ndef ")[0]
check(stop.index("pkill") < stop.index("stop_steam("),
      "TERM to the game before Steam is closed under it")

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
