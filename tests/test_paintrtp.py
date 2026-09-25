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

print("\n-- and a restart gets a receiver that has not begun, not this one --")
# The same rule, in the second place it applies, found the hard way.
#
# The initial attach was fixed above. Restarting was not: a decoder that had
# been working and stopped was rebuilt in place, the transform re-attached to
# the receiver it had been reading, and that attach *succeeds* and then
# delivers nothing for ever. The host's log across one session: the beat armed
# ten times and "0 handed over by the transform ... canvas none".
#
# From the chair: "it streams the video, then black, then streams video, then
# black. it does that a few times until it reverts to webrtc" -- the first
# attempt works because the track has just arrived, and every restart after it
# is dead.
restart = app[app.index("function restartTheDrawing"):]
restart = restart[:restart.index("\nfunction ", 10)]
check('paintMethod !== "rtp"' in restart,
      "the restart distinguishes the mode: only rtp cannot restart in place")
check("renewSoon(" in restart,
      "and asks for a fresh media connection instead of a fresh decoder")
check("true" in restart[restart.index("renewSoon("):],
      "forcing it, because the connection is perfectly healthy -- it is only "
      "this page's way in to the frames that is spent")
check("startPainting()" in restart,
      "while the data-channel modes still simply start again, which is right "
      "for them: they read a channel, not a receiver")

print("\n-- and nothing restarts in place behind its back --")
gone = app[app.index("painter.whenGone("):]
gone = gone[:gone.index("askHostForKeyframe();")]
check("restartTheDrawing()" in gone,
      "the recovery path goes through it")
check("startPainting();" not in gone,
      "and does not also start one in place, which is the bug itself")
spelling = app[app.index("function tryAnotherSpelling"):]
spelling = spelling[:spelling.index("\nasync function ")]
check("restartTheDrawing()" in spelling,
      "so does trying another spelling with no painter left")

print("\n-- the reason is recorded where the condition is --")
check("datachannel event arrives after a track event" in start
      or "after a track event" in start,
      "the ordering that caused it is stated, so the check is not tightened "
      "back up by somebody tidying")

# The mode goes back to the one the viewer asked for whenever a new offer
# arrives.
#
# giveUpPainting sets paintMethod to "browser" and deliberately leaves
# paintChoice alone, so a failure does not overwrite the choice. Nothing put
# the mode back -- and the track handler reads paintMethod to decide whether
# the <video> element may have the video track. Handing the element the track
# starts the receiver, and a transform attached to a started receiver is
# handed nothing for ever. So one give-up made every later connection fail the
# same way on a connection with nothing wrong with it.
#
# Reported as three separate things: media track only works when you first
# join, it cannot be switched on mid-stream, and a picture that stops never
# comes back without a reload.
offer = app[app.index("paintGaveUp = false;"):]
check("paintMethod = wantedPaintMethod();" in offer[:1400],
      "a new offer puts the drawing mode back to the chosen one")
check(offer.index("paintMethod = wantedPaintMethod();")
      < offer.index("stopPainting("),
      "before the old painter is put down, so the track handler that follows "
      "reads the restored mode")
check("paintGaveUp = false;" in offer[:200],
      "and giving up is not what a new connection inherits")

# And the same restore at the top of restartTheDrawing, which is the one that
# unwedges minimise-and-return.
#
# paintsHere() reads paintMethod, and giveUpPainting sets it to "browser". So
# once anything had given up, restarting the drawing hit that guard and did
# nothing for ever -- and the restore in the offer path could not help,
# because no offer ever arrives when this function is what would ask for one.
# The drawing is put down on purpose while the page is hidden, so minimising
# and coming back is the ordinary way in: reported as media track not
# rebuilding itself on reopening, and not coming back without a reload.
restart = app[app.index("function restartTheDrawing"):]
restart = restart[:restart.index("\nfunction ", 10)]
check("paintMethod = wantedPaintMethod();" in restart,
      "restarting the drawing puts the mode back to the chosen one")
check(restart.index("paintMethod = wantedPaintMethod();")
      < restart.index("if (!paintsHere()"),
      "before the guard that reads it, or the guard answers about the mode a "
      "failure left behind rather than the one that was chosen")
check("paintGaveUp" in restart,
      "and giving up is still respected, so a polled caller cannot retry for "
      "ever")

print()
if fails:
    print("FAILURES: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_paintrtp: all ok")
