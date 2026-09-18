"""The appsinks must survive a keyframe without throwing packets away.

Both sinks hold RTP packets, not frames, and both had a depth chosen as if
they held frames: four and sixteen. A keyframe is hundreds of packets pushed
as one burst, so four of them meant every keyframe arrived at the guests with
holes in it -- they could not decode it, asked for another, and that one was
cut up the same way. The log called it "guests are losing the picture faster
than sending keyframes can fix".

What made it look like a multi-client problem: the consumer copies each packet
once per guest before it can take the next one, so a second guest halves the
rate the sink drains at. One client mostly got away with it.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


source = open(os.path.join(ROOT, "fourthplayer", "video.py"),
              encoding="utf-8").read()


def number(name):
    found = re.search(r"^%s = (\d+)$" % name, source, re.M)
    return int(found.group(1)) if found else None


video = number("VIDEO_SINK_PACKETS")
audio = number("AUDIO_SINK_PACKETS")
check(video is not None, "the video sink's depth is a named number")
check(audio is not None, "and so is the sound's")
if video is None or audio is None:
    print("FAILED")
    sys.exit(1)

print("both sinks are described with those numbers and not a literal")
for name, sink in (("vsink", "VIDEO_SINK_PACKETS"), ("asink", "AUDIO_SINK_PACKETS")):
    spot = source.find("name=%s" % name)
    check(spot > 0 and "max-buffers={%s}" % sink in source[spot:spot + 400],
          "%s is sized by %s" % (name, sink))

print("and the video one is big enough for a keyframe")
try:
    from fourthplayer.config import Config
    mtu = Config().rtp_mtu
except Exception:
    mtu = 1200
# A megabyte is a generous keyframe: 1440p on a quiet desktop at the top of
# the bitrate range. Nothing here should be dropping one of those.
check(video * mtu >= 1000000,
      "%d packets of %d bytes holds a %.1f MB keyframe"
      % (video, mtu, video * mtu / 1e6))
check(video >= 256, "and is not back to a handful of packets")

print("the sound one holds a second of speech")
# Opus frames here are 10-60ms; the shortest is the one that needs the most
# packets, so size against that.
check(audio * 10 >= 1000,
      "%d packets at the shortest frame size is %d ms" % (audio, audio * 10))

print("both still drop as a last resort rather than growing without limit")
check(source.count("drop=true") >= 2,
      "neither sink is allowed to grow forever if nothing drains it")

print("FAILED" if fails else "PASSED")
sys.exit(1 if fails else 0)
