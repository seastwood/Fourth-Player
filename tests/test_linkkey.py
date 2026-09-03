"""What somebody actually pastes into the box, and what has to come out of it.

The link key is 43 characters of base64url. Nobody types that off a
television, so the field on the plain address is a place to *paste* into --
and what gets pasted is whatever the message it arrived in looked like: the
whole address, sometimes with a full stop after it, sometimes wrapped in angle
brackets by a mail client, sometimes with the tracking rubbish a chat app adds.
All of it has to reduce to the key, because a key with a full stop on the end
is refused by the host exactly as loudly as a wrong one, and the guest cannot
see the difference.

The one answer that must never be a guess is the plain address with no key in
it at all. Splitting that on "/" gives "https", and sending "https" as the key
gets a refusal that reads as if the host had changed the link -- when what
really happened is that this guest was never given one.

`keyFrom` is read out of app.js and run under node, the way test_webframe.py
runs frame.js: the browser half of this repository is only worth testing where
it is a pure function of its input, and this is that.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "web", "app.js")

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the browser half cannot be run.")
    print("         Install nodejs to check what the link box makes of a paste.")
    sys.exit(0)

source = open(APP).read()
start = source.find("function keyFrom(")
end = source.find("\n}", start) + 2
check(start > 0 and end > start, "keyFrom is in app.js to be read out")
if start < 0:
    sys.exit(1)

HOST = "https://fourthplayer.example.com"
KEY = "ExMmXooaduRGAkYKxPXDmj3YVIUrQ56Wb2360V__yQk"

CASES = [
    ("the key on its own", KEY, KEY),
    ("the whole link", HOST + "/j/" + KEY, KEY),
    ("with a trailing slash", HOST + "/j/" + KEY + "/", KEY),
    ("with something added after it", HOST + "/j/" + KEY + "?utm=chat", KEY),
    ("with a fragment", HOST + "/j/" + KEY + "#play", KEY),
    ("with the whitespace a paste brings", "  " + HOST + "/j/" + KEY + "\n", KEY),
    ("out of a sentence", "join here: " + HOST + "/j/" + KEY + ".", KEY),
    ("wrapped by a mail client", "<" + HOST + "/j/" + KEY + ">", KEY),
    ("percent-encoded on the way", HOST + "/j/AB%2DCD", "AB-CD"),
    ("http rather than https", "http://box.local:8443/j/" + KEY, KEY),
    ("nothing at all", "", ""),
    ("blank space", "   ", ""),
    ("the plain address, which carries no key", HOST + "/", ""),
    ("the plain address without its slash", HOST, ""),
]

harness = """
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const start = source.indexOf("function keyFrom(");
const end = source.indexOf("\\n}", start) + 2;
eval(source.slice(start, end));
const cases = JSON.parse(process.argv[2]);
console.log(JSON.stringify(cases.map((c) => keyFrom(c))));
"""

inputs = [given for _, given, _ in CASES]
result = subprocess.run([node, "-e", harness, "--", APP, json.dumps(inputs)],
                        capture_output=True, text=True)
if result.returncode != 0:
    print("  FAIL  node could not run keyFrom:\n" + result.stderr)
    sys.exit(1)

got = json.loads(result.stdout)
for (label, given, want), answer in zip(CASES, got):
    check(answer == want,
          "%s: %r -> %r%s" % (label, given[:60], answer,
                              "" if answer == want else " (wanted %r)" % want))

# Null and undefined reach this from an input element that is not on the page.
result = subprocess.run(
    [node, "-e", harness.replace("cases.map((c) => keyFrom(c))",
                                 "[keyFrom(null), keyFrom(undefined)]"),
     "--", APP, "[]"],
    capture_output=True, text=True)
check(result.returncode == 0 and json.loads(result.stdout or "[]") == ["", ""],
      "nothing at all does not throw: %s" % (result.stdout.strip() or result.stderr))

print(("FAILED: %d" % len(fails)) if fails else "test_linkkey: all ok")
sys.exit(1 if fails else 0)
