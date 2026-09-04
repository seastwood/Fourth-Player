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
    import fourthplayer.session as S
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

    def forget(self, sender=None):
        # VirtualPad drops one sender's share here; a stand-in has only ever
        # had the one sender, so letting go of everything is the same thing.
        self.release_all()

    def adopt_new_sender(self, sender=None):
        self.adopted += 1


class FakePeer:
    def __init__(self, peer_id):
        self.id = peer_id
        self.on_input = None
        self.on_dead = None
        self.on_broken = None
        self.ice_ok = True          # a fresh peer is assumed to be working
        self.sent = {"video_bytes": 0, "audio_bytes": 0, "video_packets": 0}
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

    def add_peer(self, peer_id, on_signal, configure=None, media=True):
        self.made += 1
        # Recorded: whether a guest was given the picture is now a property of
        # the guest, and a second controller on somebody's sofa must not be
        # handed a second copy of the encode.
        self.media = media
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


class FakePads(list):
    """A stand-in for PadSet: seats with names, whose devices come and go.

    Modelled on the real thing rather than on a plain list, because the real
    thing stopped being one: an empty seat has no device, so reading a name
    must not conjure one, and letting a seat go has to be something a caller
    can do.
    """

    def __init__(self, pads):
        super().__init__(pads)
        self.released = []

    @property
    def names(self):
        return [p.name for p in self]

    def name_for(self, index):
        return self[index].name

    def release(self, index):
        self.released.append(index)
        return True


def session_with_guest(now=None):
    """A session with one guest in slot 0.

    `now` is installed before the guest is built, not after. The guest reads
    the session's clock when it records when it last had media, and the reaper
    compares against that same clock -- so a fixture that swapped the clock in
    afterwards left the two reading different sources, and whether the test
    passed then depended on how long the machine had been switched on. It held
    for three days and failed within the hour after a reboot.
    """
    loop = asyncio.new_event_loop()
    session = LiveSession(Config(), loop)
    if now is not None:
        session._now = now
    session.stage = FakeStage()
    session.pads = FakePads([FakePad("p1"), FakePad("p2"), FakePad("p3")])
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

print("\na peer that stopped working does not count as a connection")
session, guest, loop = session_with_guest()
peer = attach(session, guest)
guest.socket = object()          # signalling up, no input yet: freshly joined
check(guest.has_media(), "a peer whose ICE is up counts")
peer.ice_ok = False
check(not guest.has_media(),
      "one whose ICE has gone quiet does not -- this is the network-switch case")
check(session.roster()[0]["connected"] is False,
      "and the roster says so rather than claiming they are playing")

print("\nand such a guest is reaped, object or no object")
clock = [500.0]
session._now = lambda: clock[0]
guest.media_since = clock[0]
clock[0] += 20
check(session.reap_now(seconds=10) == 1,
      "the impatient sweep frees a slot held by a dead connection")
check(0 not in session.guests, "and the guest is gone")

print("\nbut it never takes a slot from somebody who is playing")
session, guest, loop = session_with_guest(lambda: clock[0])
attach(session, guest)
# Somebody playing is heard from constantly: their browser sends its pad state
# every 50 ms whether or not anything moved. Timestamps must come from the same
# clock the session reads -- comparing a fake clock against time.monotonic()
# makes the result depend on how long the machine has been up, which is how
# this suite came to pass and fail on identical code.
clock[0] += 10000
guest.last_input = clock[0]
check(session.reap_now(seconds=1) == 0,
      "a working connection is left alone however long it has been")
check(session.guests.get(0) is guest, "they keep their slot")

print("\nand silence is what ends it, not the clock")
guest.socket = None
guest.last_input = clock[0] - (S.SILENCE_SECONDS + 1)
check(not guest.has_media(clock[0]),
      "a guest heard from %.0fs ago is not connected" % (S.SILENCE_SECONDS + 1))
guest.last_input = clock[0]
check(guest.has_media(clock[0]), "and one heard from just now is")

print("\na guest with no video does not keep a slot for ever")
clock = [1000.0]
session, guest, loop = session_with_guest(lambda: clock[0])
session.invite = None            # the sweeper checks this; reap directly instead
peer = attach(session, guest)
check(session.guests.get(0) is guest, "the guest holds slot 0")

clock[0] += S.GHOST_SECONDS + 10
# Still sending pad state, which is what having media means: a browser sends
# it every 50 ms whether or not anything moved, so silence is the only signal
# that works. Saying "their peer exists" is not enough and has not been since
# a guest who vanished was found holding a slot behind an ICE state that said
# `completed` for ever.
guest.last_input = clock[0]
session._reap_ghosts()
check(session.guests.get(0) is guest,
      "a guest whose media is up is never reaped, however long it has been")

session.detach_peer(guest, background=False)
guest.socket = object()          # signalling still up: they are reconnecting
guest.media_since = clock[0]
clock[0] += S.GHOST_SECONDS - 5
session._reap_ghosts()
check(session.guests.get(0) is guest,
      "and one that just lost it is given time to come back")

clock[0] += 10
session._reap_ghosts()
check(0 not in session.guests,
      "but a slot held with no video is eventually freed")

print("\na guest whose socket has gone too is not waited for as long")
session, guest, loop = session_with_guest(lambda: clock[0])
guest.socket = None              # tab closed, or out of range
guest.media_since = clock[0]
clock[0] += S.LEFT_SECONDS + 2
session._reap_ghosts()
check(0 not in session.guests,
      "somebody who left frees their slot in %.0fs, not %.0f"
      % (S.LEFT_SECONDS, S.GHOST_SECONDS))

print("\nand a freed slot is given back to the invite, not just forgotten")
session, guest, loop = session_with_guest()
import fourthplayer.invites as INV
session.invite = INV.Session(slots=3, duration=600, now=0)
tok, pin = session.invite.clear_invite
slot, _ = session.invite.join(tok, pin, now=1, address="a")
session.guests[slot] = guest
guest.slot = slot
check(session.invite.free_slot() == 1, "one slot is spoken for")
session.drop(slot, reason="left")
check(session.invite.free_slot() == slot,
      "dropping a guest frees the slot in the invite as well -- the session "
      "list and the invite are two books and both have to be written")

print("\nand the freed slot can be taken again")
check(session.invite is None or session.invite.free_slot() == 0,
      "the slot is available")

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
