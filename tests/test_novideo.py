"""An SDP answer with no video is a prediction, not a verdict.

`m=video 0` in an answer reads as "this browser cannot take H.264", and on a
first offer it usually is. On a renegotiation it is not: Safari on iOS answers
port 0 on a re-offer while the picture it already has carries on playing.

Both ends acted on it immediately, and both were wrong in the same way. The
page put a notice over a working stream saying the video could not start --
which reads as nonsense to anybody who knows Safari does H.264. The host freed
the slot five seconds later, which unplugged the guest's pad and took the
controller out of a Steam game that had already bound it.

So both wait and then look at what actually happened.
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


server = open(os.path.join(ROOT, "fourthplayer", "server.py"), encoding="utf-8").read()
app = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()


def strip_comments(text):
    """So a rule is never satisfied by a comment that describes it."""
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                if text[i] == "\\":
                    out.append(text[i:i + 2]); i += 2; continue
                out.append(text[i])
                if text[i] == quote:
                    i += 1; break
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
host = re.sub(r"(?m)^\s*#.*$", "", server)

print("the host waits, then looks")
check("_free_if_no_video" in host, "the drop goes through one named check")
free = host.split("def _free_if_no_video")[1].split("\n    @staticmethod")[0]
check("has_media()" in free,
      "which asks whether the guest has media before letting the slot go")
check("return" in free.split("has_media()")[1].split("log.warning")[0],
      "and returns without dropping them when they have")
answer = host.split('re.search(r"^m=video 0[ ]", sdp, re.M)')[1].split("elif kind")[0]
check("self.session.drop(" not in answer,
      "the answer handler does not drop anybody itself")
check("call_later" in answer, "it waits first")

print("\nthe page waits, then looks")
refused = code.split("function videoRefused()")[1].split("\nfunction ")[0]
check("setTimeout" in refused, "the notice is not put up at once")
check("pictureIsShowing()" in refused,
      "and not put up at all if there is a picture on the screen")
check("showNotice" in refused, "when there really is no picture, it still says so")
arrived = code.split("function videoArrived()")[1].split("\nfunction ")[0]
check("clearTimeout" in arrived, "a picture arriving cancels the pending notice")
check("hideNotice" in arrived, "and takes down one that is already up")
check("videoWidth" in code.split("function pictureIsShowing()")[1].split("\nfunction ")[0],
      "a picture is decided by frames decoded, not by a connection state")

print("\nand the picture arriving is actually wired to something")
for event in ("loadedmetadata", "resize", "playing"):
    check('video.addEventListener("%s", videoArrived)' % event in code,
          "the %s event clears it" % event)

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
