"""Reviving a capture pipeline that has stopped.

A GStreamer error is posted against the whole pipeline rather than the branch
that raised it. So one guest's data channel failing left the capture stopped,
and every guest afterwards was refused with "webrtcbin would not follow the
pipeline into PLAYING" -- an open session that served nobody, and could only be
fixed by restarting the service.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    from fourthplayer import video as videolib
    from fourthplayer.video import Stage, init
    from fourthplayer.config import Config
except (ImportError, ValueError) as exc:
    print("SKIPPED: %s -- needs the GStreamer bindings, which live on the host"
          % exc)
    sys.exit(0)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


class Loop:
    def call_soon_threadsafe(self, fn, *a): fn(*a)
    def run_in_executor(self, ex, fn, *a): return fn(*a)
    def create_task(self, *a, **k): return None


init()
cfg = Config()
# Nothing here needs a screen or a GPU: a test pattern exercises the same
# state machine as the real capture and runs anywhere.
cfg.hardware_encode = False
cfg.audio = False
cfg.width, cfg.height, cfg.fps, cfg.bitrate_kbps = 320, 180, 15, 300

stage = Stage.__new__(Stage)
stage.cfg = cfg
stage.loop = Loop()
stage.peers = {}
stage.has_audio = False
stage.public_ip = ""
stage._fmtp = ""
# Whichever software H.264 encoder this machine has, rather than the one this
# machine had. x264enc is in gstreamer's "ugly" set and openh264enc is in
# "bad"; a Mint desktop installs bad and not ugly, and naming one of them
# here made this suite fail on a perfectly good machine with `no element
# "x264enc"` -- a fault in the test, reported as a fault in the program.
ENCODER = videolib._first_present(videolib._ELEMENTS["h264"][1])
if ENCODER is None:
    print("SKIPPED: no software H.264 encoder here, so there is no pipeline "
          "to recover")
    sys.exit(0)
# openh264enc has neither of x264enc's knobs and refuses to start if given
# them, so the settings go with the encoder rather than beside it.
TUNING = "" if ENCODER == "openh264enc" else " tune=zerolatency"
stage.pipeline = Gst.parse_launch(
    "videotestsrc is-live=true ! video/x-raw,width=320,height=180,framerate=15/1 "
    "! " + ENCODER + TUNING + " ! h264parse ! rtph264pay ! "
    "application/x-rtp,media=video,encoding-name=H264,payload=96 ! "
    "tee name=vtee allow-not-linked=true")
stage.tee = stage.pipeline.get_by_name("vtee")
stage.audio_tee = None

print("a running pipeline is left alone")
stage.pipeline.set_state(Gst.State.PLAYING)
stage.pipeline.get_state(3 * Gst.SECOND)
check(stage.ensure_playing(), "ensure_playing reports it is running")
_c, state, _p = stage.pipeline.get_state(0)
check(state == Gst.State.PLAYING, "and it still is")

print("\na pipeline that has stopped is put back")
stage.pipeline.set_state(Gst.State.PAUSED)
stage.pipeline.get_state(3 * Gst.SECOND)
_c, state, _p = stage.pipeline.get_state(0)
check(state != Gst.State.PLAYING, "it is genuinely not playing first")
check(stage.ensure_playing(), "ensure_playing brings it back")
_c, state, _p = stage.pipeline.get_state(0)
check(state == Gst.State.PLAYING, "and it is playing again")

print("\neven from a full stop, which is what an error can leave behind")
stage.pipeline.set_state(Gst.State.NULL)
stage.pipeline.get_state(3 * Gst.SECOND)
check(stage.ensure_playing(), "a stopped pipeline is restarted")
_c, state, _p = stage.pipeline.get_state(0)
check(state == Gst.State.PLAYING, "and reaches PLAYING")

stage.pipeline.set_state(Gst.State.NULL)
print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
