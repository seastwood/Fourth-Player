"""The capture stops working when nobody is watching.

The pipeline follows the *session*, not the guests: session.py starts it when a
session opens and stops it when the session closes. A console whose session
never expires therefore captured and encoded its screen around the clock for
nobody. Measured on two machines with zero guests connected, 47% and 60% of a
core continuously, and fifteen hours of CPU between restarts. On a games
console that is taken straight out of the emulator.

So the last guest to leave starts a clock, and when it runs out the pipeline
goes to PAUSED. Paused rather than stopped, because stop() is one-way -- it
takes the pipeline to NULL and the Stage is rebuilt rather than restarted --
and it is the path that has to destroy webrtcbin, which is delicate enough to
carry its own timeout. Waking up needed no new code at all: add_peer has
called ensure_playing() before attaching anybody since long before this.

What is actually worth testing is the decision, not GStreamer's ability to
pause: when the clock is started, when it is called off, and the two races
either side of it -- a guest who arrives while the timer is pending, and one
who is part-way through attaching and therefore not in self.peers yet.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    from fourthplayer import video
except Exception as exc:                                  # noqa: BLE001
    # No GStreamer here. The laptop has no GstWebRTC; ultra and the consoles
    # do, and that is where this runs.
    print("SKIPPED: fourthplayer.video will not import (%s)" % exc)
    sys.exit(0)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


class FakePipeline:
    """Only the two calls idle_if_empty makes."""

    def __init__(self, state):
        self.state = state
        self.asked = []

    def get_state(self, _timeout):
        return (None, self.state, None)

    def set_state(self, state):
        self.asked.append(state)
        self.state = state
        return None


class FakeWorker:
    """The one thread allowed to touch the pipeline. Run inline, in order."""

    def __init__(self):
        self.ran = []

    def submit(self, fn):
        self.ran.append(fn)
        return fn()


class FakePeer:
    def __init__(self, media=True):
        self.media = media


def a_stage(peers=None, state=None, attaching=0):
    """A Stage without building a pipeline: __init__ needs a real display."""
    stage = video.Stage.__new__(video.Stage)
    stage.peers = dict(peers or {})
    # _idle_timer and _attaching come from the class, which is what lets a
    # Stage built this way answer take_peer at all.
    if attaching:
        stage._attaching = attaching
    stage.pipeline = FakePipeline(video.Gst.State.PLAYING if state is None
                                  else state)
    stage.worker = FakeWorker()
    return stage


PLAYING = video.Gst.State.PLAYING
PAUSED = video.Gst.State.PAUSED

print("-- who counts as somebody watching --")
stage = a_stage({"slot0": FakePeer(media=True),
                 "slot1": FakePeer(media=False)})
check(stage.watchers() == 1,
      "a peer with no picture is not a reason to keep encoding, got %d"
      % stage.watchers())
check(a_stage({}).watchers() == 0, "and an empty stage watches nothing")

print("\n-- the last guest to leave starts the clock --")
stage = a_stage({"slot0": FakePeer(), "slot1": FakePeer()})
stage.take_peer("slot0")
check(stage._idle_timer is None,
      "one of two leaving starts nothing -- somebody is still watching")
stage.take_peer("slot1")
check(stage._idle_timer is not None, "the last one does")
stage._cancel_idle()

print("\n-- a controller-only guest leaving is not the last guest --")
stage = a_stage({"slot0": FakePeer(media=True), "slot1": FakePeer(media=False)})
stage.take_peer("slot1")
check(stage._idle_timer is None,
      "the one still being sent a picture keeps the capture running")
stage._cancel_idle()

print("\n-- taking nobody starts nothing --")
stage = a_stage({"slot0": FakePeer()})
stage.take_peer("nosuch")
check(stage._idle_timer is None, "a name that was not there changes nothing")

print("\n-- and when the clock runs out --")
stage = a_stage({})
check(stage.idle_if_empty() is True, "an empty stage pauses")
check(stage.pipeline.asked == [PAUSED],
      "by asking for PAUSED, got %s" % stage.pipeline.asked)
check(stage.pipeline.state == PAUSED, "and it is paused")

print("\n-- but not over somebody who turned up in the meantime --")
# Thirty seconds is a long time in a session, so the count is re-read when the
# timer fires rather than trusted from when it was armed.
stage = a_stage({"slot0": FakePeer()})
check(stage.idle_if_empty() is False, "a guest who arrived is not paused over")
check(stage.pipeline.asked == [], "nothing was asked of the pipeline")

print("\n-- nor over one part-way through attaching --")
# The window that makes this necessary: add_peer does not put the peer in
# self.peers until it has attached, so between ensure_playing and that line a
# guest is real and invisible.
stage = a_stage({}, attaching=1)
check(stage.idle_if_empty() is False,
      "a guest in the middle of attaching holds the capture up")
check(stage.pipeline.asked == [], "and nothing was paused under them")

print("\n-- a pipeline that is not playing is left alone --")
stage = a_stage({}, state=PAUSED)
check(stage.idle_if_empty() is False, "already paused: nothing to do")
check(stage.pipeline.asked == [], "and it is not asked again")
stage = a_stage({})
stage.pipeline = None
check(stage.idle_if_empty() is False,
      "a Stage whose pipeline has been taken away does not raise")

print("\n-- arriving calls the clock off --")
stage = a_stage({})
stage._arm_idle()
timer = stage._idle_timer
check(timer is not None and not timer.finished.is_set(),
      "a timer is running")
stage._cancel_idle()
check(stage._idle_timer is None, "cancelling clears it")
# finished, not is_alive: cancel() sets the event immediately, and the thread
# takes a moment longer to notice and exit.
check(timer.finished.is_set(), "and stops it")

print("\n-- arming twice leaves one timer, not two --")
stage = a_stage({})
stage._arm_idle()
first = stage._idle_timer
stage._arm_idle()
check(stage._idle_timer is not first, "the second call replaces the first")
check(first.finished.is_set(),
      "and the first is stopped rather than left running")
stage._cancel_idle()

print("\n-- the timer really does pause, through the worker --")
# Every state change goes through the worker: two threads in the GPU driver at
# once is something this process has segfaulted over.
was, video.IDLE_AFTER = video.IDLE_AFTER, 0.05
try:
    stage = a_stage({})
    stage._arm_idle()
    for _ in range(40):
        if stage.pipeline.state == PAUSED:
            break
        time.sleep(0.05)
    check(stage.pipeline.state == PAUSED, "it paused on its own")
    check(len(stage.worker.ran) == 1,
          "having gone through the worker, got %d job(s)" % len(stage.worker.ran))
finally:
    video.IDLE_AFTER = was

print("\n-- and a guest arriving first means it never fires --")
was, video.IDLE_AFTER = video.IDLE_AFTER, 0.05
try:
    stage = a_stage({})
    stage._arm_idle()
    stage._cancel_idle()                     # what add_peer does first
    stage.peers["slot0"] = FakePeer()
    time.sleep(0.2)
    check(stage.pipeline.state == PLAYING, "the capture kept running")
    check(stage.worker.ran == [], "and the worker was never asked to pause")
finally:
    video.IDLE_AFTER = was

print("\n-- waking up was already written --")
import inspect                                             # noqa: E402
attach = inspect.getsource(video.Stage.add_peer)
check("_cancel_idle" in attach,
      "add_peer calls the clock off before it does anything else")
check("_attaching" in attach and "finally" in attach,
      "and counts itself in, in a finally -- an attach that raises must not "
      "leave the capture pinned awake for the rest of the session")
body = inspect.getsource(video.Stage._attach_peer)
check("ensure_playing" in body,
      "the wake-up itself is ensure_playing, which add_peer has always called")
check("_cancel_idle" in inspect.getsource(video.Stage.stop),
      "and stop() calls it off too: a pipeline on its way to NULL has nothing "
      "to pause")

print()
if fails:
    print("FAILURES: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_idlecapture: all ok")
