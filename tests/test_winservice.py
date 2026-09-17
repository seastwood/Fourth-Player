"""Keeping a host alive as SYSTEM in whichever session has the screen.

The lock screen lives on the Winlogon desktop, and only LocalSystem may open
that. A service is how a process gets to be SYSTEM and stay SYSTEM across a
reboot with nobody signed in -- but a service runs in session 0, which has had
no desktop since Vista, so being SYSTEM is not enough on its own. The service
therefore keeps a *host* running as SYSTEM inside whichever session has the
console.

The supervisor is the part with decisions in it, so it is separated from the
service plumbing and tested here with the three things it talks to -- starting
a process, asking whether one is alive, and asking which session has the
console -- handed in.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from fourthplayer.winservice import Supervisor

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def watcher(sessions, alive=True, pids=None):
    """A supervisor whose world this test decides."""
    made = []
    supply = list(pids or [])

    def start():
        # Explicit pids rather than one computed from the length of a list I
        # have just appended to: the first version of this returned 1001 for
        # what the test called the first host, and the failure was in the
        # harness rather than in anything being tested.
        made.append(True)
        return supply.pop(0) if supply else 900 + len(made)

    state = {"session": sessions, "alive": alive}

    def session_now():
        return state["session"]() if callable(state["session"]) else state["session"]

    def is_alive(_pid):
        return state["alive"]() if callable(state["alive"]) else state["alive"]

    sup = Supervisor(start=start, alive=is_alive, session=session_now)
    sup.made = made
    sup.state = state
    return sup


print("nothing is started before anybody has signed in")
# WTSGetActiveConsoleSessionId answers 0xFFFFFFFF while the machine is still
# starting, which console_session turns into None. That is a "not yet", not a
# failure, and starting into it would be starting into nowhere.
sup = watcher(None)
check(sup.look() is False, "no console session, nothing started")
check(sup.made == [], "really nothing")
check(sup.pid is None, "and nothing remembered")

print("\nonce there is a session, one host is started in it")
sup = watcher(1, pids=[4242])
check(sup.look() is True, "started")
check(sup.pid == 4242 and sup.session == 1,
      "and remembered which process, in which session: %s in %s"
      % (sup.pid, sup.session))

print("\nand not started again while it is still running there")
for _ in range(5):
    sup.look()
check(len(sup.made) == 1, "five more passes start nothing: %d" % len(sup.made))

print("\na host that dies is replaced")
sup.state["alive"] = False
check(sup.look() is True, "a dead host is noticed")
check(len(sup.made) == 2, "and replaced once")
sup.state["alive"] = True
sup.look()
check(len(sup.made) == 2, "and not again once it is up")

print("\nsigning out and in again moves the host to the new session")
# The console session number changes when somebody signs out and another
# person signs in. A process cannot follow -- it belongs to the session it was
# made in -- so a new one is started in the new session.
sup = watcher(1)
sup.look()
check(sup.session == 1, "started in session 1")
sup.state["session"] = 2
check(sup.look() is True, "the move is noticed")
check(sup.session == 2, "and the host is started in session 2")
check(len(sup.made) == 2, "exactly once")

print("\na start that fails is retried rather than remembered as success")
# CreateProcessAsUser can refuse for a moment during a session change, and a
# supervisor that recorded the failure as a running host would never try again.
def refuse():
    return None

sup = Supervisor(start=refuse, alive=lambda _p: False, session=lambda: 1)
check(sup.look() is False, "a refused start is not a success")
check(sup.pid is None, "nothing is remembered")
check(sup.look() is False, "and it is tried again next pass")

print("\nthe loop survives something throwing")
class Angry:
    def __init__(self):
        self.tries = 0
        self.stop_after = None

    def __call__(self):
        self.tries += 1
        if self.stop_after is not None:
            self.stop_after.set()
        raise RuntimeError("no")

angry = Angry()
sup = Supervisor(start=lambda: 1, alive=lambda _p: True, session=angry)
# Stopped from inside, after the first throw. Setting `stopping` beforehand
# makes run() return without a single pass -- correct, and it measures
# nothing, which is what the first version of this did.
angry.stop_after = sup.stopping
sup.EVERY = 0.01
sup.run()
check(angry.tries >= 1, "the pass happened: %d" % angry.tries)
check(True, "and run() returned rather than taking the service down with it")

print("\nthe service says what it is and asks for the right account")
import inspect
from fourthplayer import winservice
source = inspect.getsource(winservice)
check('"obj=", "LocalSystem"' in source,
      "it installs as LocalSystem, which is the entire reason it exists -- "
      "only SYSTEM may open the Winlogon desktop")
check('"start=", "auto"' in source,
      "and starts itself, so a machine nobody has touched comes back")
check("SERVICE_ACCEPT_SESSIONCHANGE" in source,
      "and asks to hear about session changes, so signing in is noticed at "
      "once rather than on the next sweep")
check("restart/5000" in source,
      "and is set to restart itself, because on a headless host nobody "
      "notices that it stopped")

print("\nand it is honest about being run the wrong way")
check("This entry point is for the service" in source,
      "running it by hand says so rather than hanging")

print()
print("each Windows call is looked up in the library that actually has it")
# WTSGetActiveConsoleSessionId is in kernel32, not wtsapi32, despite the
# prefix -- everything else named WTS* is in wtsapi32 and that one is not.
# The first version looked it up in wtsapi32 and raised AttributeError every
# single time, so the service would never have started a host at all. That is
# invisible while the code is only read and obvious the moment it is run.
from fourthplayer import winsession
import inspect as _inspect
src = _inspect.getsource(winsession)
console = src[src.index("def console_session"):src.index("def _system_token_for")]
check("_kernel32.WTSGetActiveConsoleSessionId" in console,
      "the console session id comes from kernel32")
check("_wtsapi32.WTSGetActiveConsoleSessionId" not in src,
      "and never from wtsapi32, which does not export it")
check("argtypes" in console,
      "with its signature stated, so a pseudo-handle or a DWORD is not "
      "guessed at")

print()
print("and asking is safe on a machine that cannot answer")
# None rather than an exception: no console session is an ordinary state
# during a fast user switch and while the machine is starting.
check(winsession.console_session() is None or
      isinstance(winsession.console_session(), int),
      "console_session() answers rather than raising")
check(isinstance(winsession.explain(), str) and winsession.explain(),
      "and explain() says something either way: %r"
      % winsession.explain()[:60])

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
