"""Coming back to a game that started while the app was in the background.

"The controls-paused overlay appeared when I reopened the app and the video
stream was mid game and working."

The hold is broadcast when it changes, which is the right thing for a
broadcast and the whole of the bug. The television went from a menu to a game
while this page was away; the host said so to nobody listening; the page came
back still showing "Controls paused" over a picture that was plainly playing.

So a guest is told where they stand as part of being let in, rather than only
when it next changes. It has to be applied before the early return for a page
whose stream never stopped -- which is exactly the page this was reported
from, since the picture was still running when it came back.
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
    HAVE_HOST = True
except ModuleNotFoundError as exc:
    print("SKIPPED the host half: %s" % exc)
    HAVE_HOST = False

if HAVE_HOST:
    loop = asyncio.new_event_loop()
    session = LiveSession(Config(), loop)

    def a_guest(slot):
        guest = GuestConnection(session, slot, socket=None)
        guest.label = "Guest %d" % (slot + 1)
        session.guests[slot] = guest
        return guest

    one, two = a_guest(0), a_guest(1)

    print("what a guest is told as they are let in")
    session.input_held = True
    session.hold_reason = "the Kodi menu"
    state = session.hold_state(one)
    check(state["held"] is True, "that the controls are held, when they are")
    check(state["why"] == "the Kodi menu", "and what is in front")
    check(state["driving"] is False, "and that this one is not driving")

    session.driver = 1
    check(session.hold_state(two)["driving"] is True,
          "the guest who may drive is told they may")
    check(session.hold_state(one)["driver_label"] == "Guest 2",
          "and everybody else is told who is")
    check(session.hold_state(two)["driver_label"] == "",
          "without telling the driver they are somebody else")

    print("\nand a game that started while they were away")
    session.input_held = False
    session.hold_reason = ""
    session.driver = None
    check(session.hold_state(one)["held"] is False,
          "the state is the state now, not the last one broadcast -- which is "
          "the whole point of sending it on the way in")

    print("\nand the broadcast still says the same thing")
    sent = []
    session.on_notice_one = lambda guest, message: sent.append((guest.slot, message))
    session.input_held = True
    session._tell_about_the_hold()
    check(len(sent) == 2, "every guest hears about a change")
    check(all(m["t"] == "hold" for _, m in sent), "as a hold message")
    check(all(m["held"] is True for _, m in sent),
          "carrying the same fields the joined message now carries, from the "
          "one place that works them out")
    loop.close()

print("\nthe wiring")
server = open(os.path.join(ROOT, "fourthplayer", "server.py")).read()
check('"hold": self.session.hold_state(guest)' in server,
      "the welcome carries where this guest stands")

app = open(os.path.join(ROOT, "web", "app.js")).read()
joined = app[app.index("function joined(message) {"):]
joined = joined[:joined.index("\n}")]
code = re.sub(r"/\*.*?\*/", " ", joined, flags=re.S)
code = re.sub(r"(?m)//.*$", " ", code)
check("holdInput(message.hold)" in code, "and the page applies it")
check(code.index("holdInput(message.hold)") < code.index("resumed_media"),
      "before the early return for a stream that never stopped, which is the "
      "page this was reported from")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_holdstate: all ok")
