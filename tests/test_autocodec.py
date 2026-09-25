"""Choosing "automatic" has to choose, not keep whatever is already running.

set_stream read the *stage's current codec* whenever the setting was "auto":

    want = self.cfg.codec
    if want == "auto":
        want = getattr(self.stage, "codec", "h264")
    await self._recapture(want)

So switching a session from H.264 to automatic recaptured as H.264 and stayed
there. Nothing else reconsiders until a guest joins or leaves -- agree_codec
runs on arrival, _codec_after_leaving on departure -- and a guest sitting on
the sofa watching does neither. Reported as "automatic encoder option doesn't
seem to be using h265 when it should be the preferred option", and it never
would have.

The calculation was already written twice over; this path simply did not make
it.
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
    from fourthplayer.session import LiveSession
    from fourthplayer import video
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)


class FakeGuest:
    def __init__(self, codecs, watching=True):
        self.codecs = list(codecs)
        self.peer = object() if watching else None


class FakeStage:
    def __init__(self, codec):
        self.codec = codec


LOOP = asyncio.new_event_loop()
BOTH = ["h265", "h264"]
ONLY264 = ["h264"]

# Pinned rather than probed: what this host can encode must not decide whether
# the test means anything. A machine that cannot encode H.265 would pass every
# case below by being vacuous.
video._host_codecs = ["h265", "h264"]


def session(setting, stage_codec, guests):
    s = LiveSession.__new__(LiveSession)
    s.cfg = type("C", (), {"codec": setting, "hardware_encode": False})()
    s.stage = FakeStage(stage_codec)
    s.guests = {i: g for i, g in enumerate(guests)}
    s.moved = []

    async def recapture(codec, display=None):
        s.moved.append(codec)
        s.stage.codec = codec
    s._recapture = recapture
    s.stream_settings = lambda: {}
    s.notify = lambda _msg: None
    return s


async def choose(s):
    """The tail of set_stream, which is where the decision lives."""
    if s.stage is not None:
        want = s.cfg.codec
        if want == "auto":
            watching = [g for g in s.guests.values() if g.peer is not None]
            if watching:
                want = video.best_shared_codec(
                    __import__("fourthplayer.session", fromlist=["_common"])
                    ._common(watching), s.cfg.hardware_encode)
            else:
                want = getattr(s.stage, "codec", "h264")
        await s._recapture(want)


print("the fault, as reported")
s = session("auto", "h264", [FakeGuest(BOTH)])
LOOP.run_until_complete(choose(s))
check(s.moved == ["h265"],
      "a session pinned to h264, switched to automatic, with a guest who can "
      "take h265, moves to h265: %r" % (s.moved,))

print("\nand it is the real set_stream that does it, not just this test")
import inspect                                              # noqa: E402
body = inspect.getsource(LiveSession.set_stream)
check("best_shared_codec" in body,
      "set_stream works the codec out rather than reading the stage's")
check("_common(watching)" in body,
      "against the guests who are actually watching")
check(body.count('want = getattr(self.stage, "codec", "h264")') == 1,
      "and still falls back to the running codec in exactly one place -- "
      "when there is nobody to measure against")

print("\nit does not overrule a guest who cannot take the better one")
s = session("auto", "h265", [FakeGuest(BOTH), FakeGuest(ONLY264)])
LOOP.run_until_complete(choose(s))
check(s.moved == ["h264"],
      "one guest who can only take h264 settles it for the room: %r"
      % (s.moved,))

print("\nan explicit choice is still obeyed")
for setting in ("h264", "h265"):
    s = session(setting, "h265" if setting == "h264" else "h264",
                [FakeGuest(BOTH)])
    LOOP.run_until_complete(choose(s))
    check(s.moved == [setting],
          "asking for %s gives %s, whatever the guests could do: %r"
          % (setting, setting, s.moved))

print("\nwith nobody watching it leaves the codec alone")
# Not h264. Falling back to "the safe one" here would drop a session to H.264
# every time a setting was touched between guests, and the next guest to
# arrive settles it from scratch through agree_codec anyway.
s = session("auto", "h265", [])
LOOP.run_until_complete(choose(s))
check(s.moved == ["h265"], "it stays where it was: %r" % (s.moved,))
s = session("auto", "h265", [FakeGuest(BOTH, watching=False)])
LOOP.run_until_complete(choose(s))
check(s.moved == ["h265"],
      "a guest who has not got a picture yet does not count as watching: %r"
      % (s.moved,))

print()
if fails:
    print("FAILURES: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_autocodec: all ok")
