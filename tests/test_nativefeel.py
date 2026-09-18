"""Making the mouse feel like the one in the hand, not a report of it.

Two things stood between them, and neither was latency in the usual sense.

The first is a curve applied twice. Under an ordinary pointer lock, the
browser's movementX and movementY have already had the operating system's
pointer acceleration applied -- the curve that makes a slow drag precise and a
fast one fly. The host then applies its *own* acceleration to the relative
motion it injects. Accelerating an accelerated number does not give a faster
pointer, it gives one that does not go where it is sent, and no amount of
reducing delay fixes it.

The second is that motion was gathered up and sent once per animation frame.
On the wire that is tidy; under the hand it turns a continuous movement into a
sequence of per-frame jumps and adds up to 16.7ms before anything leaves. And
at the far end Windows coalesced the arrivals again by default, so even sending
them separately would have been put back into jumps.
"""
import os
import re
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
win = open(os.path.join(ROOT, "fourthplayer", "windesk.py"), encoding="utf-8").read()

page = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()

print("the browser can be asked for movement nothing has smoothed")
check("unadjustedMovement: true" in app,
      "the pointer lock can ask for the raw motion, which is what a game "
      "reading raw input gets locally")
# Offered rather than imposed. Raw counts arrive in the mouse's own units
# instead of the pixels the accelerated ones use, so turning it on changes how
# fast the pointer feels -- sometimes a lot, and it was reported as the cursor
# feeling funky. The speed setting beside it is what puts that back.
check('id="desk-raw"' in page, "and it is the guest's choice, in the page")
check("function wantsRawMouse" in app and 'RAW_KEY' in app,
      "remembered per browser, like the speed beside it")
check("if (wantsRawMouse()) {" in app,
      "and asked for only when it has been chosen, so nobody's pointer "
      "changes under them")
# Safari rejects the request rather than ignoring the option, so asking and
# not handling the rejection is asking for no pointer lock at all.
spot = app.index("function deskCapture()")
block = app[spot:app.index("\n}", spot)]
check(block.count("requestPointerLock") >= 2,
      "and a browser that refuses the option still gets a plain lock, rather "
      "than losing the mouse entirely")
check("ask.catch" in block, "which is what the rejection is for")

print("and motion leaves as it happens rather than once a frame")
check("requestAnimationFrame(deskFlush)" not in app,
      "no animation frame gathers it up any more")
check("DESK_SEND_GAP" in app, "there is a floor on the message rate")
gap = re.search(r"const DESK_SEND_GAP = (\d+);", app)
check(bool(gap), "as a named number")
if gap:
    ms = int(gap.group(1))
    check(1 <= ms <= 8,
          "%dms -- past what a hand produces, inside what the channel "
          "carries, and a quarter of the frame it replaces" % ms)
check("deskTimer = setTimeout(deskFlush" in app,
      "and a movement inside that gap waits out the remainder rather than "
      "waiting for the next frame")
check("deskPending.dx -= dx" in app,
      "with the fraction carried, so nothing is lost to rounding however "
      "often it is sent")

print("and the host does not put it back into jumps")
check("MOUSEEVENTF_MOVE_NOCOALESCE" in win,
      "Windows merges injected moves within a tick by default, which undoes "
      "the whole of the above at the far end")
check("MOUSEEVENTF_MOVE_NOCOALESCE = 0x2000" in win, "by its real value")
check("MOUSEEVENTF_MOVE\n                                        | MOUSEEVENTF_MOVE_NOCOALESCE"
      in win or "| MOUSEEVENTF_MOVE_NOCOALESCE" in win,
      "and it is set on the move itself")

print("FAILED" if fails else "PASSED")
sys.exit(1 if fails else 0)
