"""The browser and the host must agree on the 20 bytes, byte for byte.

`web/frame.js` and `fourthplayer/protocol.py` are two hand-written struct
layouts in two languages. Nothing but a test stops them drifting: change the
button order in one and every guest's A button becomes B, silently, on a
machine you are not sitting at.

So this runs the real `frame.js` under node and decodes what it produces with
the real Python decoder. Skips, loudly, where node is not installed.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from fourthplayer import protocol as P

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the browser half cannot be run.")
    print("         Install nodejs to check the two frame layouts still agree.")
    sys.exit(0)

HARNESS = r"""
const F = require(process.argv[1]);
function pad(buttons, axes, triggers) {
  return {
    buttons: Array.from({length: 17}, (_, i) => ({
      pressed: buttons.includes(i),
      value: i === 6 ? (triggers[0] || 0) : i === 7 ? (triggers[1] || 0) : 0,
    })),
    axes: axes,
  };
}
const cases = JSON.parse(require("fs").readFileSync(0, "utf8"));
const out = cases.map(c =>
  Array.from(new Uint8Array(
    F.buildFrame(c.pad === null ? null : pad(c.buttons, c.axes, c.triggers),
                 c.seq, c.release))));
process.stdout.write(JSON.stringify(out));
"""

cases = [
    {"name": "everything at rest", "pad": 1, "buttons": [], "axes": [0, 0, 0, 0],
     "triggers": [0, 0], "seq": 0, "release": False},
    {"name": "A and Start", "pad": 1, "buttons": [P.BTN_A, P.BTN_START],
     "axes": [0, 0, 0, 0], "triggers": [0, 0], "seq": 1, "release": False},
    {"name": "sticks at the corners", "pad": 1, "buttons": [],
     "axes": [-1, 1, 1, -1], "triggers": [0, 0], "seq": 2, "release": False},
    {"name": "triggers held", "pad": 1, "buttons": [P.BTN_LT, P.BTN_RT],
     "axes": [0, 0, 0, 0], "triggers": [1, 0.5], "seq": 3, "release": False},
    {"name": "the whole d-pad", "pad": 1,
     "buttons": [P.BTN_UP, P.BTN_DOWN, P.BTN_LEFT, P.BTN_RIGHT],
     "axes": [0, 0, 0, 0], "triggers": [0, 0], "seq": 4, "release": False},
    {"name": "every button at once", "pad": 1, "buttons": list(range(17)),
     "axes": [0, 0, 0, 0], "triggers": [1, 1], "seq": 5, "release": False},
    {"name": "the sequence wrap", "pad": 1, "buttons": [P.BTN_B],
     "axes": [0, 0, 0, 0], "triggers": [0, 0], "seq": 65535, "release": False},
    {"name": "no pad at all", "pad": None, "buttons": [], "axes": [0, 0, 0, 0],
     "triggers": [0, 0], "seq": 6, "release": False},
    {"name": "a deliberate release", "pad": 1, "buttons": [P.BTN_A],
     "axes": [1, 1, 1, 1], "triggers": [1, 1], "seq": 7, "release": True},
]

result = subprocess.run(
    [node, "-e", HARNESS, "--", os.path.join(ROOT, "web", "frame.js")],
    input=json.dumps(cases), capture_output=True, text=True)
if result.returncode != 0:
    print("  FAIL  node could not run frame.js:\n" + result.stderr)
    sys.exit(1)

frames = json.loads(result.stdout)

print("the browser's frames, decoded by the host")
for case, raw in zip(cases, frames):
    data = bytes(raw)
    check(len(data) == P.FRAME_SIZE,
          "%s: %d bytes" % (case["name"], len(data)))
    try:
        state = P.decode(data)
    except P.ProtocolError as exc:
        check(False, "%s: the host refused it (%s)" % (case["name"], exc))
        continue

    check(state.seq == case["seq"] & 0xFFFF, "%s: sequence" % case["name"])
    check(state.release_all == case["release"], "%s: release flag" % case["name"])

    expected = 0 if (case["pad"] is None or case["release"]) else \
        sum(1 << b for b in case["buttons"])
    check(state.buttons == expected,
          "%s: buttons %#x, expected %#x" % (case["name"], state.buttons, expected))

    if case["pad"] is not None and not case["release"]:
        for i in range(4):
            want = max(-32768, min(32767, round(case["axes"][i] * 32767)))
            check(state.axes[i] == want,
                  "%s: axis %d is %d, expected %d"
                  % (case["name"], i, state.axes[i], want))
        for i, value in enumerate(case["triggers"]):
            want = max(0, min(32767, round(value * 32767)))
            check(state.axes[4 + i] == want,
                  "%s: trigger %d is %d, expected %d"
                  % (case["name"], i, state.axes[4 + i], want))
    else:
        check(all(a == 0 for a in state.axes),
              "%s: every axis is centred" % case["name"])

print("\nand a released frame produces a released pad")
released = P.decode(bytes(frames[-1]))
check(released.release_all and released.buttons == 0,
      "the release frame carries no buttons at all")

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
