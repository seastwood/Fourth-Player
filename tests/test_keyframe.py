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
        self._last_keyframe = 0.0
        self.forced = 0

    def force_keyframe(self):
        self.forced += 1

    request_keyframe = video.Stage.request_keyframe


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
check(len(stage.worker.jobs) == 1,
      "twenty at once is still one: %d" % len(stage.worker.jobs))

print("and asking again later works")
stage._last_keyframe -= (video.KEYFRAME_MIN_GAP + 0.01)
stage.request_keyframe("slot0")
check(len(stage.worker.jobs) == 2, "once the gap has passed")
check(video.KEYFRAME_MIN_GAP <= 1.0,
      "the gap is short enough to recover in a blink: %.2fs"
      % video.KEYFRAME_MIN_GAP)

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
