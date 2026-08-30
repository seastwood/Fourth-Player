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

live = session.LiveSession.__new__(session.LiveSession)
live.pads = [FakePad("Fourth Player 1"), FakePad("Fourth Player 2"),
             FakePad("Fourth Player 3")]
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

if failures:
    print("\n".join("FAIL: " + f for f in failures))
    sys.exit(1)
print("ok - player numbers come from the game")
