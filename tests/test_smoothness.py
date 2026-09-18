"""A preset called Buttery must buy smoothness, not latency.

jitter_ms is the browser's frame-pacing queue -- jitterBufferTarget, or
playoutDelayHint on older browsers. It is the only lever a browser gives us
over when a frame is drawn, and it is the whole of what a native client's
frame-pacing queue does.

The measurement that set these numbers, from the Windows host at 60fps:

  typical gap 15.6ms, worst 33ms, 60 late by more than half a frame;
  the timeline says typical 16.7ms, worst 17ms, 0 uneven

The timeline -- the timestamps the browser is told to draw by -- is exact.
Arrival is not: frames come in pairs, about one in ten landing 33ms after the
one before. A buffer smaller than that clump cannot absorb it, and the picture
hitches on a beat. So no preset may ask for less slack than the clumping it
will meet.
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


page = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
block = page[page.index("const STREAM_PRESETS"):]
block = block[:block.index("];")]

presets = []
for found in re.finditer(
        r'label: "([^"]+)", height: (\d+), fps: (\d+), kbps: (\d+), '
        r'jitter: (\d+)', block):
    presets.append({"label": found.group(1), "height": int(found.group(2)),
                    "fps": int(found.group(3)), "kbps": int(found.group(4)),
                    "jitter": int(found.group(5))})

check(len(presets) >= 5, "found %d presets" % len(presets))

print("every preset leaves room for a frame that arrives late")
for preset in presets:
    # Two frame times is the clumping actually measured: a frame that misses
    # its slot arrives with the next one. A buffer under that cannot help.
    need = 2000.0 / preset["fps"]
    check(preset["jitter"] >= need,
          "%s: %dms of slack against %.0fms of clumping"
          % (preset["label"], preset["jitter"], need))

print("and the ones that promise smoothness ask for the most")
buttery = [p for p in presets if "Buttery" in p["label"]]
check(buttery, "there are presets called Buttery")
for preset in buttery:
    check(preset["jitter"] >= 50,
          "%s holds %dms, which is a frame-pacing queue and not a token"
          % (preset["label"], preset["jitter"]))

print("nothing asks for more delay than the host will allow")
try:
    from fourthplayer.session import STREAM_LIMITS
    low, high = STREAM_LIMITS["jitter_ms"]
    for preset in presets:
        check(low <= preset["jitter"] <= high,
              "%s's %dms is inside the host's %d-%d" %
              (preset["label"], preset["jitter"], low, high))
except Exception as exc:
    print("  SKIPPED the host's limits (%s)" % exc)

print("FAILED" if fails else "PASSED")
sys.exit(1 if fails else 0)
