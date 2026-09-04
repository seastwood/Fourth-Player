"""Reconnect, after the page has been away for a while.

"Reconnect doesn't always work after not having been on the page for a while.
I have to refresh and re-enter the PIN."

A page that has been in the background long enough has usually been swept: the
host frees a slot held by a connection it has not heard from, and the token
that went with it is burned. The resume that Reconnect sends is then never
going to be answered -- and there was nothing watching for that.

The deadline that exists for exactly this was armed on one path only, the
automatic resume at page load. Reconnect sent its resume and armed nothing, so
the chip said "reconnecting" and nothing else ever happened. Refreshing worked
because refreshing is the path that armed it, which is why the remedy was
always "refresh and type the PIN again".

And when it did fire it checked the wrong screen: `if (!gate.hidden)` is "only
when the join screen is already showing", which is the one case where nobody
needs to be sent to it. The person it was written for -- somebody who was
playing -- got nothing.

The last thing here is a trap the fix could have walked into. reconnectSoon()
retries on a backoff, so arming a fresh deadline per attempt would push it out
for ever on a connection that fails quickly: the same stuck page by a
different road. One deadline covers the whole attempt.
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


app = open(os.path.join(ROOT, "web", "app.js")).read()


def code_only(text):
    """Source with its comments taken out.

    Checked more than once in this suite by accident: a comment that explains
    what the code no longer does contains the very words the check is looking
    for, and the check passes on the prose while the code says the opposite.
    """
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"(?m)//.*$", " ", text)


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


print("every resume gets a deadline, not just the one at load")
connect = lift("connect")
head = connect[:connect.index("new WebSocket")]
check("armRejoinTimer()" in code_only(head),
      "connect arms it, so every path that resumes is covered -- the button, "
      "the automatic revive and the backoff, not only the page load")
check('hello.t === "resume"' in code_only(head),
      "and only for a resume: a first join has the join screen in front of "
      "somebody already")
check("!rejoinTimer" in code_only(head),
      "and only if one is not already running, or a backoff that retries "
      "every two seconds would push the deadline out for ever")

# Nothing else should arm it: two arming sites is how one of them gets missed.
# Calls, not the definition, which contains the same characters.
calls = len(re.findall(r"(?<!function )armRejoinTimer\(\)", code_only(app)))
check(calls == 1, "armed in exactly one place, got %d" % calls)

print("\nand it fires on whichever screen somebody is looking at")
timer = code_only(lift("armRejoinTimer"))
check("gate.hidden" not in timer,
      "it does not ask which screen is showing: that check was inverted, and "
      "meant it only ever fired for somebody who was already being asked for "
      "a PIN")
check("askForPin" in timer, "it asks for the PIN, which is the remedy")
check("!ended" in timer,
      "unless the session is over, when there is nothing to rejoin")

limit = re.search(r"const REJOIN_LIMIT_MS = (\d+);", app)
check(bool(limit), "the deadline is stated in one place")
if limit:
    ms = int(limit.group(1))
    check(10000 <= ms <= 40000,
          "and it is long enough for a couple of backoff steps on a slow "
          "network, short enough that nobody is left staring: %d ms" % ms)

print("\nand a resume that lands cancels it")
joined = code_only(lift("joined"))
check("clearRejoinTimer()" in joined,
      "so it only ever fires on a resume that was not answered")
ask = code_only(lift("askForPin"))
check("clearRejoinTimer()" in ask,
      "and asking for the PIN takes its own deadline down")

if not shutil.which("node"):
    print("\nSKIPPED the behaviour: node is not installed")
    sys.exit(1 if fails else 0)

harness = """
'use strict';
const job = JSON.parse(process.argv[1]);
let rejoinTimer = null, ended = job.ended || false;
let armed = 0, asked = null;
const timers = [];
global.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
global.clearTimeout = () => {};
global.askForPin = (why) => { asked = why; };

%(code)s

const realArm = armRejoinTimer;
armRejoinTimer = function () { armed += 1; return realArm.apply(null, arguments); };

// One resume, then the backoff retrying twice under it.
for (let i = 0; i < job.attempts; i++) {
  if (job.t === "resume" && !rejoinTimer) armRejoinTimer();
}
// The deadline comes due.
if (timers.length) timers[0].fn();
console.log(JSON.stringify({ armed, asked, timers: timers.length }));
"""

code = "\n\n".join([re.search(r"const REJOIN_LIMIT_MS = \d+;", app).group(0),
                    lift("clearRejoinTimer"), lift("armRejoinTimer")])


def run(attempts=1, ended=False, t="resume"):
    job = json.dumps({"attempts": attempts, "ended": ended, "t": t})
    done = subprocess.run([shutil.which("node"), "-e", harness % {"code": code}, job],
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(done.stderr[-600:])
    return json.loads(done.stdout)


print("\na resume nobody answers")
out = run(attempts=1)
check(out["asked"] == "That did not get you back in.",
      "ends with the PIN screen rather than 'reconnecting' for ever")

print("\nand three attempts under one deadline")
out = run(attempts=3)
check(out["armed"] == 1,
      "the deadline is set once, not once per retry, got %d" % out["armed"])
check(out["asked"] is not None, "and it still comes due")

print("\nbut not after the session has ended")
out = run(attempts=1, ended=True)
check(out["asked"] is None,
      "there is nothing to rejoin, and the page already says so")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_rejoin: all ok")
