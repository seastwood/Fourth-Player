"""The media-track drawing mode, which never once started.

There are three ways this page can draw the picture itself. Two read whole
frames off a data channel; the third, "WebCodecs (media track)", takes them
off the media track with an encoded transform, and exists precisely because a
data channel is SCTP -- which answers a lost packet by detecting it, at a cost
of about a second with nothing delivered, and which on this host also simply
runs out of send buffer at 1440p60.

It never worked. The report said "0 handed over by the transform, 0 fed to the
decoder, 0 came out, 0 painted", and the picture was black.

An encoded transform may only be attached to a receiver that has not begun
delivering. There is exactly one moment for it: the track event. startPainting
is called there -- and returned immediately, because it required the picture
*data channel* to be open, which the media-track mode does not use and which
cannot be open yet anyway: a datachannel event arrives after a track event.
The only call that got past that check came later, from the channel's own open
handler, by which time the receiver had long since begun.

Read from the source, because the failure is a browser one and the condition
that caused it is a single line.
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
app = open(os.path.join(REPO, "web", "app.js"), encoding="utf-8").read()
paint = open(os.path.join(REPO, "web", "paint.js"), encoding="utf-8").read()

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


start = app[app.index("async function startPainting"):]
start = start[:start.index("\nasync function ") + 1
              if "\nasync function " in start[10:] else 4000]

print("-- the media-track mode does not wait on a channel it never reads --")
check('paintMethod !== "rtp"' in start,
      "the picture-channel requirement is skipped for it")
# The condition wraps over two lines, so match the statement rather than a
# single-line shape.
gate = re.search(r'if \(paintMethod !== "rtp"\s*&&\s*\(!pictureChannel',
                 start)
check(gate is not None,
      "and the skip is part of the gate itself, not a later branch that runs "
      "after the moment has passed")
check('if (!canvas) return;' in start,
      "a missing canvas still stops every mode, because none can draw without one")

print("\n-- and it is asked about by its own name --")
check('paintMethodById(paintMethod).ok()' in start,
      "the chosen method is the one whose support is checked")
check('paintMethodById("here").ok()' not in start,
      "not \"here\", which skipped the encoded-transform check -- the one "
      "capability the failing mode needed")

print("\n-- the transform is still only attached at the one moment it can be --")
check("startFromTrack" in app and "startFromTrack" in paint,
      "the track path exists on both sides")
track = app[app.index('pc.addEventListener("track"'):]
track = track[:track.index("pc.addEventListener(\"datachannel\"")]
check("startPainting()" in track,
      "startPainting is called from the track event")
check("transform" in paint and "RTCRtpScriptTransform" in paint,
      "and that is what leads to the transform")
check("never detached" in paint or "permanently stops" in paint,
      "with the one-way nature of it written down, because a retry cannot "
      "undo it")

print("\n-- and the element is never given the track it must not start --")
# Handing a track to a sink starts the receiver, and a transform attached to a
# started receiver delivers nothing. It was being started by the track handler
# itself, two lines before the transform went on: srcObject and play() on a
# stream containing the video track. The transform then attached, said so, and
# was handed nothing for ever -- "0 handed over by the transform".
check('paintMethod === "rtp"' in track,
      "the track handler knows about the mode")
check("getAudioTracks" in track,
      "and gives the element the sound only, because the element is what "
      "plays it")
check(track.index("show = new MediaStream(sound)") < track.index("startPlayback()"),
      "the swap happens before play(), not after")
check("wholeStream = incoming" in track,
      "with the whole stream kept, so switching back to the browser drawing "
      "it does not hand the element a stream with no picture in it")

print("\n-- the reason is recorded where the condition is --")
check("datachannel event arrives after a track event" in start
      or "after a track event" in start,
      "the ordering that caused it is stated, so the check is not tightened "
      "back up by somebody tidying")

print()
if fails:
    print("FAILURES: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_paintrtp: all ok")
