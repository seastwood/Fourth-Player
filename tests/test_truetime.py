"""A timestamp has to say when the picture was taken, not when it was due.

A live GstBaseSrc with do-timestamp off stamps frame n at n/fps, exactly,
for ever. That is a claim about when the frame was captured and on a desktop
capture it is false: these sources hand over a frame when the desktop gives
them one. Measured on the Windows host at 60fps over 609 frames -- typical
gap 15.6ms, worst 77ms, and 135 of them nowhere near the 16.7ms every
timestamp claimed.

The timestamp is what a guest's browser draws by, so it was drawing evenly
spaced frames whose contents had advanced by uneven amounts. That is the
pulse, and nothing downstream can undo it: the timestamps were wrong, not
late. Ruling late out took applying a 60ms jitter buffer and reading it back
off the receiver, which is why this is a test and not a comment.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


try:
    from fourthplayer import video
except Exception as exc:
    source = open(os.path.join(ROOT, "fourthplayer", "video.py"),
                  encoding="utf-8").read()
    block = source[source.index("SOURCES = ("):]
    block = block[:block.index("\n)")]
    check(block.count("do-timestamp=true") == block.count("name=capture"),
          "every capture asks for real timestamps (read, not imported: %s)"
          % str(exc)[:40])
    print("FAILED" if fails else "PASSED")
    sys.exit(1 if fails else 0)

print("every capture this host knows how to use stamps from the clock")
for element, line, _pointer in video.SOURCES:
    check("do-timestamp=true" in line,
          "%s carries do-timestamp=true" % element)
    check("name=capture" in line,
          "%s is still named so the probe and stop() can find it" % element)

print("and the measurement that found this is still watching")
check(hasattr(video.Stage, "_watch_the_capture"),
      "the capture's own pad is still probed")
check(hasattr(video.Stage, "_grab_report"),
      "and its numbers are still reported beside the pacing line")

print("FAILED" if fails else "PASSED")
sys.exit(1 if fails else 0)
