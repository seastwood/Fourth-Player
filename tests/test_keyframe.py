"""Getting the picture back after a blip, without waiting for the clock.

A browser that has lost a frame asks for a fresh keyframe, and webrtcbin turns
that into an upstream force-key-unit event. It arrived at the guest's appsrc
and stopped there -- the encoder is in the capture pipeline, not the guest's --
so nothing acted on it and the guest waited for the next periodic keyframe.
At thirty frames a second with a two-second interval that is up to two seconds
of black after a momentary loss, which is what was reported.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst

from fourthplayer import video

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


Gst.init(None)


class FakeWorker:
    def __init__(self):
        self.jobs = []

    def submit(self, fn, *a):
        self.jobs.append(fn)


class FakeStage:
    """Only the parts request_keyframe touches."""

    def __init__(self):
        self.worker = FakeWorker()
        self.forced = 0
        # Borrowed from the real Stage rather than written out here. Setting
        # this state by hand meant the fake carried one version of what
        # request_keyframe needs while the method moved on, and the test broke
        # the moment a counter was added -- for no reason to do with what it
        # was testing.
        video.Stage._reset_keyframe_limit(self)

    def force_keyframe(self):
        self.forced += 1

    request_keyframe = video.Stage.request_keyframe
    _reset_keyframe_limit = video.Stage._reset_keyframe_limit


print("a request reaches the encoder")
stage = FakeStage()
stage.request_keyframe("slot0")
check(len(stage.worker.jobs) == 1, "one request, one keyframe asked for")
stage.worker.jobs[0]()
check(stage.forced == 1, "and it is the encoder that is asked")

print("but a room full of guests cannot turn the stream into keyframes")
stage = FakeStage()
for i in range(20):
    stage.request_keyframe("slot%d" % i)
check(len(stage.worker.jobs) == video.KEYFRAME_BURST,
      "twenty at once is the burst and no more: %d" % len(stage.worker.jobs))

print("and asking again later works")
spent = len(stage.worker.jobs)
stage._keyframe_filled -= (video.KEYFRAME_MIN_GAP + 0.01)
stage.request_keyframe("slot0")
check(len(stage.worker.jobs) == spent + 1, "once the bucket has refilled")

# This used to assert that KEYFRAME_MIN_GAP was small, as a stand-in for "a
# guest recovers in a blink". It is the behaviour that matters, and a single
# number could not give it: short enough to answer a blip at once is also
# short enough for several guests to spend the whole bitrate on keyframes,
# which is what two guests on a Windows host did. The bucket separates the
# two, so the thing worth asserting is the behaviour itself.
print("one guest's blip is answered at once, however long the sustained gap is")
stage = FakeStage()
stage.request_keyframe("slot0")
check(len(stage.worker.jobs) == 1, "the first request is not made to wait")
stage.request_keyframe("slot0")
check(len(stage.worker.jobs) == 2,
      "and a second, moments later, is still served from the burst -- a "
      "dropped frame twice in a row is one blip, not a storm")

print("but sustained demand settles to the refill rate, not the burst")
stage = FakeStage()
for _ in range(50):
    stage.request_keyframe("slot0")
check(len(stage.worker.jobs) == video.KEYFRAME_BURST,
      "fifty requests with no time passing spend the burst and no more: %d"
      % len(stage.worker.jobs))
check(stage._keyframes_refused == 50 - video.KEYFRAME_BURST,
      "and the refusals are counted so the log can say so: %d"
      % stage._keyframes_refused)
# Ten seconds of a guest asking as fast as it can, with the clock driven
# rather than slept through. The first version of this poked the bucket once
# and looped with no time passing, so it measured the burst and called it the
# sustained rate -- it would have passed at any refill interval at all.
stage = FakeStage()
clock = 1000.0
stage._keyframe_filled = clock
for step in range(1000):                       # 10s in hundredths
    clock += 0.01
    stage.request_keyframe("slot0", now=clock)
allowed = len(stage.worker.jobs)
expected = 10.0 / video.KEYFRAME_MIN_GAP
check(abs(allowed - (expected + video.KEYFRAME_BURST)) <= 1.5,
      "ten seconds of constant asking buys about %.0f keyframes, not 300: "
      "got %d" % (expected + video.KEYFRAME_BURST, allowed))
check(allowed * 15.0 / (10.0 * 30.0) <= 0.40,
      "which is at most 40%% of the frame budget at 30fps: %.0f%%"
      % (allowed * 15.0 / (10.0 * 30.0) * 100))

print("the event a browser's request arrives as is the one being watched for")
# Built the same way webrtcbin builds it, so a rename upstream fails here
# rather than silently going back to two seconds of black.
gi.require_version("GstVideo", "1.0")
from gi.repository import GstVideo
event = GstVideo.video_event_new_upstream_force_key_unit(
    Gst.CLOCK_TIME_NONE, True, 0)
check(event.type == Gst.EventType.CUSTOM_UPSTREAM,
      "it is a custom upstream event")
check(event.get_structure().has_name("GstForceKeyUnit"),
      "named GstForceKeyUnit, which is what the probe matches")

print("the probe is installed on the video source, and only that one")
src = open(os.path.join(ROOT, "fourthplayer", "video.py")).read()
feed = src[src.index("def _feed("):src.index("def _on_upstream(")]
check('if kind == "video":' in feed and "add_probe" in feed,
      "video gets the probe")
check(feed.index('if kind == "video":') < feed.index("add_probe"),
      "and audio does not, having no keyframes to ask for")

print(("FAILED: %d" % len(fails)) if fails else "test_keyframe: all ok")
sys.exit(1 if fails else 0)
