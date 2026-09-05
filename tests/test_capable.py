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

print("\nSteam's own window over its own game does not hold the person given it")
# The shell rule holds guest pads out of menus. Steam's loader, its overlay and
# Big Picture all come to the front during a game, and the watcher calls every
# one of them "steam" -- so an account that had been granted the game was held
# out of it, on and off, for as long as it played.
session, loop = make_session()
session.steam_here(BROFORCE, "Broforce")
given = FakeGuest(2, "given", can=["steam"])
nothing = FakeGuest(3, "nothing")
session.input_held = True
session.hold_reason = "steam"
check(not session.holding(given)[0],
      "the account given the game plays through Steam's own window")
check(session.holding(nothing)[0], "and everybody else is still held")
session.hold_reason = "steamwebhelper"
check(not session.holding(given)[0], "Big Picture counts as Steam's own too")

# Every other shell still holds everybody.
for shell in ("kodi", "moonlight", "xfdesktop", "thunar"):
    session.hold_reason = shell
    check(session.holding(given)[0],
          "%s still holds even an account with Steam: a grant for a game says "
          "nothing about a desktop" % shell)

# And with no Steam game playing, Steam's own window is an ordinary shell.
session.steam_here("")
session.hold_reason = "steam"
check(session.holding(given)[0],
      "with no Steam game running, Steam's window holds everybody as before")
session.input_held = False

print("\nthe hold only lets go of the buttons of the people it holds")
# Steam's own window flickers to the front repeatedly while a game runs --
# twice in ten seconds, measured on the console. Each time, the hold turned on
# and every pad was released. An account that had been given the game was not
# held, so its frames flowed, and its buttons were wiped every few seconds:
# indistinguishable from a controller that does not work at all.


class FakePad:
    def __init__(self, index):
        self.index = index
        self.released = 0

    def release_all(self):
        self.released += 1


class FakePads:
    def __init__(self, count):
        self.pads = [FakePad(i) for i in range(count)]

    def live(self):
        return list(enumerate(self.pads))


session, loop = make_session()
session.notify = lambda message: None
session.notify_one = lambda guest, message: None
session.pads = FakePads(4)
session.steam_here(BROFORCE, "Broforce")
given = FakeGuest(0, "given", can=["steam"])
given.pad_index = 0
nothing = FakeGuest(1, "nothing")
nothing.pad_index = 1
session.guests = {0: given, 1: nothing}
session._hold_input(True, "steam")
check(session.pads.pads[0].released == 0,
      "the account playing the game keeps its buttons")
check(session.pads.pads[1].released == 1,
      "and the guest who is held loses theirs")
check(session.pads.pads[2].released == 1,
      "a pad with nobody on it is released too, which costs nothing")

# An ordinary menu is still one answer for everybody.
session, loop = make_session()
session.notify = lambda message: None
session.notify_one = lambda guest, message: None
session.pads = FakePads(2)
given = FakeGuest(0, "given", can=["steam"])
given.pad_index = 0
session.guests = {0: given}
session._hold_input(True, "kodi")
check(session.pads.pads[0].released == 1,
      "Kodi's menu still lets go of everybody's, Steam grant or not")

print("\nthe frames really do stop")
session, loop = make_session()
session.steam_here(BROFORCE, "Broforce")
watching = FakeGuest(3, "watching")
watching.session = session
sent = []


class Pad:
    """Only what the code under test asks of a device."""
    forgotten = []

    def apply(self, state, sender=None):
        sent.append(sender)

    def forget(self, sender):
        Pad.forgotten.append(sender)

    def adopt_new_sender(self, sender=None):
        pass

    def release_all(self):
        pass


watching.pad_index = 0
# The real frame, through the real decoder, into the real feed(): the whole
# point is that nothing on the page is what stops it.
from fourthplayer import protocol
# Saved and put back at the end of this block. Left in place it silently
# re-points every later check that touches a pad -- which is how the checks
# below it passed while proving nothing.
REAL_PAD = GuestConnection.pad
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
GuestConnection.pad = REAL_PAD

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
session.set_locked("accounts", by=FakeGuest(0, "a", can=["lock"]))
check(not session.may_join(None, resuming=True)[0],
      "but a lock still applies to them -- that one is not about how many")
session.locked = ""

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

print("\nSteam's own interface is the owner's alone")
# `steam` means "the games on the owner's list". Big Picture is the shop, the
# library, the settings and the account behind them, and handing that to
# everybody who may play Broforce is not what granting Broforce meant.
session, loop = make_session()
if accounts.find("seth") is None:
    accounts.add("seth", "a-good-password", ["grant", "steam"])
else:
    accounts.set_capabilities("seth", ["grant", "steam"])
if accounts.find("mate") is None:
    accounts.add("mate", "another-password", ["steam"])
else:
    accounts.set_capabilities("mate", ["steam"])

shell_row = {"id": "s", "label": "Steam Big Picture", "system": "Steam",
             "short": "STEAM", "kind": "steam", "appid": "bigpicture",
             "shell": True}
game_row = {"id": "b", "label": "Broforce", "system": "Steam",
            "short": "STEAM", "kind": "steam", "appid": BROFORCE}

owner = FakeGuest(0, "owner", can=["grant", "steam"])
owner.account = "seth"
player = FakeGuest(1, "player", can=["steam"])
player.account = "mate"
nobody = FakeGuest(2, "nobody")

check(accounts.is_primary("seth"), "seth is the primary admin")
check(session.may_start(owner, shell_row), "the owner may open Big Picture")
check(not session.may_start(player, shell_row),
      "an account with steam may not -- that grant was for games")
check(not session.may_start(nobody, shell_row), "and nobody else may")
check(session.may_start(player, game_row),
      "while the game on the list is still theirs to start")

print("\nand the owner may drive it once it is open")
session.steam_here("bigpicture", "Steam Big Picture")
check(not session.holding(owner)[0], "the owner drives Steam's own screen")
check(session.holding(player)[0],
      "an account with steam does not: %r" % (session.holding(player),))
check(session.holding(nobody)[0], "and neither does anybody else")

# The poll cannot see Big Picture in the process table, so this must not lean
# on steam_now staying set.
session.steam_here("")
session.input_held = True
session.hold_reason = "steamwebhelper"
check(not session.holding(owner)[0],
      "still the owner's once the poll has forgotten, while Steam is in front")
check(session.holding(player)[0], "and still nobody else's")
session.hold_reason = "kodi"
check(session.holding(owner)[0],
      "but Kodi's menu holds the owner like everybody else")
session.input_held = False

print("\nsaying who may be in the session")
session, loop = make_session()
admin = FakeGuest(0, "admin", can=["lock", "grant"])
admin.account = "seth"
mate = FakeGuest(1, "mate", can=["kick"])
mate.account = "mate"
outsider = FakeGuest(2, "no account")
session.guests = {0: admin, 1: mate, 2: outsider}
dropped = []
session.drop = lambda slot, reason="": dropped.append(slot)
session.notify = lambda message: None
if accounts.find("seth") is None:
    accounts.add("seth", "a-good-password", ["grant", "lock"])
else:
    accounts.set_capabilities("seth", ["grant", "lock"])

session.set_locked("accounts", by=admin)
check(session.locked == "accounts", "it can be shut to accounts")
check(dropped == [2], "and the guest with no account goes: %r" % dropped)

dropped.clear()
session.set_locked("named", by=admin, allowed=[])
check(session.locked == "named", "it can be shut to certain accounts")
check(1 in dropped, "an account that is not named goes: %r" % dropped)
check(0 not in dropped, "and whoever asked for it does not")
check("seth" in session.allowed,
      "asking puts you on the list, so you cannot leave yourself off")
check(session.may_stay(admin), "so you may stay")
check(not session.may_stay(mate), "and the account not named may not")

session.set_locked("named", by=admin, allowed=["mate"])
check(session.may_stay(mate), "an account that is named may stay")

print("\nthe first account made can never be shut out")
# A lock is a door and somebody has to keep a key. An owner who can shut
# themselves out of their own television, from a phone, at the moment they
# most want back in, has been handed a footgun rather than a control.
check(accounts.is_primary("seth"),
      "the primary is the oldest account holding grant")
elsewhere = FakeGuest(3, "seth on another phone")
elsewhere.account = "seth"
session.set_locked("named", by=mate, allowed=["mate"])
check(session.may_stay(elsewhere),
      "somebody else locking them out does not lock them out")
check(session.may_join({"name": "seth"})[0], "and they can always join")
check(not session.may_join({"name": "nobody"})[0],
      "while an account that is not named cannot")
session.set_locked("", by=admin)
check(session.may_join(None)[0], "and off means anybody with the invite again")

print("\nsomebody joining mid-Steam-game is said out loud")
# Fought with code for a while -- withholding devices, then unplugging and
# replugging them as permissions changed -- four mechanisms deep, none of them
# provable from outside, and between them they left a game with no controls at
# all. Saying it and giving the owner a lock is smaller and truer.
session, loop = make_session()
session.steam_here(BROFORCE, "Broforce")
told = []
session.notify_one = lambda guest, message: told.append((guest.label, message))
session.notify = lambda message: None
owner = FakeGuest(0, "owner", can=["lock"])
newcomer = FakeGuest(1, "newcomer")
session.guests = {0: owner, 1: newcomer}
session.warn_about_joining(newcomer)
notes = [m for _who, m in told if m.get("t") == "note"]
check(notes, "the owner is told: %r" % (told,))
check(notes and "Broforce" in notes[0]["message"], "which game it is about")
check(notes and "lock" in notes[0]["message"].lower(),
      "and what they can do about it: %r" % notes[0]["message"])
check(all(who != "newcomer" for who, _m in told),
      "the person who just joined is not told off for joining")

told.clear()
session.steam_here("")
session.warn_about_joining(newcomer)
# Notes only: steam_here also tells everybody where they stand on the hold,
# and that is not this.
check(not [m for _who, m in told if m.get("t") == "note"],
      "and nothing is said when no Steam game is playing: %r" % (told,))

print("\nlocking it to accounts")
session, loop = make_session()
admin = FakeGuest(0, "admin", can=["lock"])
mate = FakeGuest(1, "mate", can=["kick"])
stranger = FakeGuest(2, "stranger")
session.guests = {0: admin, 1: mate, 2: stranger}
dropped = []
session.drop = lambda slot, reason="": dropped.append(slot)
session.notify = lambda message: None
session.set_locked("accounts", by=admin)
check(session.locked == "accounts", "it locks")
check(dropped == [2], "the guest with no account is removed, got %r" % dropped)
check(0 not in dropped and 1 not in dropped,
      "and the two logged in are not -- including the one who asked")
check(not session.may_join(None)[0], "nobody new gets in without an account")
check("accounts only" in session.may_join(None)[1].lower()
      or "named accounts" in session.may_join(None)[1],
      "and is told why: %r" % session.may_join(None)[1])
check(session.may_join({"name": "seth"})[0], "somebody who logs in does")
session.set_locked("", by=admin)
check(not session.locked and session.may_join(None)[0], "and it unlocks")

print("\na Steam game is held from the moment it is asked for")
# Steam takes about eighteen seconds to spawn the marker the poll looks for.
# Waiting for it left a quarter of a minute in which a guest who had not been
# given the game was not held from it -- which is most of the time anybody
# would need.
session, loop = make_session()
session.notify = lambda message: None
session.notify_one = lambda guest, message: None
launcher.launch = lambda row, resume=False: None
launcher.clear_the_screen = lambda: []
clock = [1000.0]
session._now = lambda: clock[0]
watching = FakeGuest(3, "watching")
loop.run_until_complete(session._start_game(dict(FakeCatalogue.ROWS[1])))
check(session.steam_now == BROFORCE,
      "the appid is written down at launch, not when the poll finds it: %r"
      % session.steam_now)
check(session.holding(watching)[0], "so a guest without it is held at once")

# The poll, finding nothing yet, must not undo that.
session.steam_here("", polled=True)
check(session.steam_now == BROFORCE,
      "a poll that cannot see it yet does not unhold everybody")
clock[0] += session.STEAM_STARTING + 1
for _ in range(session.STEAM_MISSES):
    session.steam_here("", polled=True)
check(session.steam_now == "",
      "but once it has had long enough, and the poll has missed it enough "
      "times in a row, the process table is the truth")

# The grace is the poll's, because the poll is the only caller whose
# "nothing is running" is uncertain. An explicit clear is not.
session.steam_here(BROFORCE, "Broforce", starting=True)
session.steam_here("")
check(session.steam_now == "",
      "an explicit clear is believed straight away, whatever the poll would say")

# A launch that failed holds nobody.
session, loop = make_session()
session.notify = lambda message: None
session.notify_one = lambda guest, message: None
launcher.launch = lambda row, resume=False: "it would not start"
result = loop.run_until_complete(session._start_game(dict(FakeCatalogue.ROWS[1])))
check(not result["ok"], "a launch can fail: %r" % result)
check(session.steam_now == "", "and then nobody is held for it")

print("\nthe stricter answer is settled first")
# Both are read on one tick. In the other order there is a moment where the
# shell hold has been released because a game is up and the appid has not been
# recorded yet -- a moment in which a guest who has not been given that game
# may drive it.
# Read from the file rather than through inspect: the loop is not a method
# with a name worth guessing at, and every other structural check in this
# suite reads the source the same way.
source = open(os.path.join(ROOT, "fourthplayer", "session.py"),
              encoding="utf-8").read()
tick = source.split("await asyncio.sleep(SWEEP_INTERVAL)")[1].split("\n    async def ")[0]
check("_steam_in_front" in tick and "_watch_the_screen" in tick,
      "the tick reads both")
check(tick.index("_steam_in_front") < tick.index("_watch_the_screen"),
      "and records the Steam game before releasing the shell hold")

print("\nlogging in says so, when it changes whether you are held")
# The hold is broadcast when what is in front changes, which is right for a
# Steam game starting and useless for somebody logging in while one is already
# playing: the host starts letting their frames through and their page goes on
# saying "Controls paused" over a dimmed controller.
session, loop = make_session()
session.steam_here(BROFORCE, "Broforce")
told = []
session.notify_one = lambda guest, message: told.append((guest.label, message))
session.notify = lambda message: None
guest = FakeGuest(1, "seth")
session.guests = {1: guest}
check(session.holding(guest)[0], "held before logging in")
session.login_ok(guest, {"name": "seth", "can": ["steam"]}, "10.0.0.1")
holds = [m for _who, m in told if m.get("t") == "hold"]
check(holds, "logging in tells the page about the hold: %r" % (told,))
check(holds and holds[-1]["held"] is False,
      "and says it is over: %r" % (holds[-1] if holds else None))

told.clear()
session.logout(guest)
holds = [m for _who, m in told if m.get("t") == "hold"]
check(holds and holds[-1]["held"] is True,
      "logging out says it is back on: %r" % (holds[-1] if holds else None))
check(holds and "Broforce" in (holds[-1].get("because") or ""),
      "with the reason filled in")

told.clear()
session.login_ok(guest, {"name": "seth", "can": ["steam"]}, "10.0.0.1")
told.clear()
if accounts.find("seth") is None:
    accounts.add("seth", "a-good-password", ["steam"])
else:
    accounts.set_capabilities("seth", ["steam"])
accounts.set_capabilities("seth", ["kick"])
session.refresh_capabilities("seth")
holds = [m for _who, m in told if m.get("t") == "hold"]
check(holds and holds[-1]["held"] is True,
      "a capability taken away while a Steam game is playing says so too: %r"
      % (holds[-1] if holds else None))

print("\nthe host, not the page, decides")
srv = serverlib.Server.__new__(serverlib.Server)
srv.session, srv.loop = session, loop
outbox = asyncio.Queue()
loop.run_until_complete(srv._act(stranger, "lock", {"mode": "accounts"}, outbox))
reply = outbox.get_nowait()
check(reply.get("t") == "error" and reply.get("reason") == "denied",
      "a guest with no account cannot lock the session: %r" % reply)
check(not session.locked, "and did not")
loop.run_until_complete(srv._act(FakeGuest(can=["kick"]), "lock", {"mode": "accounts"}, outbox))
check(outbox.get_nowait().get("reason") == "denied",
      "nor can an account that was given something else")
loop.run_until_complete(srv._act(admin, "lock", {"mode": "accounts"}, outbox))
check(session.locked, "the account that was given it can")
session.set_locked("", by=admin)
drain(outbox)

print("\nthe things that land on other people ask for a code")
stale = FakeGuest(5, "a remembered phone", can=["kick", "lock", "grant"],
                  fresh=False)
for action, message in (("lock", {"mode": "accounts"}), ("kick", {"slot": 1}),
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

print("\ncoming back mid-game does not take the game away")
# Sockets drop here constantly: a stalled encoder, a browser that refuses the
# video, a phone changing network. Each one used to become "you are logged out
# and your controller has stopped", in the middle of a game, with nothing said.
session, loop = make_session()
session.steam_here(BROFORCE, "Broforce")
session.notify = lambda message: None
session.notify_one = lambda guest, message: None
back_in = FakeGuest(1, "seth", can=["steam"])
session.guests = {1: back_in}
check(not session.holding(back_in)[0], "playing a Steam game they were given")


class Record2:
    slot = 1


class Invite2:
    guests = {1: Record2()}

    def guest_for(self, token, now=None):
        return Record2()


session.invite = Invite2()
session.detach_peer = lambda g: None
session.publish_pad_names = lambda: None
same = session.resume("their-token", object(), "")
check(same is back_in, "a resume puts them back in the same seat")
check(not session.holding(same)[0],
      "and the game is still theirs to play: %r" % (session.holding(same),))
check(same.logged_in_at == 0.0,
      "though the authenticator code is not still fresh")

print("\none missed poll does not mean the game has gone")
# It was logged as "the Steam game has gone" and back again four seconds later
# with nobody touching anything. Every miss unholds every guest for as long as
# it lasts, and a guest joining in that window is given a controller -- which a
# Steam game, having bound device to player when it started, hands the game to.
session, loop = make_session()
session.notify = lambda message: None
session.notify_one = lambda guest, message: None
session.steam_here(BROFORCE, "Broforce")
check(session.steam_now == BROFORCE, "the game is in front")
for n in range(session.STEAM_MISSES - 1):
    session.steam_here("", polled=True)
    check(session.steam_now == BROFORCE,
          "miss %d does not clear it" % (n + 1))
session.steam_here(BROFORCE, "Broforce", polled=True)
check(session.steam_now == BROFORCE, "seeing it again resets the count")
for n in range(session.STEAM_MISSES - 1):
    session.steam_here("", polled=True)
check(session.steam_now == BROFORCE, "so the count really did reset")
session.steam_here("", polled=True)
check(session.steam_now == "",
      "but enough misses in a row is a game that really has ended")

loop.close()
shutil.rmtree(folder, ignore_errors=True)
print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
