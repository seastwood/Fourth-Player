"""Closing the game that is playing, which used to be a request and no more.

The report: starting a different game while one was running, or restarting the
same one, left the first game up and the second one refused. The log said it
plainly --

    stopping what is playing to start Mario Golf - Toadstool Tour (USA)
    what was playing is still running; not starting Mario Golf over it

-- fifteen seconds apart, which is the shape of the fault rather than a
detail. `systemctl stop` blocks until the unit is really gone and a transient
unit's default TimeoutStopSec is ninety seconds, so the call sat there until
it was abandoned after ten; the poll gave up four seconds later; and nothing
had ever escalated past a polite TERM. Systemd was still waiting patiently for
a game that had been asked to quit, and the guest was told it "would not
close".

Everything here is stubbed: no signal is sent to anything, and the clock is
made to move by hand so a suite that is about waiting does not have to wait.
What is held still is the order -- ask, wait properly, then insist -- and that
every process the code calls a game is addressed by the code that stops games.
"""
import importlib.machinery
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ldr = importlib.machinery.SourceFileLoader(
    "launcher", os.path.join(os.path.dirname(HERE), "fourthplayer",
                             "launcher.py"))
launcher = importlib.util.module_from_spec(
    importlib.util.spec_from_loader("launcher", ldr))
ldr.exec_module(launcher)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


class Fake:
    """A machine where a game stops after however long the test says.

    The clock only moves when the code sleeps, so eight seconds of grace costs
    nothing to sit through.
    """

    def __init__(self, dies_after=0.0, ignores_kill=False):
        self.now = 1000.0
        self.dies_after = dies_after
        self.ignores_kill = ignores_kill
        self.ran = []
        self.termed_at = None
        self.killed_at = None

    # -- the seams launcher.py reaches the machine through --
    def run(self, argv, **kw):
        self.ran.append(list(argv))
        if argv[0] == "pkill" and "-TERM" in argv and self.termed_at is None:
            self.termed_at = self.now
        if argv[0] == "pkill" and "-KILL" in argv:
            if self.killed_at is None:
                self.killed_at = self.now
            if not self.ignores_kill:
                self.dies_after = 0        # a killed process is gone at once
        return None

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def gone(self):
        """What running() answers: the game is up until it has had its time."""
        if self.termed_at is None:
            return True
        return self.now - self.termed_at < self.dies_after


def wire(fake):
    launcher.subprocess = type("S", (), {
        "run": staticmethod(lambda argv, **kw: fake.run(argv, **kw)),
        "SubprocessError": Exception, "TimeoutExpired": Exception})
    launcher.time = type("T", (), {
        "time": staticmethod(fake.time),
        "sleep": staticmethod(fake.sleep),
        "monotonic_ns": staticmethod(lambda: 1)})
    launcher.shutil = type("H", (), {
        "which": staticmethod(lambda name: "/bin/" + name)})
    launcher.running = fake.gone
    return fake


print("a game that closes when asked")
fake = wire(Fake(dies_after=1.5))
check(launcher.stop_running() is True, "is stopped, and reported stopped")
check(fake.killed_at is None, "and is never killed -- TERM is what saves a save")
kinds = [a[1] if a[0] == "pkill" else a[0] for a in fake.ran]
check(kinds.count("-TERM") == len(launcher.GAME_PROCESSES),
      "every process the code calls a game is asked: %d of them"
      % len(launcher.GAME_PROCESSES))

print("systemd is asked without being waited for")
call = next((a for a in fake.ran if a[0].endswith("systemctl")), [])
check("stop" in call and "--no-block" in call,
      "--no-block, because `stop` blocks for the unit's whole stop timeout "
      "and the answer is whether the game is gone, not whether systemd is done")
check(call[-1].startswith(launcher.UNIT_PREFIX),
      "and it names the game units: %s" % (call[-1] if call else "-"))

print("a game that ignores the request")
fake = wire(Fake(dies_after=60))
check(launcher.stop_running() is True, "is killed, and then it is gone")
check(fake.killed_at is not None, "the kill happened")
waited = fake.killed_at - fake.termed_at
check(launcher.STOP_GRACE <= waited < launcher.STOP_GRACE + 1,
      "after the grace and not before: waited %.2fs of %.0fs"
      % (waited, launcher.STOP_GRACE))
check(any(a[0].endswith("systemctl") and "kill" in a for a in fake.ran),
      "and the unit is killed too, not only the process")

print("a game that survives even that")
fake = wire(Fake(dies_after=60, ignores_kill=True))
check(launcher.stop_running() is False,
      "is reported as still running rather than assumed gone")
check(fake.now - fake.termed_at <= launcher.STOP_LIMIT + 1,
      "and the whole business is over inside the limit: %.1fs of %.0fs"
      % (fake.now - fake.termed_at, launcher.STOP_LIMIT))

print("Steam is closed before a guest's game starts")
fake = wire(Fake())
launcher._any_running = lambda names: False
check(launcher.stop_steam() is True and fake.ran == [],
      "a machine with no Steam running is not asked to close it")

# Steam that closes when asked: the client's own -shutdown, and nothing else.
state = {"up": True}
fake = wire(Fake())
launcher._any_running = lambda names: state["up"]


def shutdown_works(argv, **kw):
    fake.run(argv, **kw)
    if "-shutdown" in argv:
        state["up"] = False


launcher.subprocess = type("S", (), {
    "run": staticmethod(shutdown_works), "SubprocessError": Exception,
    "TimeoutExpired": Exception})
check(launcher.stop_steam() is True, "it closes")
check(any("-shutdown" in a for a in fake.ran),
      "through `steam -shutdown`, which syncs the cloud saves on the way out")
check(not any(a[0] == "pkill" for a in fake.ran),
      "and is never signalled when it went quietly -- a signal skips the sync")

# Steam that ignores it.
state = {"up": True}
fake = wire(Fake())
launcher._any_running = lambda names: state["up"]


def dies_on_kill(argv, **kw):
    fake.run(argv, **kw)
    if argv[0] == "pkill" and "-KILL" in argv:
        state["up"] = False


launcher.subprocess = type("S", (), {
    "run": staticmethod(dies_on_kill), "SubprocessError": Exception,
    "TimeoutExpired": Exception})
check(launcher.stop_steam() is True, "one that ignores the ask is killed")
order = [a[1] for a in fake.ran if a[0] == "pkill"]
check(order[0] == "-TERM" and "-KILL" in order,
      "asked before it is killed, in that order")
check(launcher.STEAM_GRACE > launcher.STOP_GRACE,
      "and given longer than a game gets: its shutdown writes a library and "
      "syncs saves this program never started")

print("Moonlight is closed too, and differently")
state = {"up": True}
fake = wire(Fake())
launcher._any_running = lambda names: state["up"] and "moonlight" in names[0]


def dies_on_term(argv, **kw):
    fake.run(argv, **kw)
    if argv[0] == "pkill" and "-TERM" in argv:
        state["up"] = False


launcher.subprocess = type("S", (), {
    "run": staticmethod(dies_on_term), "SubprocessError": Exception,
    "TimeoutExpired": Exception})
check(launcher.stop_moonlight() is True, "it closes")
check(not any("-shutdown" in a for a in fake.ran),
      "with no graceful command, because it has none and needs none: the game "
      "is on another machine, which is already prepared for a stream to stop")
check(launcher.MOONLIGHT_GRACE < launcher.STEAM_GRACE,
      "and a shorter wait than Steam gets, which has saves to sync")

print("both are cleared before a guest's game")
state = {"steam": True, "moonlight": True}
fake = wire(Fake())
launcher._any_running = lambda names: state["steam" if "steam" in names[0] else "moonlight"]
launcher.stop_steam = lambda: (state.update(steam=False), True)[1]
launcher.stop_moonlight = lambda: (state.update(moonlight=False), True)[1]
check(launcher.clear_the_screen() == [],
      "nothing is in the way once both have gone")
state = {"steam": True, "moonlight": True}
launcher.stop_steam = lambda: False         # it would not go
launcher.stop_moonlight = lambda: (state.update(moonlight=False), True)[1]
check(launcher.clear_the_screen() == ["Steam"],
      "what would not close is named, rather than the launch being refused")
check(state["moonlight"] is False,
      "and the other one is still closed: one stubborn program is not a "
      "reason to leave the rest of the screen occupied")

print("the unit does not get to wait ninety seconds")
source = open(os.path.join(os.path.dirname(HERE), "fourthplayer",
                           "launcher.py")).read()
check("TimeoutStopSec=%ds" in source,
      "the transient unit carries a stop timeout of its own")
check('"-p", "KillMode=mixed"' in source,
      "and kills the whole cgroup rather than the main process alone")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("all good")
