"""What a logged-in account may do, and what the host does about it.

The rule this whole suite exists to hold down: the page decides what to draw,
and the host decides what happens. Every check below goes at the host, with
the page's opinion left out of it entirely -- a guest who edits their own
JavaScript is the case this has to survive, and that guest sends whatever they
like.

The other rule is that capabilities only ever add, with one deliberate
exception. `stop` lets somebody end a game the launch policy would have
refused; it never stops somebody who could already. Steam is the exception,
and it is the point of the exercise: a Steam game is a desktop application
signed into somebody's account, so it takes a capability that nobody has by
default.
"""
import asyncio
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


try:
    from fourthplayer import accounts, server as serverlib
    from fourthplayer.session import LiveSession, GuestConnection
except Exception as exc:
    print("SKIPPED: this machine cannot import the host (%s)" % exc)
    sys.exit(0)

folder = tempfile.mkdtemp(prefix="fp-capable-")
accounts.STORE = os.path.join(folder, "accounts.json")
accounts.SCRYPT_N = 2 ** 8

BROFORCE = "274190"
DELTARUNE = "1671210"


class FakeGuest(GuestConnection):
    def __init__(self, slot=1, label="guest", can=(), fresh=True):
        self.slot = slot
        self.label = label
        self.session = None
        self.held_frames = 0
        self.frames = 0
        self.bad_frames = 0
        if can:
            self.account = "someone"
            self.capabilities = tuple(can)
            self.logged_in_at = 100.0 if fresh else 0.0


class FakeCatalogue:
    ROWS = [
        {"id": "a", "label": "Micro Mages", "system": "nes", "short": "NES",
         "players": "1-4", "kind": "rom", "path": "/roms/mm.nes"},
        {"id": "b", "label": "Broforce", "system": "steam", "short": "Steam",
         "players": "1-4", "kind": "steam", "appid": BROFORCE},
        {"id": "c", "label": "DELTARUNE", "system": "steam", "short": "Steam",
         "players": "1", "kind": "steam", "appid": DELTARUNE},
    ]

    def rows(self):
        return list(self.ROWS)

    def listing(self):
        """What the page gets -- and deliberately less than rows().

        The real one drops kind, appid, path and core: the page has no use for
        them and no business with them. A fake that kept them hid a bug where
        the Steam filter asked a listed row whether it was a Steam game, got
        None, and let every one of them through.
        """
        return [{k: v for k, v in r.items()
                 if k in ("id", "label", "system", "short", "players")}
                for r in self.ROWS]

    def find(self, game_id):
        return next((dict(r) for r in self.ROWS if r["id"] == game_id), None)


LOOP = asyncio.new_event_loop()


def drain(queue):
    """Empty an outbox, so one check never reads the reply to the last."""
    while not queue.empty():
        queue.get_nowait()


def make_session():
    from fourthplayer.config import Config
    loop = LOOP
    session = LiveSession(Config(), loop, now=lambda: 1000.0)
    session.guests = {}
    session.catalogue = FakeCatalogue()
    session.slots = 4
    return session, loop


print("Steam games are hidden, not refused")
session, loop = make_session()
stranger = FakeGuest()
check([r["id"] for r in session.listing_for(stranger)] == ["a"],
      "somebody with no account sees the emulator game and neither Steam one")
one = FakeGuest(can=["steam:" + BROFORCE])
check([r["id"] for r in session.listing_for(one)] == ["a", "b"],
      "a per-game grant shows that game and no other")
allsteam = FakeGuest(can=["steam"])
check([r["id"] for r in session.listing_for(allsteam)] == ["a", "b", "c"],
      "plain steam shows all of them")
check([r["id"] for r in session.listing_for(FakeGuest(can=["kick"]))] == ["a"],
      "an account with other powers is not thereby given Steam")

print("\nand the host refuses them too, not just the list")
session.launch_policy = "open"
result = loop.run_until_complete(session.request_launch(stranger, "b"))
check(not result["ok"], "naming a hidden game's id anyway is refused")
check(result["error"] == "That game is not on this box.",
      "in the same words as an id that does not exist: %r" % result["error"])
missing = loop.run_until_complete(session.request_launch(stranger, "nonsense"))
check(missing["error"] == result["error"],
      "so the refusal says nothing about whether the game is there")
result = loop.run_until_complete(session.request_launch(one, "c"))
check(result["error"] == "That game is not on this box.",
      "a grant for one game does not open another")

print("\nan emulator game is nobody's to gate")
for who in (stranger, one, allsteam):
    check(session.may_start(who, session.catalogue.find("a")),
          "%r may start the ROM" % (who.capabilities,))

print("\nwhile a Steam game is in front, controllers ask the capability")
session, loop = make_session()
session.steam_here(BROFORCE, "Broforce")
playing = FakeGuest(2, "playing", can=["steam:" + BROFORCE])
watching = FakeGuest(3, "watching")
check(not session.holding(playing)[0], "the guest who was given it may drive")
check(session.holding(watching)[0], "the guest who was not may not")
check("Broforce" in session.holding(watching)[1],
      "and is told which game: %r" % session.holding(watching)[1])
check("not been given" in session.holding(watching)[1], "and why")
check(session.hold_state(watching)["held"] is True, "their page is told so")
check(session.hold_state(playing)["held"] is False, "and the other is not")

print("\nbeing handed the screen is not being handed Steam")
session.driver = watching.slot
check(session.holding(watching)[0],
      "the named driver is still held on a Steam game they were not given")
check(session.hold_state(watching)["driving"] is False,
      "and is not told they are driving it")

print("\nan emulator game holds nobody")
session.steam_here("")
session.driver = None
check(not session.holding(watching)[0],
      "once Steam is gone the ordinary rules are back")
session.input_held = True
check(session.holding(watching)[0], "and the menu hold still works")
session.driver = watching.slot
check(not session.holding(watching)[0], "including the driver exemption")

print("\nthe frames really do stop")
session, loop = make_session()
session.steam_here(BROFORCE, "Broforce")
watching = FakeGuest(3, "watching")
watching.session = session
sent = []


class Pad:
    def apply(self, state, sender=None):
        sent.append(sender)


watching.pad_index = 0
# The real frame, through the real decoder, into the real feed(): the whole
# point is that nothing on the page is what stops it.
from fourthplayer import protocol
GuestConnection.pad = property(lambda self: Pad())
frame = protocol.encode(protocol.PadState(seq=1, buttons=0b101))
watching.feed(frame)
check(sent == [] and watching.held_frames == 1,
      "a guest without the capability gets nothing through to the pad")
allowed = FakeGuest(2, "allowed", can=["steam"])
allowed.session = session
allowed.feed(frame)
check(sent == [2], "and one with it does")
check(watching.frames == 1,
      "the held guest still counts as present -- being held is not silence")

print("\nstop only ever adds")
# Nothing here may touch a real process. This suite runs on the console
# itself, where "end the game" means ending somebody's game -- so the launcher
# is answered by hand and the stopping is replaced outright. A test that can
# turn off the television is not a test anybody will run twice.
import fourthplayer.launcher as launcher
launcher.running = lambda: True
stopped = []


async def pretend_stop():
    stopped.append(True)
    return {"ok": True, "state": "stopped"}


session, loop = make_session()
session.launch_policy = "off"
session._stop_game = pretend_stop
session.playing_now = lambda: dict(session.catalogue.ROWS[0])
result = loop.run_until_complete(session.request_stop(FakeGuest()))
check(not result["ok"] and "not turned on" in result["error"],
      "with the policy off, an ordinary guest still cannot end a game")
check(not stopped, "and nothing was stopped")
result = loop.run_until_complete(session.request_stop(FakeGuest(can=["stop"])))
check(result.get("ok") and stopped,
      "an account given stop gets past the policy: %r" % result)

# `approve` puts it to the owner; the capability says do it now.
session, loop = make_session()
session.launch_policy = "approve"
session._stop_game = pretend_stop
session.pending = None
session.notify = lambda message: None
result = loop.run_until_complete(session.request_stop(FakeGuest()))
check(result.get("state") == "pending",
      "under approve an ordinary guest asks the owner: %r" % result)
session.pending = None
stopped.clear()
result = loop.run_until_complete(session.request_stop(FakeGuest(can=["stop"])))
check(result.get("ok") and stopped, "and an account given stop does not have to")

print("\nthe connection limit")
session, loop = make_session()
check(session.limit() == 4, "it starts at the slots the session has")
check(session.set_limit(2) == 2, "it can be lowered")
check(session.limit() == 2, "and stays lowered")
check(session.set_limit(0) == 1, "never below one, whatever is asked")
check(session.set_limit(99) == 4, "never above the slots that exist")
session.guests = {0: FakeGuest(0, "a"), 1: FakeGuest(1, "b")}
allowed, why = session.may_join(None)
check(allowed, "two of four may still be joined by a third")
session.set_limit(2)
allowed, why = session.may_join(None)
check(not allowed and "slot" in why, "at the limit, the next is refused")
check(session.may_join({"name": "seth"})[0],
      "but somebody who said who they are is let in anyway")

print("\nthe limit does not shut out somebody already in it")
# A guest coming back to a slot they still hold is not a new connection. Read
# as one, setting the limit to the number of people present meant the next
# person whose network blipped could not get back into the game they were in
# the middle of.
session, loop = make_session()
session.guests = {0: FakeGuest(0, "a"), 1: FakeGuest(1, "b")}
session.drop = lambda slot, reason="": session.guests.pop(slot, None)
session.notify = lambda message: None
session.set_limit(2)
check(not session.may_join(None)[0], "a new guest is refused at the limit")
check(session.may_join(None, resuming=True)[0],
      "and somebody reconnecting to their own slot is not")
session.set_locked(True, by=FakeGuest(0, "a", can=["lock"]))
check(not session.may_join(None, resuming=True)[0],
      "but a lock still applies to them -- that one is not about how many")
session.locked = False

print("\nthe limit cannot shut an account out")
session, loop = make_session()
session.guests = {0: FakeGuest(0, "admin", can=["lock"]),
                  1: FakeGuest(1, "b"), 2: FakeGuest(2, "c")}
check(session.set_limit(1) == 1, "it can be set to one")
check(session.may_join({"name": "seth"})[0],
      "and an account can still get back in on a reload")
session.guests[3] = FakeGuest(3, "d", can=["kick"])
check(session.set_limit(1) == 2,
      "the floor rises to the number of accounts here, so none is cut off")

print("\nlocking it to accounts")
session, loop = make_session()
admin = FakeGuest(0, "admin", can=["lock"])
mate = FakeGuest(1, "mate", can=["kick"])
stranger = FakeGuest(2, "stranger")
session.guests = {0: admin, 1: mate, 2: stranger}
dropped = []
session.drop = lambda slot, reason="": dropped.append(slot)
session.notify = lambda message: None
session.set_locked(True, by=admin)
check(session.locked, "it locks")
check(dropped == [2], "the guest with no account is removed, got %r" % dropped)
check(0 not in dropped and 1 not in dropped,
      "and the two logged in are not -- including the one who asked")
check(not session.may_join(None)[0], "nobody new gets in without an account")
check("accounts only" in session.may_join(None)[1].lower()
      or "named accounts" in session.may_join(None)[1],
      "and is told why: %r" % session.may_join(None)[1])
check(session.may_join({"name": "seth"})[0], "somebody who logs in does")
session.set_locked(False, by=admin)
check(not session.locked and session.may_join(None)[0], "and it unlocks")

print("\nthe host, not the page, decides")
srv = serverlib.Server.__new__(serverlib.Server)
srv.session, srv.loop = session, loop
outbox = asyncio.Queue()
loop.run_until_complete(srv._act(stranger, "lock", {"on": True}, outbox))
reply = outbox.get_nowait()
check(reply.get("t") == "error" and reply.get("reason") == "denied",
      "a guest with no account cannot lock the session: %r" % reply)
check(not session.locked, "and did not")
loop.run_until_complete(srv._act(FakeGuest(can=["kick"]), "lock", {"on": True}, outbox))
check(outbox.get_nowait().get("reason") == "denied",
      "nor can an account that was given something else")
loop.run_until_complete(srv._act(admin, "lock", {"on": True}, outbox))
check(session.locked, "the account that was given it can")
session.set_locked(False, by=admin)
drain(outbox)

print("\nthe things that land on other people ask for a code")
stale = FakeGuest(5, "a remembered phone", can=["kick", "lock", "grant"],
                  fresh=False)
for action, message in (("lock", {"on": True}), ("kick", {"slot": 1}),
                        ("grant", {"name": "mate", "can": []})):
    loop.run_until_complete(srv._act(stale, action, message, outbox))
    reply = outbox.get_nowait()
    drain(outbox)
    check(reply.get("reason") == "code",
          "%s asks for an authenticator code first: %r" % (action, reply))
check(not session.locked, "and none of them happened")
fresh = FakeGuest(5, "a phone in hand", can=["slots"], fresh=False)
drain(outbox)
loop.run_until_complete(srv._act(fresh, "limit", {"count": 2}, outbox))
check(outbox.get_nowait().get("t") == "limits", "and it answers with the limits")
check(session.limit() == 2,
      "but setting the limit does not, because it throws nobody out")

loop.close()
shutil.rmtree(folder, ignore_errors=True)
print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
