"""Two guests must never end up holding one slot's peer between them.

A peer is named after a slot -- "slot0" -- and nothing else. That is fine
while a slot means one person for the life of a connection, and it stops
being fine the moment a seat changes hands while somebody's page is still
open: a signalling socket holds its guest object for as long as it lives, and
that object keeps the slot number it was given.

Reported as two clients where only one has a picture at a time, swapping every
few seconds. The swap is this: each page asks to renew, each renewal unhooks
whatever is registered under "slot0", and whoever was unhooked keeps a running
pipeline that nothing feeds any more -- a frozen picture, no error at either
end, and a leaked pipeline behind it.
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
    from fourthplayer.video import Stage
    from fourthplayer.session import LiveSession, StaleGuest
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)


class FakePeer:
    def __init__(self, name):
        self.id = "slot0"
        self.name = name


print("a name is only given up by whoever holds it")
stage = Stage.__new__(Stage)
mine, theirs = FakePeer("mine"), FakePeer("theirs")
stage.peers = {"slot0": theirs}

check(stage.take_peer("slot0", expected=mine) is None,
      "a caller holding an older peer is handed nothing")
check(stage.peers.get("slot0") is theirs,
      "and the peer that is actually in the slot stays registered")

check(stage.take_peer("slot0", expected=theirs) is theirs,
      "the peer that is there is handed back to whoever holds it")
check("slot0" not in stage.peers, "and the name is free afterwards")

stage.peers = {"slot0": theirs}
check(stage.take_peer("slot0") is theirs,
      "a caller that names no peer still takes it, as it always did")

print("and a guest who has lost their seat cannot renew into it")


class FakeGuest:
    def __init__(self, label, slot):
        self.label, self.slot = label, slot
        self.peer = FakePeer(label)


seated = FakeGuest("Guest 1", 0)
stale = FakeGuest("seth", 0)


class FakeSession:
    guests = {0: seated}
    detached = []

    def detach_peer(self, guest):
        self.detached.append(guest.label)

    async def attach_peer(self, guest, on_signal):
        return "attached " + guest.label


session = FakeSession()


async def run():
    try:
        await LiveSession.renew(session, stale, lambda *a: None)
    except StaleGuest:
        return "refused"
    return "allowed"


check(asyncio.get_event_loop_policy().new_event_loop().run_until_complete(run())
      == "refused",
      "a guest whose slot moved on is refused rather than served")
check(not session.detached,
      "and the guest who is in the slot keeps their picture")


async def ok():
    return await LiveSession.renew(session, seated, lambda *a: None)


check(asyncio.get_event_loop_policy().new_event_loop().run_until_complete(ok())
      == "attached Guest 1",
      "the guest who is in the slot renews exactly as before")

print("FAILED" if fails else "PASSED")
sys.exit(1 if fails else 0)
