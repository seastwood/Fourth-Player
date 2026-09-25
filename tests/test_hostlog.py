"""The tray's log for the host, and the rename that must not silence it.

Worth its own file because of how this failed. The tray starts the host fresh
once its log is big enough to be unhelpful, by renaming the old one aside.
On Windows the host that has just been killed keeps the file open for a
moment, so the rename raises WinError 32 -- and rotating and opening shared
one try, so a failed *rename* returned None and the host ran with its output
discarded.

It said so once, in the tray's own log, and then ran for four hours saying
nothing anywhere. A stream was reported broken inside that window and there
was not one line to read. Rotation is a convenience; the log is the point,
and the two are separate attempts now.
"""
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from fourthplayer import tray as traylib

bad = 0


def check(ok, what):
    global bad
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        bad += 1


class Stub(traylib.Host):
    """Just the log half. Building a real one wants a tray and a desktop."""

    def __init__(self, where):
        self.where = where

    def log_path(self):
        return self.where


def big(path, size):
    with io.open(path, "wb") as handle:
        handle.write(b"x" * size)


with tempfile.TemporaryDirectory() as room:
    path = os.path.join(room, "host.log")

    print("a log that is small enough is simply appended to")
    big(path, 64)
    host = Stub(path)
    handle = host._open_log()
    check(handle is not None, "there is somewhere for the host to write")
    handle.write(b"hello\n")
    handle.close()
    check(io.open(path, "rb").read().endswith(b"hello\n"),
          "and what it writes lands in the file")
    check(not os.path.exists(path + ".1"), "with nothing rotated aside")

    print("\nand one that has grown too big is started fresh")
    big(path, traylib.Host.LOG_LIMIT + 1)
    handle = Stub(path)._open_log()
    check(handle is not None, "there is still somewhere to write")
    handle.close()
    check(os.path.exists(path + ".1"), "the old one is kept as .1")
    check(os.path.getsize(path) == 0, "and the new one starts empty")

    print("\nbut a rename that fails costs a big file and nothing more")
    # The Windows fault, reproduced by the one thing that is true on every
    # platform: os.replace raising. Whether the real cause is a held handle,
    # a permission or a full disk does not matter -- what matters is that the
    # host still gets a log.
    big(path, traylib.Host.LOG_LIMIT + 1)
    real = os.replace

    def refuse(src, dst):
        raise OSError(32, "The process cannot access the file because it is "
                          "being used by another process")

    os.replace = refuse
    try:
        handle = Stub(path)._open_log()
    finally:
        os.replace = real
    check(handle is not None,
          "the host is NOT left with its output discarded -- the whole fault")
    if handle:
        handle.write(b"still here\n")
        handle.close()
    check(io.open(path, "rb").read().endswith(b"still here\n"),
          "and what it says is appended to the log that could not be moved")

    print("\nand somewhere that cannot be written at all is reported")
    handle = Stub(os.path.join(path, "no", "such", "place"))._open_log()
    check(handle is None, "there is nothing to hand back")

print("\n%d FAILED" % bad if bad else "\nall ok")
sys.exit(1 if bad else 0)
