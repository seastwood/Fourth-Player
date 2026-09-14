"""fourth-player -- browser guests join a game on this machine with real pads.

Four modules do the work and are deliberately separable, because three of them
can be tested without a GPU, a network or a browser:

    protocol   the wire format for one pad's state, and nothing else
    pads       kernel-level virtual gamepads, one per guest
    invites    who is allowed in, for how long, and how that is revoked
    video      the one GStreamer pipeline everybody watches

`session` ties them together and `signalling` is the socket they arrive on.
"""

__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# Windows needs to be told where GStreamer is before anything imports `gi`,
# and this is the only place guaranteed to run first.
#
# It takes two separate things and neither is enough on its own -- they are two
# different loaders. Python 3.8 stopped consulting PATH for the DLLs an
# extension module needs, so `_gi` itself needs os.add_dll_directory; and
# GLib's own g_module_open, which loads gstreamer-1.0-0.dll on behalf of the
# typelib, ignores what that adds and wants PATH. Doing one and not the other
# gives two different errors -- "DLL load failed while importing _gi" and
# "Could not locate gst_init" -- and both read like a broken install on a
# machine where every file is present and correct. Measured; see
# tools/winspike/README.md.
#
# The handle from add_dll_directory is kept for the life of the process on
# purpose: closing it removes the directory again, and it is closed by being
# garbage collected.
def _find_gstreamer():
    import os
    for var in ("GSTREAMER_1_0_ROOT_MSVC_X86_64", "GSTREAMER_1_0_ROOT_X86_64"):
        root = os.environ.get(var)
        if root and os.path.isdir(root):
            return root
    # winget installs per-user, under AppData, which is not where the
    # documentation suggests looking.
    for guess in (os.path.expandvars(
                      r"%LOCALAPPDATA%\Programs\gstreamer\1.0\msvc_x86_64"),
                  r"C:\gstreamer\1.0\msvc_x86_64"):
        if os.path.isdir(guess):
            return guess
    return None


_dll_dirs = []

if __import__("sys").platform == "win32":          # pragma: no cover - Windows
    import os as _os
    _root = _find_gstreamer()
    if _root:
        _bin = _os.path.join(_root, "bin")
        if _os.path.isdir(_bin):
            _dll_dirs.append(_os.add_dll_directory(_bin))
            _os.environ["PATH"] = _bin + _os.pathsep + _os.environ.get("PATH", "")
        _os.environ.setdefault(
            "GI_TYPELIB_PATH", _os.path.join(_root, "lib", "girepository-1.0"))
        _os.environ.setdefault(
            "GST_PLUGIN_PATH", _os.path.join(_root, "lib", "gstreamer-1.0"))
