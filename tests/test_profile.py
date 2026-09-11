"""Saying so when a guest cannot decode the H.264 profile being offered.

The codec is negotiated: the host picks the best of H.264, H.265 and AV1 that
every guest can manage. The profile inside H.264 is not, and cannot be -- the
encoder encodes once for everybody, so it is one setting for the whole session.

That limitation is reasonable. Failing silently is not. A host set to Main
offers Main, a browser that only takes Constrained Baseline answers with the
video refused, and the guest gets a black screen with nothing anywhere saying
why. It read as a network fault for hours: "the host offered no reachable
address", which was true and was not the reason.
"""
import asyncio
import dataclasses
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
    from fourthplayer.session import LiveSession
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)

print("the profile-level-id the host advertises")
check(video.h264_profile_level_id("constrained-baseline", 1080) == "42e028",
      "constrained baseline at 1080p is 42e028")
check(video.h264_profile_level_id("main", 1080) == "4d0028",
      "main at 1080p is 4d0028 -- the one iOS Safari refused")
check(video.h264_profile_level_id("constrained-baseline", 720) == "42e01f",
      "and the level follows the frame size")

print("\na guest who cannot take it moves the session, rather than being told to")
said = []
told = []
recaptured = []


# A real dataclass, because the code replaces a field on it -- and because a
# stub that cannot be replaced would pass a test the production Config fails.
@dataclasses.dataclass
class Cfg:
    h264_profile: str = "main"
    height: int = 1080
    path: str = "/home/x/.config/fourth-player/config.json"


class Guest:
    label = "A guest"


class Stage:
    codec = "h264"


LOOP = asyncio.new_event_loop()


def session_for(profile="main", stage=True):
    s = LiveSession.__new__(LiveSession)
    s.cfg = Cfg(h264_profile=profile)
    s.codec = "h264"
    s.stage = Stage() if stage else None
    s.notify_one = lambda guest, message: told.append(message)

    async def recapture(codec, display=None):
        recaptured.append(codec)
    s._recapture = recapture
    return s


def ask(s, profiles):
    LOOP.run_until_complete(s.check_profile(Guest(), profiles))


import logging
handler = logging.Handler()
handler.emit = lambda record: said.append(record.getMessage())
logging.getLogger("fourthplayer.session").addHandler(handler)

# The case that started it: a host on Main, a browser that takes Constrained
# Baseline and High. It used to name the fault and leave the guest looking at
# a black screen until somebody edited a file.
s = session_for("main")
ask(s, ["42e01f", "640c1f"])
check(s.cfg.h264_profile == "constrained-baseline",
      "the session moves to the profile every browser decodes")
check(recaptured == ["h264"],
      "and recaptures, since the profile is pinned on the encoder: %r"
      % (recaptured,))
check(said and "constrained-baseline" in said[0],
      "the log says what it did and why: %s" % (said[0][:110] if said else ""))
check(not told,
      "and the guest is told nothing, because nothing is wrong for them now")

print("\nit only ever moves one way, so it cannot oscillate")
said.clear(); told.clear(); recaptured.clear()
s = session_for("constrained-baseline")
ask(s, ["42e01f"])
check(not recaptured and not said,
      "a guest who takes what is already being sent changes nothing")

said.clear(); told.clear(); recaptured.clear()
s = session_for("main")
ask(s, ["4d0028", "42e01f"])
check(not recaptured and not said,
      "and neither does one who takes the profile already chosen")

print("\na browser that takes nothing this host can send is told so")
said.clear(); told.clear(); recaptured.clear()
s = session_for("main")
ask(s, ["640c1f"])              # High only: no baseline to fall back to
check(not recaptured,
      "there is no profile to move to, so the room is not interrupted for "
      "nothing")
check(said and "nothing to move to" in said[0],
      "the log says why it did not: %s" % (said[0][:110] if said else ""))
check(told and told[0]["t"] == "note",
      "and the guest is told, since they are the one looking at the black "
      "screen")

print("\nand the checks that were always here still hold")
said.clear(); told.clear(); recaptured.clear()
s = session_for("main")
ask(s, [])
check(not said and not recaptured, "a browser that says nothing is not guessed at")

said.clear(); told.clear(); recaptured.clear()
s = session_for("main")
s.codec = "h265"
s.stage = type("S", (), {"codec": "h265"})()
ask(s, ["42e01f"])
check(not said and not recaptured,
      "H.264 profiles are not checked when H.265 is what is going out")

print("\nand it does not fall over with no capture running")
said.clear(); told.clear(); recaptured.clear()
s = session_for("main", stage=False)
ask(s, ["42e01f"])
check(s.cfg.h264_profile == "constrained-baseline" and not recaptured,
      "the choice is remembered for the next capture, and none is restarted")

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
