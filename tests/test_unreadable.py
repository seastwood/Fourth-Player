"""What to do when we cannot tell what is on the screen.

is_shell("") says "a shell", and for an empty desktop that is right: a guest
whose game has crashed is looking at nothing, and their pad should stop at the
television.

A fullscreen game can be unreadable too. An override-redirect window has no
entry for xdotool to find, so foreground() comes back empty while the game is
running perfectly well -- and every guest is then held, permanently, mid-game,
with no reason given anywhere. Measured on the console: empty for minutes on
end with Broforce up, which is exactly what "no one can control Broforce" was.

So when the answer cannot be read, the question becomes whether a game is
running. If one is, the guest is far likelier looking at it than at a menu, and
the hold exists for menus.
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
    from fourthplayer import launcher, screen
    from fourthplayer.session import LiveSession
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)


class Cfg:
    guest_input_needs_a_game = True
    shell_windows = ()


def watcher(front, running):
    session = LiveSession.__new__(LiveSession)
    session.cfg = Cfg()
    screen.foreground = lambda: front
    launcher.running = lambda: running
    return session._watch_the_screen()


real_front, real_running = screen.foreground, launcher.running

print("a window we can read")
check(watcher("broforce", True) == (False, ""), "a game is not a shell")
check(watcher("kodi.bin kodi", True)[0], "Kodi's menu holds, game or no game")
check(watcher("kodi.bin kodi", False)[0], "and holds with nothing running")

print("\\na window we cannot read")
check(watcher("", True) == (False, ""),
      "with a game running, an unreadable window does not hold: %r"
      % (watcher("", True),))
check(watcher("", False)[0],
      "with nothing running, it still does -- that is the crashed-game case")
check(watcher("", False)[1] == "the desktop",
      "and says so: %r" % (watcher("", False)[1],))

print("\\nand a launcher that will not answer")


def angry():
    raise OSError("no process table today")


screen.foreground = lambda: ""
launcher.running = angry
session = LiveSession.__new__(LiveSession)
session.cfg = Cfg()
check(session._watch_the_screen()[0],
      "if even that cannot be asked, it holds, which is the safe way to be wrong")

screen.foreground, launcher.running = real_front, real_running

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
