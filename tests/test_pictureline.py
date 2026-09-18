"""The picture, as whole frames, on a data channel.

Taking the encoded frames off the media track needed an encoded transform,
and a transform turned out to want a receiver that has not started yet, to
deliver nothing when attached to one that has, and to leave the receiver
delivering nothing for ever once removed. Three separate black screens came
out of that, and the last of them broke the ordinary WebRTC path as well.

moonlight-web, which this is modelled on, does not use one: it carries raw
Annex B frames on a data channel and decodes them with WebCodecs. So does
this. The media track is untouched, which is why switching between the two
ways of drawing is now free.
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


video = open(os.path.join(ROOT, "fourthplayer", "video.py"),
             encoding="utf-8").read()

print("the capture produces whole frames as well as packets")
check("tee name=frames" in video, "the encoded stream is teed")
check("appsink name=fsink" in video, "one branch ends in a frame sink")
check("stream-format=byte-stream,alignment=au" in video,
      "carrying whole access units with start codes, which is what a "
      "WebCodecs decoder takes")
check("appsink name=vsink" in video, "and the other still makes RTP packets")
spot = video.find("tee name=frames")
check(spot > 0 and video.find("{payloader}", spot) > spot,
      "the payloader is downstream of the tee, so both come from one encode")

print("and Windows is asked for the screen the better way, where it exists")
# Measured on the machine, same game, same everything else:
#   Desktop Duplication  every 15.5ms typical, worst 32ms, 146 of 600 uneven
#   Graphics Capture     every 16.7ms typical, worst 35ms,  17 of 600 uneven
# The typical interval goes from wrong to exactly right and the uneven
# quarter becomes an uneven twentieth.
check('_takes("d3d11screencapturesrc", "capture-api=wgc")' in video,
      "Graphics Capture is the default where the build has it")
check("CAPTURE_APIS" in video, "and the names are a table, not scattered")
check("no such way of capturing the screen" in video,
      "an unknown one is refused with a line rather than passed through to "
      "a pipeline that will not build")

print("and the frames are offered on a channel of their own")
check('"create-data-channel", "picture"' in video, "a channel called picture")
check('ordered=(boolean)true' in video[video.find("picture channel") - 600:
                                       video.find("picture channel") + 600]
      or "video_options" in video,
      "ordered and reliable: a frame with a hole in it is not a frame")
check("def send_frame" in video, "there is something that sends one")
check("frames_wanted" in video,
      "and it is off until a guest asks, so a guest watching the media track "
      "is not sent the picture twice")

print("an open channel is recognised by the enum, not by a guessed number")
# It compared ready_state against 1. OPEN is 2; 1 is CONNECTING. So every
# frame but the one or two that landed during the handshake was dropped on
# the way out, which is exactly what the log showed: "the first encoded frame
# arrived here" and then nothing at all, for ever.
check("GstWebRTC.WebRTCDataChannelState.OPEN" in video,
      "the state is compared against the name")
check("ready_state != 1" not in video, "and not against a number")
check("_said_shut" in video,
      "and a channel that is not open says so once, rather than sixty times "
      "a second or not at all")

print("and a guest that has just asked gets a keyframe it can start from")
# Through force_keyframe, not the rate-limited path. That bucket exists
# because guests losing the picture ask faster than keyframes can fix it;
# this happens once, when somebody switches their own decoder on, and a
# decoder with nothing to decode against shows nothing at all. Refusing this
# one refuses the whole feature.
spot = video.find("want = text.lower() in")
block = video[spot:video.find("def ", spot)]
check("force_keyframe()" in block, "a keyframe is forced when frames are asked for")
check("request_keyframe" not in block,
      "not offered to the bucket that exists to refuse repeated asking -- that "
      "bucket is for a guest that has lost the picture, and a guest that has "
      "never had it cannot wait behind one")

print("and a channel that is not keeping up skips on purpose")
# The host produced 600 frames in 10 seconds with every gap at 16.7ms and the
# browser reported receiving 36 a second of it. Nothing is lost in flight on
# an ordered reliable channel, which leaves the send queue: one that has
# grown to megabytes is not being drained, and everything added to it after
# that is latency rather than picture.
check("buffered_amount" in video, "the queue depth is read")
check("frame_queue_limit()" in video,
      "against a limit worked out from the rate, not a fixed size")
check("and not key" in video,
      "and a keyframe is never the one skipped, since everything after it "
      "depends on it")
check("frames_skipped" in video, "the skipping is counted")
check("bytes behind" in video, "and said, so this is visible next time")

print("and a link that cannot carry the picture is sent less of it")
# A send queue that will not drain is the only honest signal that the link is
# narrower than the picture: nothing is lost, nothing errors, the bytes sit
# there -- and a guest sees the picture slow down while it fills and speed up
# while it drains, which is how it was described on mobile data.
check("FRAME_QUEUE_SECONDS" in video,
      "the limit is a time, because bytes mean nothing without the rate: a "
      "megabyte is a moment at 60 Mb/s and four seconds on mobile data")
check("def _ease_the_rate" in video, "and the encoder is told about it")
check("BITRATE_DOWN" in video and "BITRATE_UP" in video,
      "down quickly and up slowly, as every congestion control does")
check("BITRATE_FLOOR_KBPS" in video, "and never below something watchable")
check("BITRATE_CALM" in video,
      "with a quiet spell required before it climbs, so it does not oscillate")

print("a frame too big for one message is sent in pieces")
check("limit = 60000" in video, "well under what a browser will accept")
check('struct.pack("<BQHH"' in video,
      "each piece says what it is: flags, the capture time, which piece it "
      "is and how many there are")
check("flags |= 2" in video and "flags |= 4" in video,
      "which piece starts a frame and which ends it")
check("pieces = max(1," in video,
      "and the count is worked out from the frame, not guessed")

print("and the far end puts them back together")
worker = open(os.path.join(ROOT, "web", "frames.js"), encoding="utf-8").read()
paint = open(os.path.join(ROOT, "web", "paint.js"), encoding="utf-8").read()
check("const FIRST = 2, LAST = 4;" in worker, "reading the same two flags")
check("getBigUint64(1, true)" in worker, "and the same little-endian stamp")
check("getUint16(9, true)" in worker and "getUint16(11, true)" in worker,
      "and the piece number and count that follow it")
check("new Uint8Array(buffer, 13)" in worker,
      "with the body starting after all thirteen header bytes")
check("building.parts.push(body)" in worker, "the pieces are collected")
check("if (!(flags & LAST)) return;" in worker,
      "and nothing is decoded until the last one arrives")

print("a frame with a piece missing is dropped rather than decoded")
# The channel has a packet lifetime now, so pieces can go missing: a
# concatenated head and tail is not a frame, and feeding one to the decoder is
# how a stall becomes corruption.
check("FRAME_LIFETIME_MS" in video,
      "the host gives each piece a lifetime instead of retransmitting for ever")
check("max-packet-lifetime" in video, "on the picture channel itself")
check("ordered=(boolean)true, max-packet-lifetime" in video,
      "still ordered, so the only thing to cope with is a missing piece")
check("index !== building.next" in worker, "a gap in the numbering is noticed")
check("building.next !== building.pieces" in worker,
      "and so is a frame that ends early")
check("function lostFrame" in worker, "both drop the frame whole")
check('self.postMessage({ ask: "key" })' in worker,
      "and ask for a keyframe, since everything after a dropped frame decodes "
      "against something that never arrived")
check("ASK_KEY_EVERY" in worker,
      "not once per lost frame: a host answering all of them spends the whole "
      "bitrate on recovery")
check('text.lower() == "key"' in video, "which the host takes")
check("self.stage.request_keyframe" in video, "through the rate limiter")

print("and the browser says what actually reached it")
# The host's own send queue read empty while the browser was receiving
# thirty-six of every sixty frames sent. An empty queue proves the bytes were
# handed to SCTP, not that they arrived; only the far end knows that.
check("function tell(" in worker, "the worker counts what arrived")
check("TELL_EVERY" in worker, "once a second, not once a frame")
check("tally" in worker and "tally" in paint,
      "and the page puts it on the channel, which the worker cannot reach")
check("_take_picture_report" in video, "the host reads it")
check("frames_arriving" in video, "as a share of what it sent")
check("FRAMES_ARRIVING_LOW" in video and "FRAMES_ARRIVING_GOOD" in video,
      "and steers the encoder by it")
check("def _arriving" in video,
      "by the worst-off guest, since the encoder is shared")
check("def note_arrivals" in video,
      "stepping at most once per report, so one bad second does not walk the "
      "encoder to the floor")
check("_said_blind" in video,
      "and a queue depth that cannot be read says so, because a signal that "
      "silently reads zero looks like a link that is keeping up")

print("nothing anywhere still reaches for a transform")
for name, text in (("the host", video),
                   ("the worker", worker),
                   ("the page", paint)):
    check("RtpScriptTransform" not in text and "onrtctransform" not in text,
          "%s has none" % name)

print("FAILED" if fails else "PASSED")
sys.exit(1 if fails else 0)
