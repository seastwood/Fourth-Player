"""A capture that cannot keep up is not a capture that is lying about when.

These are two faults with opposite answers, and the log reported the second as
the first. It said

    the capture is producing 52.9 frames a second while every one of them is
    stamped as 60 -- the timeline runs 12% fast than real time

about a host with no videorate in its pipeline at all, whose timestamps were
tracking real time to within a median of a tenth of a millisecond. The claim
was believed twice, because it is stated so confidently, and a setting was
changed on the strength of it.

Both measurements needed to tell them apart were already being taken -- when
frames really arrived, and what they claim -- and were never compared to each
other. Now they are:

  * stamps summing to more or less than the real elapsed time is a timeline
    that drifts, which is wrong and worth a warning.
  * fewer frames than were asked for, honestly stamped, is a capture that
    cannot keep up. Real, worth saying, and fixed by fewer pixels or fewer
    frames rather than by a pacing setting.

Read from the source: reaching the real thing needs a display and an encoder.
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
video = open(os.path.join(REPO, "fourthplayer", "video.py"),
             encoding="utf-8").read()

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


at = video.index("rate = len(gaps) / total if total else 0")
body = video[at:at + 2600]
prose = re.sub(r"\s+", " ", body.replace("#", " "))

print("-- drift is measured, not inferred from the frame rate --")
check("drift = " in body and "stamped - total" in body,
      "the timeline is compared against real elapsed time")
check("stamped = sum(stamps)" in body,
      "using the stamps that were already being collected")
check(re.search(r'if abs\(drift\) > 0\.0\d', body) is not None,
      "and the drift warning is gated on the drift itself")

print("\n-- a rate shortfall says what it really is --")
check("cannot keep up" in prose or "managing" in prose,
      "a capture behind its requested rate is described as behind it")
check("the timestamps are fine" in prose,
      "and says explicitly that the timing is not the problem, because the "
      "old message said the opposite and was acted on")
check("Fewer pixels or a lower frame rate" in body,
      "with the answer that actually applies")

print("\n-- and the two cannot both be claimed at once --")
# elif, not a second if: a capture that is behind *and* drifting has one cause
# worth naming first, and two warnings about one window read as two faults.
check(re.search(r'if abs\(drift\) > 0\.0\d.*?\n(.*?\n)*?\s+elif rate and',
                body) is not None,
      "the rate message is an elif, so one window produces one complaint")

print("\n-- the wrong claim is gone --")
check("while every one of them is stamped as" not in video,
      "nothing still asserts that every frame is stamped at the nominal rate, "
      "which was false whenever nothing was re-gridding them")

print()
if fails:
    print("FAILURES: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_ratehonesty: all ok")
