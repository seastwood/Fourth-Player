"""Two ways to make a remote pointer feel like the one in the hand.

Both were asked for after "raw input feels terrible, Like this Machine feels
much better" -- which is worth taking at face value. macOS's acceleration
curve is good and a hand is calibrated to it; what makes a remote pointer feel
wrong is not that curve but that the console applies its *own* on top, so two
curves are composed and the pointer does not go where it is sent however
little latency there is.

  * Sending a position rather than a movement leaves nothing for the console
    to accelerate. It costs nothing in accuracy: this page already tracks
    where the pointer has got to, because the zoom follows it -- and with a
    position on the wire that estimate stops being an estimate.

  * Drawing the pointer here means it moves the instant the hand does, rather
    than a whole pipeline later. Every millisecond this project has shaved off
    the picture is spent again on the one thing the eye watches most closely.

Both are settings, because neither is right for everybody: a position is wrong
for a game reading raw input, and a locally drawn pointer takes the console's
own cursor away from everybody else watching.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

fails = 0


def check(ok, what):
    global fails
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        fails += 1


app = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
page = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
css = open(os.path.join(ROOT, "web", "style.css"), encoding="utf-8").read()
server = open(os.path.join(ROOT, "fourthplayer", "server.py"), encoding="utf-8").read()
wire = open(os.path.join(ROOT, "fourthplayer", "deskwire.py"), encoding="utf-8").read()

print("the console can be sent a position instead of a movement")
check('id="desk-send"' in page, "there is a choice, in the page")
check("function sendsPlace" in app and "SEND_KEY" in app,
      "remembered per browser, like the speed beside it")
check('out.push({ t: "p",' in app,
      "and it sends the point the protocol already carries")
check('{"t": "p", "x": int, "y": int}' in wire,
      "which the host has understood all along -- the absolute pointer is "
      "what the touchscreen path uses")
check("cursorU" in app.split('out.push({ t: "p",')[1][:200],
      "from the estimate this page already keeps for the zoom, which sending "
      "it makes true rather than merely close")
check('out.push({ t: "m"' in app,
      "and movement is still there, because a game reading raw input wants "
      "the console's own curve")

print("and the pointer can be drawn here rather than waited for")
check('id="desk-local"' in page, "there is a choice for that too")
check('id="local-pointer"' in page and ".local-pointer" in css,
      "with something to draw")
check("pointer-events: none" in css.split(".local-pointer")[1][:400],
      "that takes no taps: it is a picture of where the pointer is, not "
      "something to hit")
check("function paintLocalPointer" in app, "moved by the page")
check("cursorClientPoint()" in app.split("function paintLocalPointer")[1][:600],
      "to where the estimate says the pointer is, which is the same place the "
      "zoom follows")

print("and the console stops drawing its own while that is on")
# Two cursors on one picture is worse than a late one.
check("function askAboutPointer" in app, "the page asks")
check('kind == "pointer"' in server, "the host takes the request")
check("self.session.stage.show_pointer(want)" in server, "and acts on it")
check("desk_driver" in server.split('kind == "pointer"')[1][:800],
      "only from the guest holding the keyboard and mouse: the capture is "
      "shared, so this takes the cursor away from everybody else watching, "
      "and only the person driving has any business making that trade")

print("FAILED" if fails else "PASSED")
sys.exit(1 if fails else 0)
