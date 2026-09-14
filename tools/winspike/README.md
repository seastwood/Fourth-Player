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
