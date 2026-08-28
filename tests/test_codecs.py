"""Choosing an encoding both ends can manage.

The picture is encoded once for everybody, so this is a property of the session
rather than of each guest. Getting it wrong is silent in the worst way: the
guest connects, the controller works, and the screen stays black -- which is
what happens to a browser sent something it cannot decode.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from fourthplayer.video import best_shared_codec, host_codecs, CODEC_PREFERENCE
except (ImportError, ValueError) as exc:
    print("SKIPPED: %s -- needs the GStreamer bindings, which live on the host"
          % exc)
    sys.exit(0)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


print("what this host can encode")
ours = host_codecs()
check(bool(ours), "at least one codec, got %r" % ours)
check("h264" in ours, "h264 is always there -- it is the one every browser takes")
check(list(ours) == [c for c in CODEC_PREFERENCE if c in ours],
      "listed best first, got %r" % ours)

print("\nthe better codec is taken when both ends have it")
if "h265" in ours:
    check(best_shared_codec(["video/H264", "video/H265"]) == "h265",
          "a browser offering both gets h265")
    check(best_shared_codec(["H265"]) == "h265", "bare names work too")
else:
    print("  (this host cannot encode h265; skipping those)")

print("\nand h264 whenever there is any doubt")
check(best_shared_codec(["video/H264", "video/VP9", "video/AV1"]) == "h264",
      "a browser without h265 gets h264")
check(best_shared_codec([]) == "h264",
      "a browser that says nothing gets h264 rather than a guess")
check(best_shared_codec(None) == "h264", "and so does one that says null")
check(best_shared_codec(["video/VP8", "video/VP9"]) == "h264",
      "a browser offering only what we cannot encode still gets our safest")

print("\ncase and prefixes do not matter, because browsers differ on both")
check(best_shared_codec(["VIDEO/h264"]) == "h264", "mixed case")
check(best_shared_codec(["h264"]) == "h264", "no prefix")

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
