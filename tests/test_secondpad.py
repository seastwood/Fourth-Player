"""A second controller on the same machine, with no second picture.

Two people on one sofa share one screen. Today the only way for the second of
them to get a seat is a second browser tab, which works -- the host issues a
fresh token on every use of the PIN and takes any free slot, so nothing binds
a link to one seat -- but it makes the host encode and send the same screen
twice down the same wire, for one monitor.

So a guest may say `input: "only"`: they get a seat, a pad, a name and a row
in the list exactly like anybody else, and their peer carries the input
channel and no media. Everything else about them is ordinary, which is the
point -- the seat model already understands one guest, one pad, and did not
have to learn anything new.

Presence is the reason this works at all. A guest is judged present by their
pad frames arriving, not by video, so a guest with no video is not a guest who
looks absent.
"""
import asyncio
import os
import re
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
    from fourthplayer.config import Config
    from fourthplayer.session import LiveSession, GuestConnection
except ModuleNotFoundError as exc:
    print("SKIPPED the host half: %s" % exc)
    Config = None


class Inline:
    """run_in_executor, without a thread."""

    def submit(self, fn, *a, **kw):
        raise AssertionError("not used")


class FakePeer:
    def __init__(self, peer_id):
        self.id = peer_id
        self.ice_ok = True
        self.sent = {}
        self.on_input = self.on_dead = self.on_broken = None

    def detach(self):
        pass


class FakeStage:
    """Records whether each peer was asked for the picture."""

    def __init__(self):
        self.peers = {}
        self.asked = []
        self.mutations = None

    def add_peer(self, peer_id, on_signal, configure=None, media=True):
        self.asked.append((peer_id, media))
        peer = FakePeer(peer_id)
        if configure is not None:
            configure(peer)
        self.peers[peer_id] = peer
        return peer

    def take_peer(self, peer_id):
        return self.peers.pop(peer_id, None)


class FakePad:
    """One seat. Named like the real ones, because which controller a guest
    is on is a thing the list reports and a thing somebody reads off it."""

    def __init__(self, number):
        self.name = "Fourth Player %d" % number
        self.path = "/dev/input/event%d" % number

    def release_all(self):
        pass

    def forget(self, sender=None):
        pass

    def adopt_new_sender(self, sender=None):
        pass


class FakePads(list):
    def __init__(self, pads):
        super().__init__(pads)
        self.names = [p.name for p in pads]

    def name_for(self, index):
        return self[index].name

    def release(self, index):
        return True


if Config is not None:
    loop = asyncio.new_event_loop()
    session = LiveSession(Config(), loop)
    session.stage = FakeStage()
    session.pads = FakePads([FakePad(1), FakePad(2), FakePad(3)])

    def a_guest(slot, input_only=False):
        guest = GuestConnection(session, slot, socket=None)
        guest.label = "Guest %d" % (slot + 1)
        guest.input_only = input_only
        session.guests[slot] = guest
        return guest

    print("the guest who brought a screen, and the one who did not")
    watching = a_guest(0)
    listening = a_guest(1, input_only=True)
    loop.run_until_complete(session.attach_peer(watching, lambda *a: None))
    loop.run_until_complete(session.attach_peer(listening, lambda *a: None))
    asked = dict((peer_id, media) for peer_id, media in session.stage.asked)
    check(asked.get("slot0") is True, "the first is given the picture")
    check(asked.get("slot1") is False,
          "and the second is not -- one screen, one encode, however many "
          "controllers are around it")

    print("\nand in every other way the second one is an ordinary guest")
    check(listening.pad_index == 1, "it has a seat of its own")
    check(listening.slot == 1 and listening.label == "Guest 2",
          "and a name of its own")
    rows = session.people()
    check(len(rows) == 2, "both appear in the list everybody sees")
    row = [r for r in rows if r["slot"] == 1][0]
    check(row.get("input_only") is True,
          "marked as having no picture, so the row is not read as a guest "
          "whose video has failed")
    check(row["pad_name"] == "Fourth Player 2", "on its own controller")

    print("\nand presence does not depend on video")
    listening.peer.ice_ok = True
    listening.last_input = 0
    listening.socket = object()
    check(listening.has_media() is True,
          "a guest with no picture and an open socket is present -- presence "
          "is pad frames arriving, which is exactly what this guest sends")
    loop.close()

print("\nthe wiring, read from the source")
video = open(os.path.join(ROOT, "fourthplayer", "video.py")).read()
attach = video[video.index("def attach(self):"):]
attach = attach[:attach.index("create-data-channel")]
check("if self.media:" in attach,
      "a peer only feeds itself video when it is meant to have some")
check(attach.index("if self.media:") < attach.index('self._feed("video"'),
      "and the check is in front of the feed, not after it")

server = open(os.path.join(ROOT, "fourthplayer", "server.py")).read()
check('message.get("input", "")' in server,
      "the page can ask for a controller-only connection")
check("or bool(guest.input_only)" in server,
      "and it sticks across a resume -- a second controller that reconnects "
      "is still a second controller")
refusal = re.search(r"if \(not guest\.input_only\s*\n\s*and re\.search\(r\"\^m=video 0", server)
check(bool(refusal),
      "and the rule that frees a slot when a browser refuses the video does "
      "not fire for a guest who was never offered any")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_secondpad: all ok")
