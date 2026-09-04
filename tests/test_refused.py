"""Being told the link or PIN is no good, and being able to do something.

The page went one way only. gate.hidden = true when somebody joined, and
nothing anywhere ever set it back -- so asking for the PIN again wrote the
reason into the join screen's error box while that screen was hidden. What a
guest saw was a chip reading "That link or PIN is not valid" and no way to
give one.

It was worse than that, because the page did not even reach the asking. A
refused resume was counted, and the PIN was only asked for on the second
refusal -- but the first refusal stops the page retrying, so the second never
came. The counting was aimed at a different case: a slot the host had not yet
swept refuses once and accepts the next time, and that one answers "every
player slot is taken", not "your link is stale".

So the host says why in a word now, and the page acts on the word: a stale
credential or an ended session cannot come good by waiting, and the join
screen goes back up at once. A full session or a lockout is worth keeping the
credential for.
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


server = open(os.path.join(ROOT, "fourthplayer", "server.py")).read()
app = open(os.path.join(ROOT, "web", "app.js")).read()

print("the host says why, not just what")
for reason in ("credential", "closed", "full", "locked"):
    check('"reason": "%s"' % reason in server or '"%s"' % reason in server,
          "a refusal can be %r" % reason)
refusals = re.findall(r'"t": "error"[^}]*', server)
check(len(refusals) >= 5, "found %d refusals in the server" % len(refusals))
missing = [r for r in refusals
           if "reason" not in r and "could not" not in r and "restart" not in r]
check(not missing,
      "and every refusal about getting in carries one: %s"
      % [m[:60] for m in missing])

print("\nand the page can act on it")
check("function backToGate(" in app, "there is a way back to the join screen")
back = app[app.index("function backToGate("):]
back = back[:back.index("\nfunction ")]
check("gate.hidden = false" in back and "stage.hidden = true" in back,
      "which actually shows it, rather than writing into a hidden box")
check("video.pause" in back and "srcObject = null" in back,
      "and stops the picture -- a stream left running behind the join screen "
      "is somebody else's game still making noise at a person typing a PIN")
check("pc.close" in back, "and drops the peer, rather than leaving one open "
      "for a session this page has left")

ask = app[app.index("function askForPin("):]
ask = ask[:ask.index("\n/* Put the join screen back")]
check("backToGate()" in ask,
      "and asking for the PIN goes back there, which is the whole of the bug")

if not shutil.which("node"):
    print("\nSKIPPED the behaviour: node is not installed")
    sys.exit(1 if fails else 0)


def lift(name):
    start = app.index("function " + name + "(")
    depth = 0
    for j in range(app.index("{", start), len(app)):
        if app[j] == "{":
            depth += 1
        elif app[j] == "}":
            depth -= 1
            if depth == 0:
                return app[start:j + 1]
    raise AssertionError(name)


harness = """
'use strict';
%(code)s

const job = JSON.parse(process.argv[1]);
let resumeRefused = job.already || 0;
let asked = null, chip = null;
global.gate = { hidden: true };            // playing: the join screen is away
global.askForPin = (why) => { asked = why; };
global.setLink = (kind, said) => { chip = said; };

onError({ t: "error", message: job.message, reason: job.reason });
console.log(JSON.stringify({ asked, chip, refused: resumeRefused }));
"""

code = "\n\n".join([re.search(r"const HOPELESS = \[[^\]]*\];", app).group(0),
                    lift("onError")])


def run(message, reason=None, already=0):
    job = json.dumps({"message": message, "reason": reason, "already": already})
    done = subprocess.run([shutil.which("node"), "-e", harness % {"code": code}, job],
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(done.stderr[-600:])
    return json.loads(done.stdout)


print("\na stale link, while playing")
out = run("That link or PIN is not valid.", "credential")
check(out["asked"] is not None,
      "the join screen is asked for on the first refusal, not the second that "
      "never comes")
check(out["chip"] == "That link or PIN is not valid.",
      "and the chip still says what happened")

print("\nan ended session")
out = run("There is no session open.", "closed")
check(out["asked"] is not None, "also goes back: there is nothing to resume")

print("\na full session")
out = run("Every player slot is taken.", "full")
check(out["asked"] is None,
      "does not: the credential is fine and a swept slot may free up, which "
      "is what the one-retry tolerance was always for")
out = run("Every player slot is taken.", "full", already=1)
check(out["asked"] is not None, "but a second refusal is a real no")

print("\nand an error about what they just asked for")
out = run("That pad is taken.", "request")
check(out["asked"] is None and out["refused"] == 0,
      "is shown and not counted: a seat somebody could not take says nothing "
      "about whether their link is still good")
out = run("That pad is taken.", "request", already=1)
check(out["asked"] is None,
      "and still not counted on the second one, or two fumbled seat changes "
      "would end a session")

print("\nand a host too old to say why")
out = run("That link or PIN is not valid.")
check(out["asked"] is not None,
      "falls back to its words, which are fixed strings on the other side of "
      "this connection")
out = run("Every player slot is taken.")
check(out["asked"] is None, "and still waits out the ones worth waiting out")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_refused: all ok")
