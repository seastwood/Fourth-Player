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

print("\na guest who cannot take it is named in the log")
said = []
told = []


class Cfg:
    h264_profile = "main"
    height = 1080
    path = "/home/x/.config/fourth-player/config.json"


class Guest:
    label = "Seth"


session = LiveSession.__new__(LiveSession)
session.cfg = Cfg()
session.codec = "h264"
session.stage = None
session.notify_one = lambda guest, message: told.append(message)

import logging
handler = logging.Handler()
handler.emit = lambda record: said.append(record.getMessage())
logging.getLogger("fourthplayer.session").addHandler(handler)

session.check_profile(Guest(), ["42e01f", "42001f"])
check(said, "a browser that lists only baseline profiles is reported")
check(said and "constrained-baseline" in said[0],
      "and told what to change: %s" % (said[0][:120] if said else ""))
check(told and told[0]["t"] == "note",
      "the guest is told too, since they are the one looking at the black screen")

said.clear(); told.clear()
session.check_profile(Guest(), ["4d0028", "42e01f"])
check(not said, "a browser that lists the offered profile is not complained about")
check(not told, "and hears nothing")

said.clear()
session.check_profile(Guest(), [])
check(not said, "a browser that says nothing is not guessed at")

said.clear()
session.codec = "h265"
session.check_profile(Guest(), ["42e01f"])
check(not said, "and H.264 profiles are not checked when H.265 is what is going out")

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
