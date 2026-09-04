"""Ending the game from a guest's phone, and keeping the save.

There was no way to end a game from here. Somebody could start one and then
had to ask the room to stop it.

The saving is not a separate step and deliberately so: RetroArch is set to
write its state on the way out and load it again next time, and stop_running()
sends TERM and waits precisely for that. So "end" and "save" are one act, and
the only way to lose progress is to be killed after ignoring TERM -- which the
host says out loud when it happens, rather than reporting a clean stop.

Ending is gated exactly as starting is. It is not the smaller act: somebody in
the room is playing. An owner who wants to be asked before a game starts is
asked before one ends, and an owner who has turned this off is not asked at
all.
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
    from fourthplayer import session as S
    from fourthplayer.session import LiveSession, GuestConnection
    HAVE_HOST = True
except ModuleNotFoundError as exc:
    print("SKIPPED the host half: %s" % exc)
    HAVE_HOST = False

if HAVE_HOST:
    loop = asyncio.new_event_loop()

    class FakeLauncher:
        def __init__(self):
            self.playing = True
            self.clean = True
            self.stops = 0

        def running(self):
            return self.playing

        def stop_running(self):
            self.stops += 1
            self.playing = False
            return self.clean

    fake = FakeLauncher()
    S.launcher.running = fake.running
    S.launcher.stop_running = fake.stop_running

    def a_session(policy):
        live = LiveSession(Config(), loop)
        live.launch_policy = policy
        live.notices = []
        live.on_notice = live.notices.append
        guest = GuestConnection(live, 0, socket=None)
        guest.label = "Ada"
        live.guests[0] = guest
        return live, guest

    def ask(live, guest):
        return loop.run_until_complete(live.request_stop(guest))

    print("when the owner has not turned this on")
    live, guest = a_session("off")
    fake.playing = True
    out = ask(live, guest)
    check(out["ok"] is False, "it is refused")
    check("has not turned on" in out["error"],
          "in the same words starting a game is refused in")
    check(fake.stops == 0, "and nothing is stopped")

    print("\nwhen it is open")
    live, guest = a_session("open")
    fake.playing, fake.clean, fake.stops = True, True, 0
    out = ask(live, guest)
    check(out["ok"] is True and out.get("stopped") is True, "the game is ended")
    check(fake.stops == 1, "through the ordinary stop, which is what waits for "
                           "the save to be written")
    said = " ".join(n.get("message", "") for n in live.notices)
    check("back at the menu" in said, "and everybody is told, got %r" % said)

    print("\nand when it had to be killed")
    live, guest = a_session("open")
    fake.playing, fake.clean, fake.stops = True, False, 0
    out = ask(live, guest)
    check(out["ok"] is True and out["clean"] is False,
          "it still reports that it stopped")
    said = " ".join(n.get("message", "") for n in live.notices)
    check("may not have been kept" in said,
          "but says the save may not have been kept, rather than reporting a "
          "clean stop; got %r" % said)

    print("\nwhen nothing is playing")
    live, guest = a_session("open")
    fake.playing, fake.stops = False, 0
    out = ask(live, guest)
    check(out["ok"] is False and "Nothing is playing" in out["error"],
          "it says so instead of stopping nothing")
    check(fake.stops == 0, "and does not go through the motions")

    print("\nand when the owner wants to be asked")
    live, guest = a_session("approve")
    fake.playing, fake.stops = True, 0
    out = ask(live, guest)
    check(out.get("state") == "pending", "the owner is asked first")
    check(fake.stops == 0, "and nothing stops until they answer")
    check(live.pending and live.pending.get("kind") == "stop",
          "the ask says which kind it is, so approving it ends the game "
          "rather than looking for a game id that is not there")
    result = loop.run_until_complete(live.approve_launch())
    check(result.get("stopped") is True and fake.stops == 1,
          "and approving it ends the game")

    print("\nstarting the same game again")
    live, guest = a_session("open")
    fake.playing, fake.stops = True, 0
    live.last_started = None
    live.LAST_GAME = "/nonexistent/last-game.json"
    out = loop.run_until_complete(live.request_restart(guest))
    check(out["ok"] is False and "cannot tell which game" in out["error"],
          "with nothing to go on it says so rather than guessing at a game")

    asked = {}

    async def fake_launch(g, game_id, resume=False):
        asked["id"], asked["resume"] = game_id, resume
        return {"ok": True, "state": "starting"}

    live.request_launch = fake_launch
    live.last_started = {"id": "abc123", "label": "A Game", "path": "/g.gba"}
    out = loop.run_until_complete(live.request_restart(guest))
    check(asked.get("id") == "abc123", "the game it started is the one it restarts")
    check(asked.get("resume") is False,
          "from the beginning, not from the save -- ending the game is the "
          "way to keep your place, and restart is the one that does not")

    live, guest = a_session("off")
    fake.playing = True
    out = loop.run_until_complete(live.request_restart(guest))
    check(out["ok"] is False, "and it is refused where starting a game is")

    print("\nand a game somebody put on from the television")
    live, guest = a_session("open")
    fake.playing = True
    live.last_started = None
    import json as _json
    import tempfile as _tempfile
    where = _tempfile.mktemp()
    with open(where, "w") as handle:
        _json.dump({"rom": "/home/retro/Games/x.gba"}, handle)
    live.LAST_GAME = where
    live.catalogue.rows = lambda: [{"id": "zz", "path": "/home/retro/Games/x.gba"}]
    found = live.playing_now()
    check(found and found["id"] == "zz",
          "is found by the path the television wrote down, so restart is not "
          "only for games started from a phone")
    os.remove(where)
    loop.close()

    print("\nwhat the page is told about the television")
    live, guest = a_session("open")
    fake.playing = True
    live.last_started = {"id": "abc", "label": "Advance Wars", "path": "/a.gba"}
    live.pads = None
    state = live.pad_state()
    check(state.get("game") == "Advance Wars",
          "the game on now is named, so 'End game' is not asking somebody to "
          "remember what they are about to stop; got %r" % state.get("game"))
    check(state.get("last") is None,
          "and nothing is offered to continue while it is still playing")

    fake.playing = False
    state = live.pad_state()
    check(state.get("game") == "",
          "with nothing on, nothing is named")
    check(state.get("last") and state["last"]["label"] == "Advance Wars",
          "and the last one is offered instead, which is the only useful "
          "thing that tab can say when the television is idle")
    check(state["last"]["id"] == "abc",
          "by id, so continuing it goes through the ordinary launch")

print("\nthe page")
page = open(os.path.join(ROOT, "web", "index.html")).read()
app = open(os.path.join(ROOT, "web", "app.js")).read()
check('id="endgame"' in page, "there is a button")
paint = app[app.index("function paintEndGame()"):]
paint = paint[:paint.index("\nfunction ")]
check("launchMode" in paint and "padSeats.playing" in paint,
      "shown on both counts: whether the owner allows starting and stopping "
      "at all, and whether there is anything to stop")
check("actions.hidden = !can" in paint,
      "and the destructive group is what that hides, not the whole tab -- "
      "continuing the last game is offered on a television that is idle")
check('send({ t: "endgame" })' in app, "and it asks the host")
check(app.count("confirm(") >= 2,
      "and restarting asks too, separately: it is the one of these that "
      "throws something away")
check('id="tab-game"' in page and 'id="tab-controls"' in page,
      "the panel has a tab each for the controller and the television")
check('aria-label="Options"' in page,
      "and the button that opens it is an options icon rather than a word")
check("function showTab(" in app, "the tabs switch")
check('id="game-now"' in page and 'id="continuegame"' in page,
      "the tab names what is playing, and offers the last game when nothing "
      "is")
cont = app[app.index('el("continuegame").addEventListener'):]
cont = cont[:cont.index("\n}")]
check("confirm(" not in cont,
      "continuing is not confirmed: nothing is playing, so it interrupts "
      "nobody -- which is what makes it different from the two above it")
check('resume: true' in cont,
      "and it continues rather than restarting, which is the whole word")
gear = page[page.index('aria-label="Options"'):]
gear = gear[:gear.index("</button>")]
check(gear.count("<rect") >= 8 and "r=\"2.4\"" in gear,
      "and the options icon is a gear -- teeth around a ring with a hub -- "
      "rather than spokes radiating from a circle, which is a sun")
repick = app[app.index('el("pads-repick").hidden'):]
repick = repick[:repick.index("\n")]
check("padSeats.playing" in repick and "launchMode" not in repick,
      "and repicking follows its own rule -- there is a game -- rather than "
      "the owner's rule about who may start one, which it never did before")
check("confirm(" in app,
      "after asking the person pressing it, because it is somebody else's "
      "evening: the phone holding this may be the fourth player in a room "
      "where three people are mid-race")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_endgame: all ok")
