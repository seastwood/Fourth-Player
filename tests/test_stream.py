"""Changing the picture from a phone, and what stops it going wrong.

These are dials on a live system, turned by somebody who cannot see the host's
load, and getting one wrong does not produce a worse picture -- it produces a
host that cannot keep up, which everybody reads as the network breaking. So
every value is bounded, only what actually changed is applied, and the whole
thing is behind a capability.

It is deliberately not behind an authenticator code. It cannot lock anybody
out, take a controller, or reach the machine; it is a dial, and a dial that
asks for six digits every time is a dial nobody turns.
"""
import asyncio
import dataclasses
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


try:
    from fourthplayer import accounts
    from fourthplayer.session import LiveSession
    from fourthplayer.config import Config
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)

LOOP = asyncio.new_event_loop()


class Stage:
    codec = "h264"
    encoder_name = "vah264enc"
    encoder_kind = "hardware"
    sending_width, sending_height = 1280, 720


def session(**over):
    s = LiveSession.__new__(LiveSession)
    s.cfg = dataclasses.replace(Config(), **over)
    s.stage = Stage()
    s.recaptured = []
    s.told = []

    async def recapture(codec, display=None):
        s.recaptured.append(codec)
    s._recapture = recapture
    s.notify = lambda message: s.told.append(message)
    return s


def ask(s, **settings):
    return LOOP.run_until_complete(s.set_stream(settings))


print("it is a capability, and one that does not ask for a code")
check("stream" in accounts.CAPABILITIES, "stream is a capability")
check("stream" not in accounts.NEEDS_CODE,
      "and is not one of the ones that asks for six digits every time")

print("\nwhat it reports is what is going out, not what was asked for")
s = session(width=1920, height=1080)
now = s.stream_settings()
check(now["sending"] == "1280x720",
      "a software cap that shrank the picture is reported as the size it "
      "actually sends: %s (asked for %dx%d)"
      % (now["sending"], now["width"], now["height"]))
check(now["encoder"] == "vah264enc" and now["hardware"],
      "and names the encoder doing it")

print("\nchanging something rebuilds the stream")
s = session(fps=30)
out = ask(s, fps=60)
check(s.cfg.fps == 60, "the setting takes")
check(s.recaptured == ["h264"],
      "and the stream is rebuilt, because none of this can be changed on a "
      "running encoder: %r" % (s.recaptured,))
check(out["changed"] == ["fps"], "and it says what moved: %r" % (out["changed"],))
check(any(m.get("t") == "stream" for m in s.told),
      "everybody is told, not just whoever turned the dial")

print("\nchanging nothing does nothing")
# Opening the panel and closing it again must not cost the room a second of
# picture, so a request that matches what is already set is not a change.
s = session(fps=60, bitrate_kbps=4000)
out = ask(s, fps=60, bitrate_kbps=4000)
check(out["changed"] == [], "nothing changed: %r" % (out["changed"],))
check(s.recaptured == [], "so nothing was rebuilt")
check(s.told == [], "and nobody was interrupted to be told so")

print("\nevery number is bounded")
s = session()
ask(s, fps=9999, bitrate_kbps=10 ** 9, jitter_ms=-50, queue_ms=0, cpb_ms=10 ** 6)
low_fps, high_fps = LiveSession.STREAM_LIMITS["fps"]
check(s.cfg.fps == high_fps, "fps is clamped to %d, not %s" % (high_fps, 9999))
check(s.cfg.bitrate_kbps == LiveSession.STREAM_LIMITS["bitrate_kbps"][1],
      "so is the bitrate: %d" % s.cfg.bitrate_kbps)
check(s.cfg.jitter_ms >= LiveSession.STREAM_LIMITS["jitter_ms"][0],
      "a negative buffer becomes the floor: %d" % s.cfg.jitter_ms)
check(s.cfg.queue_ms >= LiveSession.STREAM_LIMITS["queue_ms"][0],
      "and so does a zero send queue: %d" % s.cfg.queue_ms)

print("\nthe size is a name, not two numbers")
# Width follows height from a fixed table, so there is no way to ask for a
# shape the capture cannot produce.
s = session(width=1920, height=1080)
ask(s, height=540)
check((s.cfg.width, s.cfg.height) == (960, 540),
      "540 means 960x540: %dx%d" % (s.cfg.width, s.cfg.height))
try:
    ask(s, height=481)
    check(False, "a size that is not offered is refused")
except ValueError as exc:
    check(True, "a size that is not offered is refused: %s" % exc)

print("\nand the codec may be pinned or left alone")
s = session()
ask(s, codec="h264")
check(s.cfg.codec == "h264", "it can be pinned")
s2 = session(codec="h264")
ask(s2, codec="nonsense")
check(s2.cfg.codec == "h264", "and nonsense is ignored rather than obeyed")

print("\nand a request with no capture running does not fall over")
s = session()
s.stage = None
out = ask(s, fps=24)
check(s.cfg.fps == 24 and s.recaptured == [],
      "the setting is kept for the next capture, and none is rebuilt")

print()
if fails:
    print("FAILURES: %d" % len(fails))
    sys.exit(1)
print("test_stream: all ok")
