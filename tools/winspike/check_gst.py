"""Whether this Windows machine can do the streaming half at all.

The two things that decide whether Fourth Player can host from Windows are
independent, and this covers the first: GStreamer, its Python bindings, and a
webrtcbin that builds -- plus a desktop source and a hardware encoder to feed
it. check_pad.py covers the second.

Run before porting anything. Every answer here is a fact that would otherwise
be discovered halfway through a rewrite, and the expensive way to find out
that PyGObject will not import on Windows is after moving nine modules behind
a platform layer.

What this does *not* prove: that a guest can connect. It builds the pipeline
and runs it to a fakesink, which is the part that fails on a machine missing
plugins. Negotiating with a browser is the next step and needs the host.

    py check_gst.py            # what is here
    py check_gst.py --run 5    # and actually encode for five seconds
"""
import argparse
import os
import sys

VERDICT = []

# The handles from os.add_dll_directory, kept alive on purpose -- see
# bootstrap(). Dropping them silently undoes the thing they did.
_DLL_DIRS = []


def find_gstreamer():
    """Where GStreamer is, by the installer's own account if possible."""
    # The installer sets this, and it is the only answer that stays right when
    # somebody installs somewhere else. The fallbacks are the per-user path
    # (winget's install is per-user, under AppData, which surprised me) and
    # the machine-wide one.
    for var in ("GSTREAMER_1_0_ROOT_MSVC_X86_64", "GSTREAMER_1_0_ROOT_X86_64"):
        root = os.environ.get(var)
        if root and os.path.isdir(root):
            return root
    for guess in (
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\gstreamer\1.0\msvc_x86_64"),
        r"C:\gstreamer\1.0\msvc_x86_64",
    ):
        if os.path.isdir(guess):
            return guess
    return None


def bootstrap(root):
    """Make this process able to load GStreamer, which PATH no longer does.

    Both halves are needed, and neither is enough. Measured on the Windows
    machine this was written against, GStreamer 1.28.6 and PyGObject 3.50:

        add_dll_directory only   -- "Could not locate gst_init"
        PATH only                -- "DLL load failed while importing _gi"
        both                     -- works

    They are two different loaders. Python 3.8 stopped searching PATH for the
    DLLs an extension module needs, so `_gi` itself needs
    os.add_dll_directory; and GLib's own g_module_open, which is what loads
    gstreamer-1.0-0.dll on behalf of the typelib, does not consult the
    directories that adds -- it wants PATH. Doing one and not the other gives
    two different errors, both of which read like a broken install on a
    machine where everything is present and correct.
    """
    if not root:
        return False
    bindir = os.path.join(root, "bin")
    if os.path.isdir(bindir):
        # The return value is kept, and that is not tidiness. The handle
        # *removes* the directory again when it is closed, and it is closed by
        # being garbage collected -- so `os.add_dll_directory(bindir)` on its
        # own works for exactly as long as it takes the collector to notice,
        # which is usually somewhere between here and the first import that
        # needed it. The failure that follows is "Failed to load shared
        # library 'gstreamer-1.0-0.dll' referenced by the typelib", from a
        # machine where that file is plainly present in that directory.
        _DLL_DIRS.append(os.add_dll_directory(bindir))
        # And PATH, for GLib. See the docstring: this is not belt and braces,
        # it is the other half.
        os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
    # These two are ordinary environment lookups and PATH's change did not
    # touch them, but they have to be right before gi is imported rather than
    # after, so they are set here beside it.
    os.environ.setdefault("GI_TYPELIB_PATH",
                          os.path.join(root, "lib", "girepository-1.0"))
    os.environ.setdefault("GST_PLUGIN_PATH",
                          os.path.join(root, "lib", "gstreamer-1.0"))
    return True


def say(ok, line):
    print(("  ok   " if ok else "  --   ") + line)
    VERDICT.append((ok, line))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=float, default=0,
                    help="seconds to actually capture and encode for")
    args = ap.parse_args()

    print("python")
    say(sys.maxsize > 2 ** 32, "64-bit: %s" % sys.version.split()[0])
    say(sys.platform == "win32",
        "running on Windows: %s" % sys.platform)

    print("\nwhere GStreamer is")
    root = find_gstreamer()
    say(root is not None, "found: %s" % (root or "nowhere this knows to look"))
    say(bootstrap(root),
        "DLL directory added -- PATH does not do this since Python 3.8")

    print("\nthe bindings")
    try:
        import gi
        gi.require_version("Gst", "1.0")
        gi.require_version("GstWebRTC", "1.0")
        from gi.repository import Gst
    except Exception as exc:
        # The likeliest failure on Windows and the one worth being loudest
        # about: pip's PyGObject does not bring GStreamer's typelibs with it.
        # They come from the GStreamer installer (both the runtime *and* the
        # development MSI), and PATH/GI_TYPELIB_PATH have to reach them.
        say(False, "PyGObject with Gst and GstWebRTC: %s" % exc)
        print("\n  This is the one that decides the project.\n"
              "  'DLL load failed while importing _gi' means the bootstrap\n"
              "  above did not find GStreamer -- PATH is not consulted for\n"
              "  this since Python 3.8, only os.add_dll_directory.\n"
              "  A meson error about girepository-2.0 at pip time means a\n"
              "  PyGObject too new for this GStreamer: 3.50.0 is the last\n"
              "  that wants girepository-1.0, and it builds here against\n"
              "  Visual Studio Build Tools.")
        return report()
    say(True, "PyGObject imports, with Gst and GstWebRTC typelibs")

    # An empty list rather than None: this PyGObject refuses None here with
    # "Argument 1 does not allow None as a value", which on first sight looks
    # like the import having failed rather than argv being wrong.
    Gst.init([])
    say(True, "GStreamer %s" % Gst.version_string())

    print("\nthe pieces a host needs")
    # Grouped by the job each does, because a machine missing one of these is
    # missing a different capability rather than being broken outright.
    wanted = {
        "webrtcbin": "the whole guest connection",
        "rtph264pay": "video onto RTP",
        "h264parse": "framing before that",
        "opusenc": "sound",
        "rtpopuspay": "sound onto RTP",
        "appsink": "frames out to Python",
        "appsrc": "frames in to each guest",
    }
    for name, why in wanted.items():
        say(Gst.ElementFactory.find(name) is not None, "%s -- %s" % (name, why))

    print("\ndesktop capture (Linux uses ximagesrc; none of these exist there)")
    sources = ["d3d11screencapturesrc", "dxgiscreencapsrc", "gdiscreencapsrc"]
    found_source = next((s for s in sources if Gst.ElementFactory.find(s)), None)
    for name in sources:
        say(Gst.ElementFactory.find(name) is not None, name)

    print("\nH.264 encoders, best first (Linux uses vah264enc)")
    # The same shape as the ENCODERS table in video.py, which is already
    # "first factory that exists wins" -- so these drop straight into it.
    encoders = ["mfh264enc", "qsvh264enc", "nvh264enc", "d3d11h264enc",
                "openh264enc", "x264enc"]
    found_encoder = next((e for e in encoders if Gst.ElementFactory.find(e)), None)
    for name in encoders:
        say(Gst.ElementFactory.find(name) is not None, name)

    print("\nsound (Linux uses pulsesrc)")
    for name in ["wasapi2src", "wasapisrc", "directsoundsrc"]:
        say(Gst.ElementFactory.find(name) is not None, name)

    if found_source and found_encoder:
        print("\nand whether they actually link")
        # String checks cannot catch this. An encoder can be registered and
        # still refuse the format the source offers, and it fails at run time
        # on a guest's first connection rather than here -- which is exactly
        # the fault that cost an evening on the Linux side.
        line = (f"{found_source} ! videoconvert ! videoscale "
                f"! video/x-raw,format=NV12,width=1280,height=720 "
                f"! {found_encoder} ! h264parse ! rtph264pay ! fakesink")
        print("     " + line)
        try:
            pipe = Gst.parse_launch(line)
        except Exception as exc:
            say(False, "the pipeline parses: %s" % exc)
            return report()
        say(True, "the pipeline parses")
        want = Gst.State.PLAYING if args.run else Gst.State.PAUSED
        ret = pipe.set_state(want)
        if ret == Gst.StateChangeReturn.ASYNC:
            ret, state, _ = pipe.get_state(10 * Gst.SECOND)
        say(ret != Gst.StateChangeReturn.FAILURE,
            "it negotiates caps and reaches %s" % want.value_nick)
        if args.run and ret != Gst.StateChangeReturn.FAILURE:
            import time
            print("     encoding for %.0fs..." % args.run)
            time.sleep(args.run)
            say(True, "it ran without falling over")
        pipe.set_state(Gst.State.NULL)

    return report()


def report():
    print()
    missing = [line for ok, line in VERDICT if not ok]
    if not missing:
        print("all good: this machine has everything the streaming half needs.")
        return 0
    print("%d thing(s) not found:" % len(missing))
    for line in missing:
        print("  " + line)
    print("\nSome of these are alternatives rather than requirements -- one\n"
          "desktop source and one encoder is enough. webrtcbin is not\n"
          "optional.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
