"""Session bookkeeping: whose peer is whose, and what a death means.

The interesting cases here are all races that a live test would only hit
sometimes, so the stage and the pads are fakes and the timing is explicit.
"""
import asyncio
import concurrent.futures
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from fourthplayer.config import Config
    from fourthplayer.session import LiveSession, GuestConnection
except ImportError as exc:
    print("SKIPPED: %s -- session imports the pad layer, which needs"
          " python3-evdev. This suite runs on the host machine." % exc)
    sys.exit(0)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


class FakePad:
    def __init__(self, name="pad"):
        self.name = name
        self.path = "/dev/input/eventX"
        self.released = 0
        self.adopted = 0

    def release_all(self):
        self.released += 1

    def adopt_new_sender(self):
        self.adopted += 1


class FakePeer:
    def __init__(self, peer_id):
        self.id = peer_id
        self.on_input = None
        self.on_dead = None
        self.detached = 0

    def detach(self):
        self.detached += 1


class Inline:
    """Stands in for the pipeline-mutation worker, running everything here.

    It must return a real Future: asyncio's run_in_executor insists on one.
    """

    def submit(self, fn, *args, **kwargs):
        future = concurrent.futures.Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:       # noqa: BLE001 - mirror the executor
            future.set_exception(exc)
        return future


class FakeStage:
    def __init__(self):
        self.peers = {}
        self.made = 0
        self.mutations = Inline()

    def add_peer(self, peer_id, on_signal, configure=None):
        self.made += 1
        peer = FakePeer(peer_id)
        if configure is not None:
            configure(peer)
        self.peers[peer_id] = peer
        return peer

    def take_peer(self, peer_id):
        return self.peers.pop(peer_id, None)


def attach(session, guest):
    """attach_peer is a coroutine now; the tests are not."""
    return session.loop.run_until_complete(
        session.attach_peer(guest, lambda *a: None))


def session_with_guest():
    loop = asyncio.new_event_loop()
    session = LiveSession(Config(), loop)
    session.stage = FakeStage()
    session.pads = [FakePad("p1"), FakePad("p2"), FakePad("p3")]
    guest = GuestConnection(session, 0, socket=None)
    session.guests[0] = guest
    return session, guest, loop


print("a peer that dies while it is still the current one ends the guest")
session, guest, loop = session_with_guest()
peer = attach(session, guest)
check(guest.peer is peer, "the guest holds the peer it was given")
peer.on_dead("media connection failed")
check(0 not in session.guests, "the guest is dropped when their own peer dies")

print("\nbut a replaced peer's death must not touch the guest that replaced it")
session, guest, loop = session_with_guest()
old = attach(session, guest)
old_death = old.on_dead
# The guest reloads: a resume detaches the old peer and attaches a new one.
session.detach_peer(guest, background=False)
new = attach(session, guest)
check(guest.peer is new and new is not old, "the guest now holds a different peer")
check(old.detached == 1, "the old peer was torn down")
check(old.on_dead is None,
      "and had its death callback cleared, so teardown cannot fire it")

# The old peer's ICE failure was already queued before the swap, and arrives now.
old_death("media connection failed")
check(session.guests.get(0) is guest,
      "the late death of the old peer does NOT drop the new connection")
check(guest.peer is new, "and the new peer is untouched")

print("\nnor may it drop somebody else who has taken the slot since")
session, guest, loop = session_with_guest()
old = attach(session, guest)
old_death = old.on_dead
session.drop(0, reason="left")
newcomer = GuestConnection(session, 0, socket=None)
session.guests[0] = newcomer
attach(session, newcomer)
old_death("media connection closed")
check(session.guests.get(0) is newcomer,
      "a stranger in the same slot survives the previous guest's death")

print("\nattaching a peer tells the pad it has a new sender")
session, guest, loop = session_with_guest()
before = session.pads[0].adopted
attach(session, guest)
check(session.pads[0].adopted == before + 1,
      "the pad forgets the old sequence numbers on attach")

print("\ndetaching releases the pad and frees the peer id at once")
session, guest, loop = session_with_guest()
peer = attach(session, guest)
check(peer.id in session.stage.peers, "the peer is registered while attached")
session.detach_peer(guest, background=False)
check(peer.id not in session.stage.peers,
      "and unregistered immediately, so a replacement can reuse the id")
check(session.pads[0].released > 0, "the pad is released")
attach(session, guest)
check(session.stage.made == 2, "a replacement peer can take the same slot")

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
