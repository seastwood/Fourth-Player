"""Which player a pad actually is, and what the browser is told about it.

The numbering here used to be counted off the guest's slot on the assumption
that somebody at the television is player 1. On the machine this was written
for that was untrue in both directions at once: the pad the host had given to a
guest WAS player 1, and ports two to four were bound to nothing at all -- so a
guest was told they were player 3 while the game called them player 1, and was
offered two seats that did not exist.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fourthplayer import launcher            # noqa: E402

failures = []


def check(ok, why):
    if not ok:
        failures.append(why)


REAL = '''input_player1_joypad_index = "1"
input_player2_joypad_index = "99"
input_player3_joypad_index = "99"
input_player4_joypad_index = "99"
input_player1_reserved_device = "Fourth Player 2"
input_player2_reserved_device = ""
input_player3_reserved_device = ""
input_player4_reserved_device = ""
'''

with tempfile.NamedTemporaryFile("w", suffix=".cfg", delete=False) as fh:
    fh.write(REAL)
    path = fh.name

ports = launcher.ports_from_config(path)
check(ports == {"Fourth Player 2": 1},
      "the guest's pad is player 1, not player 3: %r" % ports)
check("Fourth Player 3" not in ports,
      "a port bound to nothing must not look like a seat")

with tempfile.NamedTemporaryFile("w", suffix=".cfg", delete=False) as fh:
    fh.write('input_player1_reserved_device = "Fourth Player 1"\n'
             'input_player3_reserved_device = "Fourth Player 3"\n')
    two = fh.name
check(launcher.ports_from_config(two) ==
      {"Fourth Player 1": 1, "Fourth Player 3": 3},
      "ports are read as written, gaps and all")

check(launcher.ports_from_config("/nonexistent/nothing.cfg") == {},
      "no config is no answer, not a crash")

# The scan used to stop at the first process whose name ended in "retroarch",
# whether or not that process had been handed a config at all -- and /proc
# comes back in whatever order the kernel feels like. So a second emulator, or
# one somebody started by hand, was enough to make this answer "nothing is
# playing" while a game was plainly playing, and to do it only sometimes.
check(launcher.ports_from_paths(["/nonexistent/nothing.cfg", path])
      == {"Fourth Player 2": 1},
      "an unreadable config does not end the search")
check(launcher.ports_from_paths([path, "/nonexistent/nothing.cfg"])
      == {"Fourth Player 2": 1}, "and order does not matter")
check(launcher.ports_from_paths([]) == {}, "no configs, no answer")
check(launcher.ports_from_paths(["/nonexistent/a", "/nonexistent/b"]) == {},
      "nor several unreadable ones")
os.unlink(path)
os.unlink(two)


# --- what the guests are told -----------------------------------------------
class FakePad:
    def __init__(self, name):
        self.name = name


class FakeGuest:
    def __init__(self, index, label):
        self.pad_index, self.label = index, label


from fourthplayer import session                      # noqa: E402

saved_running = launcher.running
launcher.running = lambda: True

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


live = session.LiveSession.__new__(session.LiveSession)
live.pads = FakePads([FakePad("Fourth Player 1"),
                      FakePad("Fourth Player 2"),
                      FakePad("Fourth Player 3")])
live.guests = {"a": FakeGuest(1, "Dave")}

saved = launcher.player_ports
launcher.player_ports = lambda: {"Fourth Player 2": 1}
try:
    state = live.pad_state()
finally:
    launcher.player_ports = saved

check(state["ports"] == {"1": 1},
      "only the bound pad has a player number: %r" % state["ports"])
check(state["playing"] is True, "and a game is reported as running")
check(state["count"] == 3, "every pad is still offered")
check(state["who"] == {"1": "Dave"}, "who is on it is unchanged")

launcher.player_ports = lambda: (_ for _ in ()).throw(OSError("nope"))
try:
    state = live.pad_state()
finally:
    launcher.player_ports = saved
check(state["ports"] == {},
      "an unreadable game costs the player numbers, not the panel")
# The exact case that put "No game is running" on the screen of somebody
# watching a game run: the host is sandboxed with a /tmp of its own and cannot
# read the file that says which pad is which player. Not knowing the numbers
# is not the same as there being no game.
check(state["playing"] is True,
      "and the game is still reported as running: %r" % state["playing"])
launcher.running = lambda: False
try:
    idle = live.pad_state()
finally:
    launcher.running = saved_running
check(idle["playing"] is False, "with nothing running, nothing is claimed")

# A guest with no name is a guest, not a player: which player they are is not
# knowable from their slot, which is the whole point of the above.
check("Player" not in session.GuestConnection.__init__.__doc__ if
      session.GuestConnection.__init__.__doc__ else True,
      "the docstring should not promise a player number")

# --- and that anybody ever hears about it ---------------------------------
# pad_state was sent once, with the welcome, and never again unless somebody
# changed seats. A guest who was already connected when the game started was
# therefore told "no game is running" for as long as they stayed -- which is
# exactly how it was reported.
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
server_src = open(os.path.join(ROOT, "fourthplayer", "server.py")).read()
app_src = open(os.path.join(ROOT, "web", "app.js")).read()

check('elif kind == "pads":' in server_src,
      "the host answers a request for the current seats")
check('"pads": {"yours": guest.pad_index' in server_src,
      "and still sends them with the welcome")

start = app_src.index("function openPads(")
opener = app_src[start:app_src.index("\n}", start)]
check('send({ t: "pads" })' in opener,
      "the panel asks for fresh seats as it opens")

starting = app_src[app_src.index('case "starting":'):]
starting = starting[:starting.index('case "arrived"')]
check("askSeatsUntilKnown()" in starting,
      "and again once a game has had time to come up")
# It used to be one question eight seconds in, which is a guess at how long a
# game takes to start: right for a cartridge, far too early for a GameCube disc
# through Dolphin, and there was no second attempt. The answer came back "not
# known yet" and stayed on screen until the page was reloaded.
runner = app_src[app_src.index("function askSeatsUntilKnown"):]
runner = runner[:runner.index("\n}")]
check('send({ t: "pads" })' in runner, "the retry is what does the asking")
check("padSeats.ports" in runner,
      "and it gives up as soon as there is a real answer, not on a fixed count")

# Repicking is not a private action: the game stops on the television and
# everybody playing chooses a slot again. One tap from a phone, behind a label
# that did not say so, was a trap.
html_src = open(os.path.join(ROOT, "web", "index.html")).read()
flat = " ".join(html_src.split())
check("Repick player slots" in flat and "Change players on the TV" not in flat,
      "the button says what it does")

opener = app_src[app_src.index('el("pads-repick").addEventListener'):]
opener = opener[:opener.index("});") + 3]
check('send({ t: "repick" })' not in opener,
      "pressing it does not repick on the spot: %r" % opener.strip()[-60:])
check('el("repick-ask").hidden = false' in opener,
      "it asks first")

yes = app_src[app_src.index('el("repick-yes").addEventListener'):]
yes = yes[:yes.index("});") + 3]
check('send({ t: "repick" })' in yes, "only confirming actually repicks")

ask = flat[flat.index('id="repick-ask"'):]
ask = ask[:ask.index("</div>")]
for wanted in ("The game closes for a moment",
               "picker comes up on the television",
               "Everyone playing chooses their slot again",
               "carries on from exactly where it is now",
               "Your place in the game is kept"):
    check(wanted in ask, "the question explains: %r" % wanted)

if failures:
    print("\n".join("FAIL: " + f for f in failures))
    sys.exit(1)
print("ok - player numbers come from the game")
