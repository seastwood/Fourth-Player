"""A GObject assertion is a log line, not a dead process.

The host segfaults about once a day on the console. The last five cores all
die in the same place, and it is not where the bug is -- it is where the
*complaint about* the bug is handled:

    Python -> _gi -> g_object_get_qdata      <- on an object already freed
           -> g_log "assertion 'G_IS_OBJECT (object)' failed"
           -> _gi's log handler -> PyErr_WarnEx
           -> crash in PyObject_GC_UnTrack

PyGObject installs a handler for GLib's logging and turns a warning into a
Python warning. Raising one means allocating, filtering and possibly
collecting, on whichever thread GStreamer happened to be on -- and doing that
on a heap a use-after-free has already disturbed is what turns a survivable
complaint into SIGSEGV.

So the domains are handed to GLib's own C handler. The assertion still goes to
stderr, which systemd keeps, so the signal that the underlying bug happened is
not lost. **This does not fix the use-after-free** and is not claimed to: it
removes one of the two places the corruption has been seen to kill the
process.

This runs where GStreamer is, which is the consoles and not the laptop.
"""
import os
import sys
import warnings

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    from fourthplayer import video
    from gi.repository import GLib, GObject
except Exception as exc:                                  # noqa: BLE001
    print("SKIPPED: no GStreamer here (%s)" % exc)
    sys.exit(0)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def provoke():
    """A real GLib-GObject assertion, of the same family as the crash."""
    GObject.Object().disconnect(999999)


def warnings_from(fn):
    got = []
    keep = warnings.showwarning
    warnings.showwarning = (
        lambda m, c, f, l, file=None, line=None: got.append(str(m)))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            fn()
    finally:
        warnings.showwarning = keep
    return got


print("-- the knob exists on this PyGObject --")
check(hasattr(GLib, "log_set_handler"),
      "GLib.log_set_handler is available, which is what takes the domain back")
check(hasattr(GLib, "log_default_handler"),
      "and GLib's own C handler can be named as the replacement")

print("\n-- before: PyGObject routes it through Python --")
# Deliberately put PyGObject's handler back, so this suite measures the change
# rather than whatever a previous test left behind.
video._initialised = False
os.environ["FOURTH_PLAYER_PYTHON_GLIB_WARNINGS"] = "1"
video.init()
through_python = warnings_from(provoke)
check(through_python,
      "with the escape hatch set, the assertion arrives as a Python warning: "
      "%r" % (through_python[:1] or None))
check(any("handler with id" in w for w in through_python),
      "and it is the assertion text, not something else")

print("\n-- after: it goes to GLib's own handler instead --")
del os.environ["FOURTH_PLAYER_PYTHON_GLIB_WARNINGS"]
video._initialised = False
video.init()
through_glib = warnings_from(provoke)
check(not through_glib,
      "no Python warning is raised, so PyErr_WarnEx is never reached: got %r"
      % (through_glib or None))

print("\n-- and the process is still usable afterwards --")
# The point of the whole thing: the complaint must not end the session.
pipeline = video.Gst.parse_launch("fakesrc num-buffers=2 ! fakesink")
pipeline.set_state(video.Gst.State.PLAYING)
state = pipeline.get_state(video.Gst.SECOND)[1].value_nick
pipeline.set_state(video.Gst.State.NULL)
check(state == "playing",
      "a pipeline still builds and runs after the assertion, got %r" % state)
provoke()
check(True, "and provoking it again does not end this test run")

print("\n-- the escape hatch is documented where it is read --")
source = open(os.path.join(ROOT, "fourthplayer", "video.py")).read()
check("FOURTH_PLAYER_PYTHON_GLIB_WARNINGS" in source, "the variable is named")
check("does not fix the use-after-free" in source,
      "and the comment says plainly that this is not the fix, so nobody reads "
      "the absence of crashes as the bug being gone")

print()
if fails:
    print("FAILURES: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_glibwarn: all ok")
