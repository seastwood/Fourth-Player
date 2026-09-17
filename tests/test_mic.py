"""A guest's microphone, arriving on the host as a recording device.

Asked for as: send microphone input through the client to the host as a real
microphone device, so it can be used in Discord. Admin only, on a chip.

The transport is the WebRTC connection that already carries the picture out,
which is the part worth being pleased about -- no second protocol, no second
port, no daemon, and it is encrypted and NAT-traversed because the picture
already had to be.

What cannot be free is the "real microphone device" half. Windows has no
virtual microphone, so the audio is played into the playback half of a
loopback cable and whatever wants to listen selects the recording half. Linux
gets the same arrangement from a null sink for nothing.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from fourthplayer import accounts, micsink

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


print("it is a permission, and one of the serious ones")
check("mic" in accounts.CAPABILITIES, "`mic` can be granted")
check("mic" in accounts.NEEDS_CODE,
      "and asks for an authenticator code at the moment it is used, like "
      "`desk` -- it puts a live microphone from somebody's room onto the "
      "machine, where anything signed in can listen to it")

print()
print("nobody has it unless it was given")
import inspect
add = inspect.signature(accounts.add)
check(add.parameters["capabilities"].default == (),
      "a new account is given nothing at all, so this is opt-in like the rest")

print()
print("where the sound goes is a setting, not a guess")
from fourthplayer import config
check(hasattr(config.Config, "guest_mic_device")
      or "guest_mic_device" in config.Config.__dataclass_fields__,
      "the destination is configurable")
check(config.Config.__dataclass_fields__["guest_mic_device"].default == "",
      "and empty by default -- picking one automatically would put a guest's "
      "voice into somebody's speakers the first time they pressed the button, "
      "and the difference between that and what was wanted is a room full of "
      "feedback")
check(micsink.where(config.Config()) == "",
      "so nothing is offered until somebody chooses")

print()
print("the pipeline plays, rather than records")
tail = micsink.describe("Speakers (VB-Audio Virtual Cable)")
check("audioconvert" in tail and "audioresample" in tail,
      "it converts and resamples, because a browser sends what it likes")
check("leaky=downstream" in tail,
      "and drops the oldest rather than backing up into the connection: this "
      "is somebody talking, and late audio is worse than missing audio")
check("sync=false" in tail,
      "and does not wait on a clock it does not share")

print()
print("the element is the one this platform actually has")
check(micsink.sink_element() in ("wasapi2sink", "pulsesink"),
      "%s here" % micsink.sink_element())

print()
print("a cable is suggested, never chosen")
check("suggest" in dir(micsink), "there is a suggestion")

# Ranked by which hint matched, not by which device came first. The Windows
# host lists a Voicemeeter input before the plain cable, so "first device
# matching any hint" suggested the complicated one.
import unittest.mock as mock
ordered = [("Voicemeeter In 5 (VB-Audio Voicemeeter VAIO)", "a"),
           ("Speakers (VB-Audio Virtual Cable)", "b"),
           ("Speakers (Focusrite USB Audio)", "c")]
with mock.patch.object(micsink, "sinks", lambda _g: ordered):
    check(micsink.suggest(None) == "Speakers (VB-Audio Virtual Cable)",
          "the plain cable wins over a Voicemeeter listed before it: %r"
          % micsink.suggest(None))
with mock.patch.object(micsink, "sinks", lambda _g: [ordered[2]]):
    check(micsink.suggest(None) == "",
          "and a machine with no cable is offered nothing rather than its "
          "speakers")
source = open(os.path.join(ROOT, "fourthplayer", "micsink.py")).read()
check("Offered rather than chosen" in source,
      "and it says why it is only a suggestion")
for hint in ("vb-audio virtual cable", "voicemeeter", "null sink"):
    check(hint in micsink.CABLE_HINTS,
          "%r is recognised as a loopback cable" % hint)

print()
print("the host offers the line up front, not when it is wanted")
video = open(os.path.join(ROOT, "fourthplayer", "video.py")).read()
check("_add_mic_line" in video, "there is a line for it")
check(re.search(r"if micsink\.where\(self\.stage\.cfg\):\s*\n\s*self\._add_mic_line",
                video),
      "added only where the host has somewhere to play it -- an m-line a host "
      "cannot use is a promise nobody can keep")
check("RECVONLY" in video, "and it is the one line the host receives on")
check("transceiver is not getattr(self, \"mic_transceiver\", None)" in video,
      "and the loop that makes every other line one-way outward leaves it "
      "alone, which it would otherwise silently undo")

print()
print("and what arrives is checked before it is played")
check('"media=(string)audio" not in text' in video,
      "a pad that is not audio is not wired into somebody's speakers")
check("nowhere to play it" in video,
      "and a microphone with nowhere to go is said out loud rather than "
      "dropped in silence")

print()
print("the client only shows it to somebody who may use it")
app = open(os.path.join(ROOT, "web", "app.js")).read()
check('chip.hidden = !may("mic") || !micLine()' in app,
      "hidden without the permission, and without a line to speak on")
check("micTrack.stop()" in app,
      "and the microphone is stopped, not merely detached: a detached track "
      "leaves the browser's recording light on, which tells somebody they are "
      "being listened to when they are not")
check("replaceTrack" in app,
      "switching it on attaches a track to the line that is already there, "
      "rather than renegotiating mid-game")

print()
print("and the destination can be chosen without editing a file")
sess = open(os.path.join(ROOT, "fourthplayer", "session.py")).read()
page = open(os.path.join(ROOT, "web", "setup.html")).read()
script = open(os.path.join(ROOT, "web", "setup.js")).read()
check('"mic_sinks"' in sess, "the host publishes what it could play into")
check('"mic_suggestion"' in sess,
      "and which of them looks like a cable, as a hint")
check('guest_mic_device' in sess and "has no audio output called" in sess,
      "and refuses a device it does not have, rather than accepting a name "
      "that matches nothing and leaving somebody talking into it")
check('id="set-mic-device"' in page, "the page has the picker")
check("guest_mic_device: el(\"set-mic-device\")" in script,
      "and sends it")
check("Nowhere" in script,
      "with an explicit off, because off is a real choice and the default one")

print()
print("the host names the microphone's line rather than the page guessing")
# The page used to look for the transceiver whose direction was "sendonly".
# That cannot work, and was a deadlock: with no track attached yet a browser
# answers an offered recvonly line as *inactive*, so the line was never found,
# so no track could be attached, so it stayed inactive -- and the button never
# appeared. The host added the transceiver and can simply say which line it is.
check("mic_line" in video, "the offer carries the line number")
check("def mic_line_index" in video, "worked out from the offer itself")

# Exercised as the plain function it is; a pipeline is not needed to count
# m-lines, and the count is what a browser indexes its transceivers by.
body = video[video.index("def mic_line_index"):video.index("def describe_sdp")]
ns = {}
exec(body, ns)
index_of = ns["mic_line_index"]
two = "\n".join(["v=0", "m=video 9 x", "m=audio 9 x", "m=audio 9 x",
                  "m=application 0 y"])
check(index_of(two) == 2,
      "the second audio line is the microphone's, at index 2: %r"
      % index_of(two))
check(index_of("v=0\nm=video 9 x\nm=audio 9 x") is None,
      "one audio line means no microphone offered, not the game's sound "
      "mistaken for one")
check(index_of("v=0\nm=video 9 x") is None, "and no audio at all means none")

check("micLineIndex" in app, "the page keeps the number it was given")
check("micLineIndex == null) return null" in app,
      "and offers nothing when the host offered nothing")
check("typeof message.mic_line" in app,
      "read from the offer, and type-checked -- index 0 is a real answer and "
      "must not be mistaken for absence")

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
