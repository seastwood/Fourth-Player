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

print("a frame too big for one message is sent in pieces")
check("limit = 60000" in video, "well under what a browser will accept")
check('struct.pack("<BQ"' in video,
      "each piece says what it is: flags and the capture time")
check("flags |= 2" in video and "flags |= 4" in video,
      "which piece starts a frame and which ends it")

print("and the far end puts them back together")
worker = open(os.path.join(ROOT, "web", "frames.js"), encoding="utf-8").read()
check("const FIRST = 2, LAST = 4;" in worker, "reading the same two flags")
check("getBigUint64(1, true)" in worker, "and the same little-endian stamp")
check("building.parts.push(body)" in worker, "the pieces are collected")
check("if (!(flags & LAST)) return;" in worker,
      "and nothing is decoded until the last one arrives")

print("nothing anywhere still reaches for a transform")
for name, text in (("the host", video),
                   ("the worker", worker),
                   ("the page", open(os.path.join(ROOT, "web", "paint.js"),
                                     encoding="utf-8").read())):
    check("RtpScriptTransform" not in text and "onrtctransform" not in text,
          "%s has none" % name)

print("FAILED" if fails else "PASSED")
sys.exit(1 if fails else 0)
