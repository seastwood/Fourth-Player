"""The parameter sets go out once, from the encoder, not twice from two places.

`config-interval=-1` was set on both the parser and the payloader. The parser's
copy is the one that matters: it puts SPS and PPS into the bitstream in front of
every keyframe, so they reach a guest as part of the keyframe and a guest who
joins mid-session is covered.

The payloader's copy did two kinds of damage, and both were reported from a
sofa before either was understood.

It sent them *twice*, and not the same twice -- the payloader inserts its own
cached copy from the caps rather than the encoder's, so the two disagree.
Measured on the guest's page:

    the two copies of NAL 7 in one keyframe differ:
    first  6742c033...0000ea6042
    then   6742c033...0000ea6046d0442324

Same profile, level and timing; the encoder's carries HRD and
bitstream-restriction fields the cached one lacks.

And it sent them as their own access unit: a forty-byte frame of `SPS PPS`
with no coded slice in it. A browser's own decoder shrugs that off. A page
drawing the picture itself is handed it as a frame, and a chunk with no
picture in it is what killed its decoder every few seconds -- streaming,
black, streaming, black, and then a fall back to WebRTC.

Read from the source: building a pipeline needs a display and an encoder, and
what is being asserted is one setting.
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


print("-- the parser still guarantees them, because something must --")
check(re.search(r'\{parser\} config-interval=-1', video) is not None,
      "h264parse/h265parse keeps config-interval=-1, so every keyframe carries "
      "the parameter sets and a mid-session joiner can start")

print("\n-- and the payloader does not add a second copy --")
check(re.search(r'\{payloader\} pt=96 config-interval=0', video) is not None,
      "the payloader is config-interval=0")
check(re.search(r'\{payloader\}[^"]*config-interval=-1', video) is None,
      "and nowhere still asks it for -1")

print("\n-- with the reason recorded next to it --")
at = video.index("{payloader} pt=96")
# Whitespace-normalised, and the comment markers taken out, so a check reads
# the prose rather than where it happens to wrap. A test that breaks when a
# comment is re-flowed has already cost this repo a red suite once.
why = re.sub(r"\s+", " ", video[max(0, at - 3500):at].replace("#", " "))
check("disagree" in why,
      "that the two copies disagreed is written down, not just that there were "
      "two -- keeping the wrong one is a different fault from keeping one")
check("no coded slice in it" in why,
      "and that the payloader's copy arrives as an access unit of its own, "
      "which is the half that broke the decoder")

print("\n-- the client-side repairs stay --")
# The host is one host. A page has to cope with hosts it does not control, and
# these two are what it took to survive this one.
paint = open(os.path.join(REPO, "web", "paint.js"), encoding="utf-8").read()
check("function tidyParameterSets" in paint,
      "the page still reduces repeated parameter sets to one")
check("function hasPicture" in paint,
      "and still refuses to hand over a frame with no picture in it")

print()
if fails:
    print("FAILURES: %d" % len(fails))
    sys.exit(1)
print("test_configinterval: all ok")
