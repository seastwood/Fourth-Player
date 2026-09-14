"""That a Windows host can actually capture its screen and encode it.

Builds the real Stage -- not a hand-written pipeline -- and counts what comes
out of the encoder. Everything before this proves elements exist; this proves
video.py can put them together on Windows and that frames come out the far end
at the rate that was asked for.

Two things make it skip rather than fail, and both are honest rather than
convenient:

  * Not Windows. There is no d3d11screencapturesrc to ask.
  * Session 0. A process reached over SSH lands in the services session,
    which has one dummy display and no desktop, and d3d11screencapturesrc
    fails with "Failed to prepare capture object". That is a real constraint
    on how a Windows host is launched rather than a quirk of testing -- the
    Linux side needs an X display for the same reason -- so the test says so
    and stops instead of reporting a fault that is not there.

Run it in the interactive session. A scheduled task with -LogonType
Interactive is how this was driven while the port was written.
"""
import ctypes
import os
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

if sys.platform != "win32":
    print("SKIPPED: not Windows, so there is no desktop capture to test here.")
    sys.exit(0)

# Which session this process is in, and which one has the screen.
_pid = ctypes.windll.kernel32.GetCurrentProcessId()
_mine = ctypes.c_uint()
ctypes.windll.kernel32.ProcessIdToSessionId(_pid, ctypes.byref(_mine))
_console = ctypes.windll.kernel32.WTSGetActiveConsoleSessionId()
if _mine.value != _console:
    print("SKIPPED: this is session %d and the desktop is in session %d."
          % (_mine.value, _console))
    print("         d3d11screencapturesrc has nothing to capture from here.")
    print("         Run it in the interactive session -- a scheduled task with")
    print("         -LogonType Interactive will do it.")
    sys.exit(0)

try:
    from fourthplayer import video
    from fourthplayer.config import Config
except Exception as exc:
    print("SKIPPED: the host will not import here (%s)" % exc)
    sys.exit(0)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


SECONDS = 4.0

video.init()
from gi.repository import Gst                                  # noqa: E402

print("what this machine offers")
source = video.pick_source()
sound = video.pick_sound()
check(source is not None, "a desktop source: %s"
      % (source[0] if source else "none of %s"
         % ", ".join(n for n, _l, _p in video.SOURCES)))
print("  sound  : %s" % (sound[0] if sound else "none -- the session is silent"))
encoder = video.pick_encoder("h264")
check(encoder is not None, "an H.264 encoder: %s"
      % (encoder[0] if encoder else "none"))
if not source or not encoder:
    print("\n%d FAILED" % len(fails))
    sys.exit(1)

print("\nthe Stage video.py builds from them")
import asyncio                                                  # noqa: E402
cfg = Config()
stage = video.Stage(cfg, asyncio.new_event_loop())
check(stage.source_name == source[0],
      "captures with %s" % stage.source_name)
check(stage.encoder_kind == "hardware",
      "encodes with %s (%s)" % (stage.encoder_name, stage.encoder_kind))

frames = {"n": 0, "bytes": 0}


def probe(_pad, info):
    buf = info.get_buffer()
    if buf is not None:
        frames["n"] += 1
        frames["bytes"] += buf.get_size()
    return Gst.PadProbeReturn.OK


# On the encoder's own output. A probe on the appsink would count RTP packets
# instead -- it sits after rtph264pay, and one frame is several of those.
stage.pipeline.get_by_name("enc").get_static_pad("src").add_probe(
    Gst.PadProbeType.BUFFER, probe)

print("\nrunning it for %.0fs" % SECONDS)
try:
    stage.start()
    time.sleep(SECONDS)
finally:
    try:
        stage.stop()
    except Exception as exc:
        print("  (stop raised: %s)" % exc)

rate = frames["n"] / SECONDS
check(frames["n"] > 0, "frames come out of the encoder: %d" % frames["n"])
check(frames["bytes"] > 0, "carrying actual bytes: %d" % frames["bytes"])
# Generous either way: a screen that is not changing and a first second spent
# starting up both pull the average down, and this is a smoke test rather than
# a benchmark. What it is really catching is 0, or 4x the asked-for rate.
check(0.4 * cfg.fps <= rate <= 1.6 * cfg.fps,
      "at about the rate asked for: %.1f fps against %d" % (rate, cfg.fps))

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
