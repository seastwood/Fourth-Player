"""The lock survives a restart, because this host restarts on its own.

Who may be here is a decision the owner made about this session. It lived only
in memory, so every restart quietly opened the door again -- and the video
encoder on this console segfaults several times a day, so "I set it to only me
and somehow it went back to anybody with the link" needed no explanation
beyond that.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


try:
    from fourthplayer import session as sessionlib
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)

folder = tempfile.mkdtemp(prefix="fp-lock-")
sessionlib.STATE_PATH = os.path.join(folder, "state.json")

print("what is written down")
source = open(os.path.join(ROOT, "fourthplayer", "session.py"),
              encoding="utf-8").read()
save = source.split("    def save(self):")[1].split("\n    @staticmethod")[0]
for key in ("locked", "allowed", "max_guests"):
    check('snapshot["%s"]' % key in save, "save() writes %s down" % key)

print("\nand written the moment it is set")
# The snapshot is otherwise only rewritten when somebody joins or leaves, so a
# lock set on a quiet session was still only in memory when the encoder next
# fell over and took it with it.
locking = source.split("    def set_locked(self")[1].split("\n    def ")[0]
check("self.save()" in locking, "set_locked writes the snapshot")
limiting = source.split("    def set_limit(self")[1].split("\n    def ")[0]
check("self.save()" in limiting, "and so does set_limit")

print("\nand read back")
json.dump({"locked": "named", "allowed": ["seth"], "max_guests": 2,
           "expires_in": 3600, "saved_at": 0},
          open(sessionlib.STATE_PATH, "w"))
keep = sessionlib.LiveSession.saved_limits()
check(keep["locked"] == "named", "the lock comes back: %r" % keep)
check(list(keep["allowed"]) == ["seth"], "and who was named")
check(keep["max_guests"] == 2, "and the connection limit")

print("\na snapshot from before any of this still restores")
json.dump({"expires_in": 3600, "saved_at": 0}, open(sessionlib.STATE_PATH, "w"))
keep = sessionlib.LiveSession.saved_limits()
check(keep["locked"] == "", "no lock is simply no lock: %r" % keep)
check(keep["max_guests"] is None, "and no limit is no limit")

print("\nand a file that is not there at all")
os.unlink(sessionlib.STATE_PATH)
check(sessionlib.LiveSession.saved_limits() == {},
      "reads as nothing rather than raising")

print("\nthe restore puts it back")
server = open(os.path.join(ROOT, "fourthplayer", "server.py"),
              encoding="utf-8").read()
restore = server.split("restored the session that was open before")[0]
check("saved_limits()" in restore,
      "the restore asks for the saved lock before saying it restored anything")
check("session.locked = keep" in restore, "and puts it back on the session")

import shutil
shutil.rmtree(folder, ignore_errors=True)
print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
