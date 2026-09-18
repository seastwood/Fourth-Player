"""A host that survived a deploy is replaced, not adopted.

This is the bug that cost a whole round trip and looked like nothing at all.

On Windows the tray is the supervisor, and `schtasks /End` ends the tray
without ending the host it started. So a restart left the old host orphaned
and still answering on the port; the new tray asked "is one running?", was
told yes, and adopted it. Every outward sign of a successful deploy was
there -- git pulled, the task restarted, the log kept moving -- and the code
being served was three commits old.

It surfaced only because the browser had been updated and the host had not, so
the two disagreed about a message format and the browser started reporting
frames with holes in them: "it ended after 1 of 256", where 256 is what you
get reading two bytes of an H.264 start code as a big-endian count. Without
that coincidence the host would still be behind.

So the host says which build it loaded and the tray compares.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from fourthplayer import build as build_module
from fourthplayer import tray as tray_module

fails = 0


def check(ok, what):
    global fails
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        fails += 1


print("the stamp says what is on disk, and does not change on its own")
first = build_module.stamp()
check(bool(first) and isinstance(first, str), "it is a string: " + first)
check(build_module.stamp() == first, "asking twice gives the same answer")
check(build_module.LOADED == first,
      "and what this process loaded is what is on disk, having just loaded it")

print("touching a file changes it, which is the whole point")
mark = os.path.join(ROOT, "web", "app.js")
was = os.stat(mark).st_mtime
try:
    os.utime(mark, (was + 10000, was + 10000))
    check(build_module.stamp() != first,
          "a newer file in web/ is a different build, because guests are "
          "served those files too")
finally:
    os.utime(mark, (was, was))
check(build_module.stamp() == first, "and putting it back puts the stamp back")

print("a tray that finds a host of another build replaces it")
asked = []
stopped = []


def host_saying(what):
    it = tray_module.Host(launch=True)
    it.unit = None
    it.child = None
    it.stop = lambda: (stopped.append(True), True)[1]
    it.reachable = lambda: True
    return it


def answering(reply):
    def _ask(request):
        asked.append(request)
        return reply
    return _ask


was_ask = tray_module._ask
try:
    tray_module._ask = answering({"ok": True, "build": "something-else"})
    host = host_saying("something-else")
    check(host.stale(), "a build that is not this one is stale")

    tray_module._ask = answering({"ok": True, "build": build_module.stamp()})
    check(not host.stale(), "and the same build is not")

    # A host too old to say anything is by definition not this build: the
    # field was added by the commit that fixed this, so its absence dates it.
    tray_module._ask = answering({"ok": True})
    check(host.stale(), "a host too old to say which build it is counts as stale")

    # Nothing answering is not a stale host, it is no host, and start() has
    # its own path for that.
    tray_module._ask = answering({"ok": False, "error": "no server is running"})
    check(not host.stale(), "and nothing answering at all is not stale")

    print("and start() acts on it rather than saying 'already running'")
    tray_module._ask = answering({"ok": True, "build": "something-else"})
    del stopped[:]
    started = []
    host._real_start = host.start
    # Stop the real launch after the decision, which is the part being tested.
    host.reachable = lambda: bool(started) or not stopped
    ok, why = tray_module.Host.start(host)
    check(bool(stopped), "the old host was stopped")

    tray_module._ask = answering({"ok": True, "build": build_module.stamp()})
    del stopped[:]
    host.reachable = lambda: True
    ok, why = tray_module.Host.start(host)
    check(ok and why == "already running" and not stopped,
          "while a host of this build is adopted as before: " + str(why))
finally:
    tray_module._ask = was_ask

print("and the host tells anybody who asks")
server = open(os.path.join(ROOT, "fourthplayer", "server.py"),
              encoding="utf-8").read()
check(server.count('"build": build.LOADED') == 2,
      "in both replies, open and closed -- a host with no session is exactly "
      "the one a deploy finds")
check("build.LOADED" in server and "build.stamp()" not in server,
      "and it reports what it loaded, never what is on disk now, which is the "
      "one thing that would make this useless")

print("FAILED" if fails else "PASSED")
sys.exit(1 if fails else 0)
