# Hosting from Windows 11 — the spike

Fourth Player hosts from Linux. Whether it can host from Windows comes down to
two questions that have nothing to do with each other, and both are cheaper to
answer now than halfway through a port:

1. **Can this machine stream?** GStreamer, its Python bindings and a
   `webrtcbin` that builds, plus a desktop source and a hardware encoder.
   → `check_gst.py`
2. **Can this machine give a guest a controller?** Windows has no `/dev/uinput`,
   so this needs a kernel driver.
   → `check_pad.py`

Neither script touches the Fourth Player codebase. They are here to be run on a
Windows machine and to print facts.

## Setting the machine up

Run these in an **administrator** PowerShell.

```powershell
# 1. Python. The Store build works; so does python.org. 64-bit.
winget install Python.Python.3.12

# 2. GStreamer. BOTH packages -- the runtime alone has no typelibs, and
#    without those PyGObject imports and then cannot find Gst.
winget install GStreamer.GStreamer
winget install GStreamer.GStreamer.Development

# 3. The bindings.
py -m pip install PyGObject

# 4. The virtual gamepad driver. Prompts for the ViGEmBus install; that
#    prompt is the whole point -- a signed kernel driver is what Windows
#    has instead of uinput.
py -m pip install vgamepad
```

If `check_gst.py` says PyGObject cannot find `Gst`, it is almost always the
environment rather than the install:

```powershell
$env:PATH = "C:\gstreamer\1.0\msvc_x86_64\bin;$env:PATH"
$env:GI_TYPELIB_PATH = "C:\gstreamer\1.0\msvc_x86_64\lib\girepository-1.0"
```

## Running it

```powershell
py check_gst.py             # what is here
py check_gst.py --run 5     # and prove it encodes rather than merely links
py check_pad.py             # one pad
py check_pad.py --pads 4 --seconds 30
```

While `check_pad.py` runs, open **joy.cpl** and watch. Then open a game, which
is the only test that counts.

## What the spike found, 2026-09-14

Run against a Ryzen 7 5700G with an RTX 4070 Ti, Windows 11 build 26200,
GStreamer 1.28.6, Python 3.13.5.

**The streaming half works.** `webrtcbin`, `d3d11screencapturesrc`,
`wasapi2src`, `opusenc`, `appsrc`/`appsink` are all present, and a capture ->
encode pipeline ran 120 real frames through four different paths. Nothing here
is a blocker.

Four things cost time and will cost it again on the next machine:

1. **There is no PyGObject wheel for Windows, and the current one will not
   build.** `pip install PyGObject` fails with a meson error about
   `girepository-2.0`, which GStreamer 1.28's bundle does not ship -- it has
   `gobject-introspection-1.0`. **`PyGObject==3.50.0` is the last version that
   wants the old one**, and it builds in about a minute against the Visual
   Studio Build Tools. Without a compiler on the machine there is no route at
   all short of MSYS2.

2. **Loading the DLLs needs two separate things, and neither alone is enough.**

   | | result |
   |---|---|
   | `os.add_dll_directory(bin)` only | `Could not locate gst_init` |
   | `PATH` only | `DLL load failed while importing _gi` |
   | both | works |

   They are two different loaders: Python 3.8 stopped consulting `PATH` for an
   extension module's DLLs, so `_gi` needs `add_dll_directory`; and GLib's own
   `g_module_open`, which loads `gstreamer-1.0-0.dll` on the typelib's behalf,
   ignores what that adds and wants `PATH`. Each on its own gives a different
   error, and both read like a broken install on a machine where every file is
   present.

3. **`Gst.init(None)` raises here.** "Argument 1 does not allow None as a
   value" -- pass `[]`. It looks like a failed import until you read it.

4. **Capture only works in the interactive session.** Over SSH the process
   lands in session 0, which has one dummy 1024x768 display and no desktop:
   `d3d11screencapturesrc` fails with "Failed to prepare capture object". The
   same pipeline run as a scheduled task with `-LogonType Interactive` in
   session 1 plays immediately. This is a real constraint on how a Windows host
   is launched, not a quirk of testing -- the Linux side has the same shape of
   requirement in needing an X display.

**The encoder to use is `nvd3d11h264enc`**, not `nvh264enc`. This build has no
`cudaupload`/`cudaconvertscale` at all, so the CUDA path is not available on
Windows; `nvd3d11h264enc` is NVENC in Direct3D11 mode and takes the capture's
frames without leaving the GPU. Measured, 120 frames at 1280x720:

| path | fps |
|---|---|
| `d3d11screencapturesrc ! nvd3d11h264enc` | 30.3 |
| `! d3d11convert ! nvautogpuh264enc` | 29.9 |
| `! d3d11convert ! nvd3d11h264enc` | 29.7 |
| `! videoconvert ! videoscale ! nvh264enc` | 28.6 |
| `! videoconvert ! videoscale ! mfh264enc` | 29.1 |

~30 is the capture rate on a static desktop rather than an encoder limit, so
these say "all of them keep up" rather than ranking them.

**The pad half works, with one hard ceiling.** ViGEmBus drove six virtual
pads happily -- and **XInput reported four of them**, because XInput has four
slots and that is that. Most Windows games use XInput, so:

> **A Windows host can seat four guests at most, and every controller
> physically plugged into it takes one of the same four.**

Linux has no such limit: `uinput` will make as many devices as asked. This has
to be said in the UI rather than discovered by the fifth person to join.

Two things the pad half does *not* need: the interactive session (ViGEm
devices created from session 0 are visible system-wide), and any elevation
beyond installing the driver once.

Still unanswered, and only a person at the machine can answer them: whether a
*game* binds these pads, and what a game that is already running does when one
appears. The Linux side has a known fault there -- a service restart re-binds
the pads and the running game keeps the old ones -- and it would be worth
knowing whether Windows behaves the same before building the seating model on
top of it.

## What the answers decide

| finding | what it means |
|---|---|
| no `webrtcbin` | stop; there is no project without it |
| no `d3d11screencapturesrc` | fall back to `dxgiscreencapsrc`, or capture on the CPU and accept the cost |
| no hardware encoder | `x264enc` works and 1080p in software will flatten an old laptop -- the same cap the Linux side applies would be needed |
| ViGEm refuses the fifth pad | XInput's limit of four is a real ceiling on guests, and the seating model has to say so |
| a running game ignores a new pad | the same fault the Linux side has, where a restart re-binds the pads mid-game -- worth knowing it is not Linux-specific |

## What is already portable

More than it looks. `ENCODERS` and `pick_encoder()` in `fourthplayer/video.py`
are already a "first factory that exists wins" table, so `mfh264enc` and
`qsvh264enc` drop in beside the VA and NVENC entries with no new machinery --
and `nvh264enc` is in there already. The protocol, the web UI, the invites, the
session and the seating logic are all platform-agnostic.

What is not: `pads.py` and `desk.py` are built on `evdev` and `uinput`, and
`keymap.py` and `retroarch.py` import `evdev` at module scope -- so on Windows
the package does not merely misbehave, it fails at import. A platform layer
behind which the two implementations sit is the first structural job, and it is
worth doing only once these two scripts have said the project is possible.
