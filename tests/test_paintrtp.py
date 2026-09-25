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

# A start that belongs to a connection which has gone must not attach, and
# must not block the one that replaces it.
#
# startPainting awaits -- it asks the browser whether it can decode a codec
# rather than constructing one and hoping -- and stopPainting happens while it
# is suspended. The stale call woke up afterwards and attached its transform
# to whatever receiver was current, which on a renewal has already begun
# delivering; and while it slept, paintStarting was still set, so the call
# from the new connection's track handler -- the one moment a transform can be
# attached at all -- returned immediately as "a start is already in progress".
#
# Between them: media track works on a fresh join and never again, because a
# join has no earlier call in flight and every renewal does. The host's log
# said it over and over -- "0 handed over by the transform, 0 fed to the
# decoder, 0 came out" with megabytes arriving on the connection.
check("let paintEra = 0;" in app,
      "starts are stamped with the connection they belong to")
start = app[app.index("async function startPainting"):]
start = start[:start.index("\nfunction ", 10)]
check("const era = paintEra;" in start,
      "the stamp is taken before the browser is asked")
check("if (era !== paintEra) return;" in start,
      "and a start whose connection has gone does not attach")
check(start.index("if (era !== paintEra) return;")
      < start.index("if (painter || !paintsHere())"),
      "checked before anything is built")
stop = app[app.index("function stopPainting"):]
stop = stop[:stop.index("\nfunction ", 10)]
check("paintEra += 1;" in stop,
      "putting the painter down ends that era")
check("paintStarting = false;" in stop,
      "and clears the in-progress flag, or the next connection's one chance "
      "to attach a transform is refused as a start already under way")

# The transform goes on in the same turn the worker is made, not when the
# worker answers.
#
# It used to wait for the worker's "ready" message -- a whole script load and
# run away, tens to hundreds of milliseconds on a phone. The rule it has to
# beat is measured in frames: a transform attached to a receiver that has
# already carried one is handed nothing, for ever.
#
# So whether it worked depended on something the page does not control. On a
# fresh join the host has not started sending, and the worker got there first.
# On a renewal the host is already streaming and the first packet arrives at
# once, so the receiver had begun before the worker said a word. That is
# "media track works when I join and never again", and the log said "0 handed
# over by the transform, 0 fed to the decoder, 0 came out" every time.
# It goes on when the worker says it is listening, which is neither of the two
# obvious moments and both of those are wrong.
#
# Not in the turn the worker is made: an rtctransform event is fired at the
# worker the instant the transform is constructed and is NOT queued the way a
# message is, so a worker whose script has not run has no handler and the
# event is lost. That reads exactly like attaching too late -- "0 handed over
# by the transform" -- while the page's own measurement of the receiver showed
# hundreds of packets arriving with the transform still attached.
#
# Not on "ready" either, which is the last line of a 58 KB script: by then the
# receiver has carried frames on any connection where the host is already
# streaming, and a transform attached after that delivers nothing for ever.
frames = open(os.path.join(REPO, "web", "frames.js"), encoding="utf-8").read()
check("if (m.listening) { attach(); return; }" in paint,
      "the transform goes on when the worker says it is listening")
check("self.postMessage({ listening: true });" in frames,
      "which the worker says as soon as it has a handler for the event")
check(frames.index("self.onrtctransform") < frames.index("let decode")
      if "let decode" in frames else True,
      "installed near the top of the file rather than after everything it "
      "uses")
check(frames.index("self.postMessage({ listening: true });")
      < frames.index("self.postMessage({ ready: true });"),
      "and long before ready, which is the last line of the script")
check("const transformsWaiting = [];" in frames
      and "for (const early of transformsWaiting.splice(0))" in frames,
      "anything that arrives before the real handler exists is held and "
      "handed over, not dropped")
ready = paint[paint.index("if (m.ready) {"):]
check("new RTCRtpScriptTransform" not in ready[:900],
      "and the transform is not attached on ready, which is too late")
check("it.postMessage({ start:" in ready[:900],
      "only the start message waits for ready, and frames arriving before "
      "there is a decoder are dropped by take()")

# And nothing is awaited in front of the painter in this mode.
#
# pickCodec awaits VideoDecoder.isConfigSupported -- tens of milliseconds --
# and in this mode the transform has to be attached before the receiver has
# carried a single frame. On a renewal the host is already streaming, so the
# receiver began during that await every time and the transform was handed
# nothing. Moving the attach earlier inside the painter did not help while
# this await still stood in front of the painter being made at all.
check('if (paintMethod === "rtp") {' in start,
      "the media-track path is told apart where the codec is chosen")
rtp = start[start.index('if (paintMethod === "rtp") {'):]
rtp = rtp[:rtp.index("} else {")]
# The code, not the prose: the comment above it explains what awaiting cost.
code = "\n".join(line for line in rtp.splitlines()
                 if not line.lstrip().startswith(("*", "/*", "//")))
check("await" not in code,
      "and nothing is awaited on it: being wrong for one attempt is cheap, "
      "being late is not recoverable")
check("codec = paintTried < all.length ? all[paintTried] : \"\";" in rtp,
      "the spelling list is walked instead, which is what it is for")

print()
if fails:
    print("FAILURES: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_paintrtp: all ok")
