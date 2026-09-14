"""The tray bringing a crashed host back, which once it did not do.

A host died on a Windows machine and stayed dead. The icon was still there,
still saying "not running", and the only way back was someone noticing and
starting it by hand -- which is worse than having no watchdog, because the
icon looks like one.

Two things are checked here, because the failure needed both to go wrong
before anyone could see it. First that a dead child really is restarted, and
that the backoff makes the second attempt wait rather than hammering. Second
-- and this is the part that hid it -- that a poll which raises does not end
the thread doing the polling. The revive call used to sit outside the try in
the backend's loop, so one exception anywhere in it stopped the watching for
good, silently, with the icon frozen on its last words.
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from fourthplayer import tray as tray_module


class DeadChild:
    """A Popen that has exited, which is what a crashed host leaves behind."""

    def __init__(self, code=-1073741819):     # 0xC0000005, an access violation
        self.code = code

    def poll(self):
        return self.code


class LiveChild:
    def poll(self):
        return None


def a_tray(child, started=True):
    """A tray whose host this test drives, with nothing really launched."""
    it = tray_module.Tray.__new__(tray_module.Tray)
    it.open = False
    it.reachable = False
    it.busy = ""
    it.stopping = False
    # A real Host, not a stand-in. The first version of this built one out of
    # SimpleNamespace and handed it every attribute _revive reached for --
    # including a BACKOFF that the real Host has never had. _revive was
    # reading self.host.BACKOFF, so on the actual machine it raised
    # AttributeError every time the host died and restarted nothing, for as
    # long as the watchdog had existed, while this test sat green. A fake
    # built from the code's assumptions can only ever confirm them.
    it.host = tray_module.Host(launch=True)
    it.host.child = child
    it.host.unit = None
    it.host.tries = []

    def start():
        it.host.tries.append(True)
        return (started, "" if started else "no")

    it.host.start = start
    return it


def test_a_dead_host_is_started_again():
    it = a_tray(DeadChild())
    it._revive()
    assert it.host.tries, "the host crashed and nothing started it again"
    assert it.host.failures == 0, ("a start that worked should clear the "
                                   "count, or the next crash waits longer "
                                   "than it should")


def test_a_live_host_is_left_alone():
    it = a_tray(LiveChild())
    it._revive()
    assert not it.host.tries, ("a host that is merely slow to answer was "
                               "started a second time, which would leave two "
                               "fighting over the capture device")


def test_a_reachable_host_is_left_alone():
    it = a_tray(DeadChild())
    it.reachable = True
    it._revive()
    assert not it.host.tries


def test_a_host_nobody_here_started_is_not_ours_to_restart():
    it = a_tray(None)
    it._revive()
    assert not it.host.tries


def test_a_host_asked_to_stop_stays_stopped():
    it = a_tray(DeadChild())
    it.host.wanted = False
    it._revive()
    assert not it.host.tries, "the icon restarted a host the user turned off"


def test_it_waits_between_attempts():
    it = a_tray(DeadChild(), started=False)
    it._revive()
    assert len(it.host.tries) == 1
    it._revive()
    assert len(it.host.tries) == 1, ("a host that will not start was retried "
                                     "immediately; that fills the disk with "
                                     "the same traceback")
    # Far enough past the first wait that the second attempt is due.
    it._last_try -= tray_module.Tray.BACKOFF[1] + 1
    it._revive()
    assert len(it.host.tries) == 2, "the backoff never expired"


def test_the_watch_survives_a_poll_that_raises():
    """The one that matters: a thrown poll must not end the watching.

    Written against the loop's shape rather than the loop itself, because the
    loop lives inside whichever backend drew the icon and neither can be
    started without a display. What is asserted is that the body is attempted
    again after one that raised.
    """
    import re
    source = open(os.path.join(ROOT, "fourthplayer", "tray.py")).read()
    loops = re.findall(r"def watch\(\):\n(.*?)(?=\n    [a-z@]|\nclass |\Z)",
                       source, re.S)
    assert loops, "no watch loop found; this test has lost its subject"
    for body in loops:
        # Comments out first. One of them names tray.poll() while explaining
        # why the call belongs inside the try, and reading that as the call
        # made this test fail on the very code that fixed the bug.
        body = re.sub(r"#[^\n]*", "", body)
        before, _, after = body.partition("tray.poll()")
        assert "try:" in before.split("while True:")[-1], (
            "tray.poll() is outside the try in a watch loop -- one exception "
            "there ends the thread and nothing watches the host again")
        assert "except Exception:" in after, (
            "nothing catches what the loop body raises")
        assert "return" not in after.split("except Exception:")[1][:200], (
            "the handler leaves the loop, which is the same silence by "
            "another route")


if __name__ == "__main__":
    checks = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for check in checks:
        try:
            check()
            print("ok   ", check.__name__)
        except AssertionError as why:
            bad += 1
            print("FAIL ", check.__name__, "--", why)
    print(f"{len(checks) - bad}/{len(checks)} passed")
    sys.exit(1 if bad else 0)
