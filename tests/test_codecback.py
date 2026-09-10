"""Going back up to the better codec when the reason to be down has left.

The downgrade and the upgrade are deliberately not symmetric at the moment a
guest *arrives*: dropping keeps somebody from being turned away, and rising
would interrupt people who are watching happily.

That asymmetry is wrong for ever afterwards. One browser that could only take
H.264 pinned a whole session to it and it stayed pinned long after that
browser had gone, so everybody left behind watched the worse picture at twice
the bitrate for as long as the session lasted.
"""
import asyncio
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
    from fourthplayer.session import LiveSession, _common, CODEC_RANK
    from fourthplayer import video
    from fourthplayer.video import best_shared_codec
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)


class FakeGuest:
    def __init__(self, codecs):
        self.codecs = list(codecs)
        self.peer = object()          # watching


class FakeStage:
    def __init__(self, codec):
        self.codec = codec


LOOP = asyncio.new_event_loop()


def session(stage_codec, guests):
    s = LiveSession.__new__(LiveSession)
    s.cfg = type("C", (), {"codec": "auto", "hardware_encode": False})()
    s.stage = FakeStage(stage_codec)
    # `open` is derived from the invite rather than set, so give it one that
    # is alive rather than trying to assign to a property.
    s.invite = type("I", (), {"alive": lambda self, now: True})()
    s._now = lambda: 0.0
    s.guests = {i: g for i, g in enumerate(guests)}
    s.moved = []
    async def recapture(codec, display=None):
        s.moved.append(codec)
        s.stage.codec = codec
    s._recapture = recapture
    return s


BOTH = ["h265", "h264"]
ONLY264 = ["h264"]

# What this host can encode is pinned rather than probed. The question below is
# whether a session climbs back up once the guest holding it down has left, and
# the answer must not depend on the card in whichever machine runs the test: an
# Intel HD 530 decodes H.265 and cannot encode it, so there the host offers
# H.264 alone and every case here passes by being vacuous.
video._host_codecs = ["h265", "h264"]

print("the shape of the fault")
s = session("h264", [FakeGuest(BOTH), FakeGuest(BOTH)])
LOOP.run_until_complete(s._codec_after_leaving())
check(s.moved == ["h265"],
      "everybody left can take h265, so it moves up: %r" % (s.moved,))

print("\nand it stays put when it should")
s = session("h264", [FakeGuest(BOTH), FakeGuest(ONLY264)])
LOOP.run_until_complete(s._codec_after_leaving())
check(s.moved == [],
      "somebody left still cannot take h265: %r" % (s.moved,))

s = session("h265", [FakeGuest(BOTH)])
LOOP.run_until_complete(s._codec_after_leaving())
check(s.moved == [], "already on the best one, so nothing happens")

s = session("h264", [])
LOOP.run_until_complete(s._codec_after_leaving())
check(s.moved == [],
      "nobody is watching, so there is nobody to move up for -- the next "
      "guest settles it from scratch anyway")

print("\nit never moves *down* here")
# A guest leaving can only ever widen what is shared, so this is belt and
# braces: the guard is explicit rather than relying on that being true.
s = session("h265", [FakeGuest(ONLY264)])
LOOP.run_until_complete(s._codec_after_leaving())
check(s.moved == [],
      "downgrading is agree_codec's job, at the moment somebody arrives")

print("\nand it is off when the codec was chosen by hand")
s = session("h264", [FakeGuest(BOTH)])
s.cfg = type("C", (), {"codec": "h264", "hardware_encode": False})()
LOOP.run_until_complete(s._codec_after_leaving())
check(s.moved == [], "a codec set in the config is not second-guessed")

print("\nand a host that cannot encode the better codec stays put")
# The other half of pinning it: on a host offering H.264 alone, two guests who
# both decode H.265 are still watching H.264, because nothing here can make it.
video._host_codecs = ["h264"]
s = session("h264", [FakeGuest(BOTH), FakeGuest(BOTH)])
LOOP.run_until_complete(s._codec_after_leaving())
check(s.moved == [],
      "both guests take h265 and the host cannot encode it: %r" % (s.moved,))
video._host_codecs = ["h265", "h264"]

print("\nleaving is what asks the question")
source = open(os.path.join(ROOT, "fourthplayer", "session.py"),
              encoding="utf-8").read()
drop = source.split("    def drop(self, slot")[1].split("\n    def ")[0]
check("_codec_after_leaving" in drop,
      "drop() asks it, so a guest going is what reconsiders the encoding")

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
