"""Keeping the stream inside the bitrate it was asked for.

A Windows host set to 5 Mb/s sent 9.3 Mb/s steadily for twenty minutes, to two
guests whose picture was choppy the whole time. Nothing was lost on the wire --
both browsers reported zero packet loss -- and the host was not struggling. It
was simply sending far more than it had been told to.

Two causes, and both are the kind that hide:

The encoder was never given a buffer constraint. `cpb_ms` is turned into a CPB
size and handed to the VA encoders, and the four NVENC lines quietly did not
take it, so on Windows the number in the config did nothing at all and NVENC
chose its own window.

And guests asking for keyframes were granted two a second. A keyframe costs
roughly fifteen ordinary frames and the encoder is shared, so one guest's
recovery is billed to everybody; the extra traffic made frames late, late
frames were dropped, and a dropped frame produces another request.
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


# Read the table as text rather than importing it: this must hold on machines
# with no GStreamer, and the thing being checked is what the pipeline string
# says, which is exactly what the table is.
source = open(os.path.join(ROOT, "fourthplayer", "video.py")).read()
table = source[source.index("ENCODERS = {"):source.index("# Where the picture comes from")]
lines = re.findall(r'\("([a-z0-9]+enc)", "(\w+)", \w+,\s*((?:"[^"]*"\s*)+)\)', table)
check(len(lines) >= 10, "the encoder table was found and parsed (%d entries)"
      % len(lines))

print()
print("every hardware encoder is given a buffer to stay inside")
for name, kind, settings in lines:
    if kind != "hardware":
        continue
    # cpb-size on VA, vbv-buffer-size on NVENC; Media Foundation and v4l2
    # expose no such property, and are named here so that is a decision
    # somebody made rather than an omission nobody noticed.
    if name.startswith(("mf", "v4l2")):
        continue
    check("{cpb}" in settings,
          "%s is told its buffer size, so its bitrate is a limit and not a "
          "hope" % name)

print()
print("and the bitrate is passed in the units the property is documented in")
for name, kind, settings in lines:
    check("bitrate={kbps}" in settings or "bitrate={bps}" in settings
          or "video_bitrate={bps}" in settings,
          "%s is given a bitrate" % name)

print()
print("keyframes forced by guests cannot eat the bitrate")
from_source = re.search(r"^KEYFRAME_MIN_GAP = ([0-9.]+)", source, re.M)
check(from_source is not None, "the limit exists")
gap = float(from_source.group(1)) if from_source else 0.0
# At 30fps a keyframe is worth about fifteen frames, so one every half second
# is roughly half the budget spent on recovery. The number that matters is
# how much of the stream keyframes may claim, not the gap itself.
share = (15.0 / gap) / 30.0 if gap else 1.0
check(share <= 0.40,
      "forced keyframes may claim at most 40%% of the frame budget "
      "(gap %.2fs gives %.0f%%)" % (gap, share * 100))
check(gap >= 1.0,
      "the gap is at least a second (%.2fs); the storm ran at two a second"
      % gap)

print()
print("a storm is visible in the log rather than silent")
check("_keyframes_refused" in source,
      "refused requests are counted, not dropped on the floor")
check(re.search(r"refused.*(since the last|guests are losing)", source, re.S),
      "and said out loud, so a host spending its bitrate on recovery looks "
      "like one")

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
