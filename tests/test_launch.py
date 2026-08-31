"""What a guest can and cannot cause to happen on somebody else's television.

The catalogue is the whole security model: a guest sends an id, and the only
thing an id can turn into is a row that was already on the host's disk. So the
checks that matter are that ids carry nothing actionable, that an id nobody
issued resolves to nothing, and that each policy refuses exactly what it says
it refuses.

Nothing here starts a game. The launcher is replaced with a recorder, because
the question is what would have been run, not whether an emulator opens.
"""
import asyncio
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from fourthplayer import catalogue as cat, launcher, session as sessionlib

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


# -- a catalogue over a library that exists only for this test ---------------

def build_catalogue(tmp):
    plists = os.path.join(tmp, "plists")
    os.makedirs(plists)
    rom = os.path.join(tmp, "Super Test (USA).sfc")
    core = os.path.join(tmp, "test_libretro.so")
    for path in (rom, core):
        open(path, "wb").write(b"\0")
    with open(os.path.join(plists, "Nintendo - Super Nintendo Entertainment System.lpl"), "w") as fh:
        json.dump({"items": [{"path": rom, "label": "Super Test (USA)",
                              "core_path": core}]}, fh)
    with open(os.path.join(tmp, "gameplayers.json"), "w") as fh:
        json.dump({"counts": {"Nintendo - Super Nintendo Entertainment System":
                              {"Super Test (USA)": 2}}}, fh)
    cat.PLAYLIST_DIR = plists
    cat.PLAYERS = os.path.join(tmp, "gameplayers.json")
    cat.THUMB_DIR = os.path.join(tmp, "thumbs")
    return cat.Catalogue(), rom, core


tmp = tempfile.mkdtemp(prefix="fp-launch-")
catalogue, ROM, CORE = build_catalogue(tmp)

print("the catalogue gives out names, not instructions")
rows = catalogue.listing()
check(len(rows) == 1, "the one game in the library is listed")
row = rows[0]
check("path" not in row and "core_path" not in row,
      "a listed game carries no path and no core")
check(row["short"] == "Super Nintendo", "the console reads as its short name")
check(row["players"] == 2 and row["bucket"] == "2", "the player count came through")
check(catalogue.find(row["id"])["path"] == ROM, "the id resolves back to the game")
check(catalogue.find("f" * 16) is None, "an id nobody issued resolves to nothing")
check(cat.game_id("a", "b") == cat.game_id("a", "b")
      and cat.game_id("a", "b") != cat.game_id("a", "c"),
      "ids are stable per game and differ between games")
check(cat.bucket(9) == "5+" and cat.bucket(1) == "1" and cat.bucket(0) is None,
      "player counts bucket the way the filter expects")


# -- a session with the emulator replaced by a notebook ----------------------

class Recorder:
    def __init__(self):
        self.launched = []
        self.stopped = 0
        self.busy = False

    def install(self):
        # Same signature as the real one, or the stub hides a caller passing an
        # argument the launcher does not take.
        launcher.launch = lambda row, display=":0", resume=False: (
            self.launched.append(row) or None)
        launcher.preflight = lambda row: None
        launcher.running = lambda: self.busy
        launcher.stop_running = lambda: (setattr(self, "stopped", self.stopped + 1)
                                         or setattr(self, "busy", False) or True)


class Guest:
    slot, label = 0, "Player 2"


def make_session(policy, recorder):
    loop = asyncio.new_event_loop()
    live = sessionlib.LiveSession.__new__(sessionlib.LiveSession)
    live.loop = loop
    live._now = lambda: live.clock
    live.clock = 1000.0
    live.catalogue = catalogue
    live.pending = None
    live.launch_policy = policy
    live.notices = []
    live.on_notice = live.notices.append
    return live, loop


def ask(live, loop, game_id):
    return loop.run_until_complete(live.request_launch(Guest(), game_id))


recorder = Recorder()
recorder.install()
game = row["id"]

print("off refuses everything")
live, loop = make_session("off", recorder)
result = ask(live, loop, game)
check(not result["ok"] and "not turned on" in result["error"],
      "a guest is told the owner has not turned this on")
check(not recorder.launched, "and nothing was launched")

print("a made-up id is refused whatever the policy")
live, loop = make_session("open", recorder)
result = ask(live, loop, "deadbeefdeadbeef")
check(not result["ok"] and "not on this box" in result["error"],
      "an id that is not in the catalogue gets nowhere")
check(not recorder.launched, "and still nothing was launched")

print("idle waits for the screen to be free")
live, loop = make_session("idle", recorder)
recorder.busy = True
result = ask(live, loop, game)
check(not result["ok"] and "already playing" in result["error"],
      "refused while something is playing")
check(not recorder.launched and recorder.stopped == 0,
      "and it does not stop what is playing to make room")
recorder.busy = False
result = ask(live, loop, game)
check(result["ok"] and result["state"] == "starting", "allowed once free")
check(len(recorder.launched) == 1, "the game was started")

print("open takes over")
recorder.launched.clear()
live, loop = make_session("open", recorder)
recorder.busy = True
result = ask(live, loop, game)
check(result["ok"], "allowed even with something playing")
check(recorder.stopped == 1, "what was playing was stopped first")
check(len(recorder.launched) == 1, "and the new game started")

print("approve asks, and nothing happens until it is answered")
recorder.launched.clear()
live, loop = make_session("approve", recorder)
result = ask(live, loop, game)
check(result["ok"] and result["state"] == "pending", "the guest is told to wait")
check(result["seconds"] == round(sessionlib.APPROVAL_SECONDS),
      "and how long they have to wait")
check(not recorder.launched, "nothing started on the strength of asking")
check(live.pending is not None, "the request is being held")

second = ask(live, loop, game)
check(not second["ok"] and "Someone else" in second["error"],
      "a second ask while one is waiting is refused rather than queued")

state = live.launch_state()
check(state["pending"]["who"] == "Player 2" and state["pending"]["label"] == "Super Test (USA)",
      "the owner can see who asked for what")

loop.run_until_complete(live.approve_launch())
check(len(recorder.launched) == 1, "approving starts it")
check(live.pending is None, "and clears the request")

print("...and refusing does not")
recorder.launched.clear()
live, loop = make_session("approve", recorder)
ask(live, loop, game)
live.deny_launch("no")
check(not recorder.launched and live.pending is None, "denied and forgotten")

recorder.launched.clear()
live, loop = make_session("approve", recorder)
ask(live, loop, game)
deadline = live.pending["deadline"]
check(deadline == live.clock + sessionlib.APPROVAL_SECONDS,
      "the deadline is thirty seconds out")
live.clock = deadline + 1                    # the sweeper's condition, arrived
check(live._now() >= live.pending["deadline"], "which does arrive")
live.deny_launch("nobody answered")
check(not recorder.launched, "an unanswered request starts nothing")

print("turning the policy off drops a request that is waiting")
recorder.launched.clear()
live, loop = make_session("approve", recorder)
ask(live, loop, game)
live.set_policy("off")
check(live.pending is None and not recorder.launched,
      "the waiting request went with the policy")
try:
    live.set_policy("whatever")
    check(False, "an unknown policy is refused")
except ValueError:
    check(True, "an unknown policy is refused")

print("the command line offers exactly the policies the session knows")
check(set(sessionlib.LAUNCH_POLICIES) == {"off", "open", "idle", "approve"},
      "off, open, idle, approve")

print("a guest starts a game from the beginning unless they ask not to")
# The opposite default from the television's own menu, and deliberately: there,
# picking a game is somebody resuming their own save; here it is a guest
# starting one on a machine they are not sitting at.
fresh_argv = launcher.build_argv(catalogue.find(game))
check("--fresh" in fresh_argv, "the default carries --fresh: %s" % fresh_argv[:4])
resumed = launcher.build_argv(catalogue.find(game), resume=True)
check("--fresh" not in resumed, "and asking to continue does not")
check(resumed[-2:] == fresh_argv[-2:], "both run the same game either way")

print("the listing says whether there is anything to continue from")
row_now = catalogue.listing()[0]
check("saved" in row_now and "saved_at" in row_now,
      "so the page can offer that only when it is a real choice")
check(row_now["saved"] is False and row_now["saved_at"] is None,
      "with no state file on disk, there is nothing to continue")

print("the choice reaches the launcher")
recorder.launched.clear()
live, loop = make_session("open", recorder)
launcher.launch = lambda row, display=":0", resume=False: (
    recorder.launched.append((row["id"], resume)) or None)   # notes the choice
loop.run_until_complete(live.request_launch(Guest(), game))
check(recorder.launched and recorder.launched[-1][1] is False,
      "asking for a game plainly starts it fresh")
loop.run_until_complete(live.request_launch(Guest(), game, resume=True))
check(recorder.launched[-1][1] is True, "and asking to continue continues")
recorder.install()                       # put the recorder's launch back

print("the command line it would run")
argv = launcher.build_argv(catalogue.find(game))
check(argv[0] == launcher.PICKER, "the player picker is what gets run, not RetroArch")
check(argv[-2:] == [CORE, ROM], "with the core and the game at the end")
check("--max-players" in argv and argv[argv.index("--max-players") + 1] == "2",
      "and the board sized to the game")

print("and the picker will actually appear")
# The guarantee rests on kodi-retrobox's own rule, so it is checked against
# that rule rather than assumed: the picker stands down only for one player
# holding one pad, and a session has already made one pad per slot.
import importlib.machinery
PICKER_SRC = os.path.expanduser("~/.local/bin/ra_players.py")
if not os.path.exists(PICKER_SRC):
    print("  --   ra_players.py is not installed here, so its rule cannot be read")
else:
    loader = importlib.machinery.SourceFileLoader("rp", PICKER_SRC)
    spec = importlib.util.spec_from_loader("rp", loader)
    rp = importlib.util.module_from_spec(spec)
    try:
        loader.exec_module(rp)
    except Exception as exc:                  # pygame or evdev missing
        print("  --   could not load ra_players.py (%s)" % exc)
    else:
        for slots in (1, 2, 3, 4):
            for players in (None, 1, 2, 4):
                check(rp.needs_picker(slots, players) or (slots == 1 and players == 1),
                      "picker appears with %d pad(s), %s-player game"
                      % (slots, players))
        check(not rp.needs_picker(0, 1),
              "and stands down with no pads at all, which a session never has")

print("two games in a row do not fight over one unit name")
# Every game ran as the transient unit `fourth-player-game`. Starting a second
# over the top of a first was then refused outright -- "Unit
# fourth-player-game.service was already loaded" -- because a unit lingers
# after its process is gone. The first game had already been told to quit, so
# the screen held its last frame and nothing replaced it.
names = {launcher.new_unit_name() for _ in range(50)}
check(len(names) == 50,
      "50 launches ask for 50 different unit names, got %d" % len(names))
check(all(n.startswith(launcher.UNIT_PREFIX) for n in names),
      "and every one is still recognisably ours, so the sweep can match them")

print("a game that will not close stops the new one from being launched")
# stop_running() reporting failure means the old game is still on screen.
# Launching anyway gave the guest the worst of both: the game they were
# playing had been told to quit, and the one they asked for was refused.
stubborn_launched = []
saved_stop, saved_launch = launcher.stop_running, launcher.launch
launcher.stop_running = lambda: False
launcher.launch = lambda r, resume=False: stubborn_launched.append(r)
try:
    live2, loop2 = make_session("open", recorder)
    answer = loop2.run_until_complete(
        live2._start_game(catalogue.find(game), busy=True, resume=False))
    check(answer.get("ok") is False,
          "the guest is told it did not happen: %r" % (answer,))
    check(not stubborn_launched,
          "and nothing was launched over a game that is still running")
finally:
    launcher.stop_running, launcher.launch = saved_stop, saved_launch

print(("FAILED: %d" % len(fails)) if fails else "test_launch: all ok")
sys.exit(1 if fails else 0)
