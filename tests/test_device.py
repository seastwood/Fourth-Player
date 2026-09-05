"""A remembered device travels on every knock, not on the ones somebody edited.

The bug this exists for: the device token was added to the two reconnect paths
and not to the one on the load path -- which is the only path that runs when
somebody closes the app and opens it again, so the case the whole feature was
built for was the case it missed.

The fix was to stop patching call sites. connect() is the single funnel every
join and resume goes through, so the token goes on there. This suite reads
app.js and holds that down: every connect() call site is found, and none of
them is allowed to be the one that carries its own.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


app = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()


def strip_comments(text):
    """Line and block comments out, so a rule is never met by a comment.

    Left to right in one pass, because a `//` inside a string is not a
    comment and a quote inside a comment is not a string -- which is how an
    earlier version of this trick swallowed 64k of code.
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                if text[i] == "\\":
                    out.append(text[i:i + 2])
                    i += 2
                    continue
                out.append(text[i])
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


code = strip_comments(app)

print("one funnel, and it is the one that carries the token")
funnel = code.split("function connect(hello)")[1].split("\nfunction ")[0]
check("savedDevice()" in funnel,
      "connect() reaches for the remembered device itself")
check("hello.device = device" in funnel,
      "and puts it on the message it is about to send")
check("!hello.login" in funnel,
      "unless the caller is already sending a password, which wins")

print("\nevery way in goes through it")
# Two shapes: an object written out at the call, and a variable built up
# first -- which is what the gate does, because it may add a password to it.
inline = re.findall(r"connect\(\s*\{[^;]*?\}\s*\)", code, re.S)
byname = re.findall(r"connect\(\s*([A-Za-z_$][\w$]*)\s*\)", code)
check(len(inline) + len(byname) >= 4,
      "found %d places that open a connection (%d written out, %d built up)"
      % (len(inline) + len(byname), len(inline), len(byname)))
kinds = {re.search(r't:\s*"(\w+)"', s).group(1) for s in inline
         if re.search(r't:\s*"(\w+)"', s)}
check("resume" in kinds, "a resume is among them: %s" % sorted(kinds))
# The gate's join, found through the variable it hands over rather than by
# reading the object literally.
for held in byname:
    if held == "hello":
        continue                        # connect's own parameter
    built = code.split("connect(" + held + ")")[0]
    check('t: "join"' in built or '"t": "join"' in built,
          "the message handed over as %r is a join" % held)
    check("device" not in built.split(held + " = {")[-1].split("};")[0],
          "and it does not carry a device token of its own")
for site in inline:
    one = " ".join(site.split())
    check("device" not in site,
          "no call site written out carries its own token: %s" % one[:64])

print("\nand the load path is one of them")
# The path that runs when the app is closed and reopened. Named explicitly,
# because it is the one that was missed and the one that matters most.
load = code.split("localStorage.getItem(credKey())")[1]
check(re.search(r'connect\(\s*\{\s*t:\s*"resume"', load),
      "reopening the app resumes on the token it saved")
check("device" not in load.split("connect(")[1].split(")")[0],
      "and does not name a device itself, so it cannot be the one left out")

print("\nthe token is kept and dropped in one place each")
check(code.count("localStorage.setItem(DEVICE_KEY") == 1,
      "one place writes it")
check(code.count("localStorage.removeItem(DEVICE_KEY") == 1,
      "one place forgets it")
check('rememberDevice("")' in code,
      "and logging out forgets it, rather than re-offering a device the "
      "person has just signed out of")

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
