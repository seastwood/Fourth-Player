"""An on-screen shoulder has to pull the trigger, not just press the button.

The pad the host builds has real analogue triggers -- ABS_Z and ABS_RZ -- fed
from axes 4 and 5 of every frame. Those axes were filled in only from a
physical controller, where a trigger reports how far it travelled. A finger on
glass has no travel to report, so the button bit arrived and the trigger stayed
at rest, and a game that accelerates with an analogue trigger did nothing:
Crazy Taxi would let you drive its menus and not its car.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the browser half cannot be run.")
    sys.exit(0)

source = open(os.path.join(ROOT, "web", "app.js")).read()
start = source.index("function sendFrame(")
depth, body = 0, None
for j in range(source.index("{", start), len(source)):
    if source[j] == "{":
        depth += 1
    elif source[j] == "}":
        depth -= 1
        if depth == 0:
            body = source[start:j + 1]
            break

HARNESS = """
const FPFrame = require(process.argv[1]);
""" + body + """
const job = JSON.parse(require("fs").readFileSync(0, "utf8"));
let touchButtons = job.touch || 0;
let touchAxes = job.touchAxes || [0, 0, 0, 0];
let lastSent = null, lastSentAt = 0, seq = 0;
const HEARTBEAT_MS = 0, BACKLOG_LIMIT = 1e9, AXIS_EPSILON = 0.01;
let sent = null;
const input = { readyState: "open", bufferedAmount: 0,
                send: (buf) => { sent = Buffer.from(buf); } };
function changed() { return true; }
function report() {}
sendFrame(job.pad ? {
  buttons: Array.from({length: 17}, (_, i) =>
    ({ pressed: (job.padButtons || []).includes(i),
       value: ((job.padValues || {})[i]) || 0 })),
  axes: job.padAxes || [0, 0, 0, 0],
} : null, !!job.releaseAll);
const view = new DataView(sent.buffer, sent.byteOffset, sent.byteLength);
const axes = [];
for (let i = 0; i < 6; i++) axes.push(view.getInt16(8 + i * 2, true));
process.stdout.write(JSON.stringify({ buttons: view.getUint32(4, true), axes }));
"""


def run(job):
    done = subprocess.run(
        [node, "-e", HARNESS, os.path.join(ROOT, "web", "frame.js")],
        input=json.dumps(job), capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(done.stderr[:600])
    return json.loads(done.stdout)


LT, RT = 6, 7           # standard mapping: the two triggers
AX_LT, AX_RT = 4, 5     # where their travel lives on the wire

print("an on-screen trigger arrives as travel, not only as a press")
left = run({"touch": 1 << LT})
check(left["axes"][AX_LT] == 32767,
      "the left trigger is all the way in: %r" % left["axes"][AX_LT])
check(left["buttons"] & (1 << LT), "and the button is reported too, as a pad does")
check(left["axes"][AX_RT] == 0, "the right one is untouched")

right = run({"touch": 1 << RT})
check(right["axes"][AX_RT] == 32767, "and the right trigger the same")
check(right["axes"][AX_LT] == 0, "without disturbing the left")

both = run({"touch": (1 << LT) | (1 << RT)})
check(both["axes"][AX_LT] == 32767 and both["axes"][AX_RT] == 32767,
      "both at once, which is a handbrake turn: %r" % both["axes"][4:])

print("\nnothing pressed is nothing sent")
idle = run({})
check(idle["axes"][AX_LT] == 0 and idle["axes"][AX_RT] == 0,
      "the triggers rest: %r" % idle["axes"][4:])

print("\na physical trigger still reports its own travel")
half = run({"pad": True, "padButtons": [RT], "padValues": {RT: 0.5}})
check(abs(half["axes"][AX_RT] - 16384) < 2,
      "half pressed is half travel, not all of it: %r" % half["axes"][AX_RT])

print("\nand a finger does not undo a physical trigger already held")
mixed = run({"pad": True, "padButtons": [RT], "padValues": {RT: 0.5},
             "touch": 1 << LT})
check(abs(mixed["axes"][AX_RT] - 16384) < 2,
      "the physical one keeps its value: %r" % mixed["axes"][AX_RT])
check(mixed["axes"][AX_LT] == 32767, "and the on-screen one adds its own")

print("\nletting go of everything releases the triggers")
gone = run({"touch": (1 << LT) | (1 << RT), "releaseAll": True})
check(gone["axes"][AX_LT] == 0 and gone["axes"][AX_RT] == 0,
      "a release frame carries no travel: %r" % gone["axes"][4:])

print()
if fails:
    print("FAILURES: %d" % len(fails))
    sys.exit(1)
print("test_triggers: all ok")
