"""What the offer says about the sound, which until now was nothing.

There was no `a=fmtp` line for Opus at all. That is not a missing nicety:
RFC 7587 says a stream with nothing said about it is mono, and both browsers
believe it -- so the host encoded 48 kHz stereo and every guest folded it into
one channel on arrival. A game's music mixed into the middle of the head is
exactly what "the sound is poor while the picture is fine" sounds like, and
nothing on the host could see it, because the host's half was correct
throughout.

Read as text rather than through GStreamer, so this runs on any machine. The
caps string is what becomes the SDP -- verified against a real webrtcbin on
the host, which turns these fields into
`a=fmtp:97 sprop-stereo=1;stereo=1;useinbandfec=1;minptime=10;maxaveragebitrate=128000`
-- and it is the string that gets edited by accident.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


video = open(os.path.join(ROOT, "fourthplayer", "video.py")).read()
config = open(os.path.join(ROOT, "fourthplayer", "config.py")).read()
app = open(os.path.join(ROOT, "web", "app.js")).read()

# The audio half of _rtp_caps: from the branch that names it to the return.
caps = video.split('if kind == "audio":')[1].split("encoding = self.stage")[0]

print("the offer describes the sound it is sending")
check("sprop-stereo=(string)1" in caps,
      "sprop-stereo says two channels are being sent -- without it, mono")
check("encoding-params=(string)2" in caps,
      "and the rtpmap still says opus/48000/2, which is a different statement")
check("useinbandfec=(string)1" in caps,
      "the decoder is told it may use the error correction the encoder makes")
check("minptime" in caps and "maxaveragebitrate" in caps,
      "and the frame length and bitrate are stated where a receiver looks")

print("and it describes what is actually being encoded")
frame = re.search(r"audio_frame_ms: int = (\d+)", config).group(1)
rate = re.search(r"audio_bitrate_kbps: int = (\d+)", config).group(1)
check("minptime=(string){cfg.audio_frame_ms}" in caps,
      "minptime is the encoder's frame size rather than a number typed twice")
check("maxaveragebitrate=(string){cfg.audio_bitrate_kbps * 1000}" in caps,
      "and the bitrate is the encoder's, in bits, for the same reason")
check(f"frame-size={{cfg.audio_frame_ms}}" in video,
      "the encoder reads the same frame size (%s ms)" % frame)
check("bitrate={cfg.audio_bitrate_kbps * 1000}" in video,
      "and the same bitrate (%s kb/s)" % rate)

print("nothing was taken away")
check("rtcp-fb" not in caps,
      "the sound still asks for no retransmission: Opus carries its own "
      "correction, and a packet sent again arrives after the gap it was for")

print("the guest says what it did with the sound")
check("tellAboutSound" in app, "the page reports on the sound it decoded")
told = app.split("async function tellAboutSound")[1].split("\nasync function")[0]
check("channels" in told,
      "how many channels it settled on, which is the whole question here")
check("concealedSamples" in told,
      "and how much of it the decoder had to invent, which is what crackle is")
check("soundTold" in told,
      "once per connection, not once every two seconds")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("all good")
