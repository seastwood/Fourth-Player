"""The one pipeline everybody watches, and one WebRTC peer per guest.

The shape of this file is a single decision: the screen is captured and encoded
**once**, and a `tee` hands the same encoded bytes to every guest. A fourth
guest therefore costs bandwidth and nothing else -- no extra capture, no extra
encode. On the machine this was written for that is not an optimisation, it is
the difference between working and not: measured there, 720p60 through the
Radeon's encoder runs at about 54 fps and a second concurrent encode does not
fit.

What it costs is per-guest adaptation. Everyone shares one bitrate ladder, so a
guest on a bad connection cannot be given a smaller picture without encoding a
second stream. The queue feeding each peer is therefore leaky: a slow guest
drops frames and stays live rather than stalling and dragging the pipeline down
with them.

GStreamer wants a GLib main loop and the rest of the program is asyncio, so the
loop runs on its own thread and every callback is marshalled back with
`call_soon_threadsafe`. Nothing in this module touches session state directly.
"""

import collections
import concurrent.futures
import json
import logging
import os
import re
import struct
import threading
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst, GstWebRTC, GstSdp, GstVideo, GLib, GObject  # noqa: E402

from . import micsink, net, screen, vdisplay, windesktop  # noqa: E402

log = logging.getLogger("fourthplayer.video")

# Long enough for a cold VAAPI encoder on slow hardware, short enough that a
# genuinely broken pipeline is reported rather than hung on.
START_TIMEOUT = 10 * Gst.SECOND if hasattr(Gst, "SECOND") else 10_000_000_000

# How long to hold a detached peer pipeline while it finds its way to NULL.
# Generous on purpose: nothing is waiting on it (see Peer.detach), and
# the only thing a short wait would buy is giving up on a teardown that was
# about to finish and calling it a leak.
TEARDOWN_TIMEOUT = 10 * Gst.SECOND if hasattr(Gst, "SECOND") else 10_000_000_000

# How long the capture keeps running after the last guest leaves.
#
# It used to keep running for ever. The pipeline follows the *session*, not
# the guests -- session.py starts it when a session opens and stops it when
# the session closes -- so a console with a session that never expires
# captured and encoded its screen around the clock for nobody. Measured on two
# machines with zero guests connected: 47% and 60% of a core, continuously,
# and fifteen hours of CPU between restarts. On a games console that is taken
# straight out of the emulator.
#
# Paused rather than stopped. stop() is one-way -- it takes the pipeline to
# NULL and the Stage is rebuilt rather than restarted -- and it is the path
# that has to destroy webrtcbin, which is delicate enough to have its own
# timeout above. PAUSED costs nothing while idle, keeps every element built,
# and add_peer already calls ensure_playing() before attaching anybody, so
# waking up was written long before this.
#
# Not instant, because a guest who reloads is back within milliseconds and
# pausing between the two would add a state change to every reload.
IDLE_AFTER = 30.0

# And off by default, because PAUSED is not reliably reversible.
#
# The saving is real and large -- 47% of a core to nothing, measured on the
# console -- and it was shipped on the strength of a round trip tested in
# isolation: ximagesrc into vah264enc on Linux, nvd3d11h265enc and wasapi2src
# on Windows, all of which paused and resumed happily. The real pipeline is
# not those. On Windows the very next guest to join got:
#
#     pipeline is paused; putting it back to PLAYING
#     pipeline would not return to PLAYING (paused)
#     timed out attaching a peer for seth; freeing the slot
#
# d3d11screencapturesrc does not come back, and the one thing that could not
# be tested over ssh -- a screen capture needs a desktop session, and ssh
# lands in session 0 -- is the one thing that was wrong. So nobody could
# connect at all, which is a far worse fault than an idle encoder.
#
# Kept, switched off, and opt-in while it is worth another attempt: the fix is
# not a longer timeout but a different idle strategy, because a source that
# will not resume will not resume.
IDLE_CAPTURE = os.environ.get("FOURTH_PLAYER_IDLE_CAPTURE") == "1"

_initialised = False


# Let a GObject assertion be a log line instead of a dead process.
#
# PyGObject installs its own handler for GLib's logging and turns a warning
# into a *Python* warning, through PyErr_WarnEx. That is helpful in a test run
# and lethal here. The host segfaults about once a day on the console, and the
# last five cores all die in the same place:
#
#     Python -> _gi -> g_object_get_qdata      <- on an object already freed
#            -> g_log "assertion 'G_IS_OBJECT (object)' failed"
#            -> _gi's log handler -> PyErr_WarnEx
#            -> crash in PyObject_GC_UnTrack
#
# Raising a Python warning means allocating, filtering and possibly collecting,
# on whichever thread GStreamer happened to be on. Doing that on top of a heap
# that a use-after-free has already disturbed is what turns a survivable
# complaint into SIGSEGV.
#
# Handing the domain to GLib's own C handler takes Python out of that path
# entirely: the assertion is printed to stderr -- which systemd keeps, so
# nothing is hidden, and it is still the signal that the underlying bug
# happened -- and the process carries on. Measured before and after on the
# console: same message, no Python warning, pipeline still usable.
#
# **This does not fix the use-after-free.** It removes one of the two places
# the corruption has been seen to kill the process; earlier cores died in
# g_object_unref from libgstwebrtc's dispose instead, and that path is
# untouched. Set FOURTH_PLAYER_PYTHON_GLIB_WARNINGS=1 to leave PyGObject's
# handler in place, which is what to do when hunting the root cause: the
# Python warning carries a traceback naming whoever touched the dead object.
_GLIB_DOMAINS = ("GLib-GObject", "GLib", "GObject")


def _quieten_glib_warnings():
    if os.environ.get("FOURTH_PLAYER_PYTHON_GLIB_WARNINGS"):
        log.info("leaving GLib warnings on PyGObject's Python path, as asked")
        return
    try:
        mask = (GLib.LogLevelFlags.LEVEL_MASK
                | GLib.LogLevelFlags.FLAG_FATAL
                | GLib.LogLevelFlags.FLAG_RECURSION)
        for domain in _GLIB_DOMAINS:
            GLib.log_set_handler(domain, mask, GLib.log_default_handler, None)
    except Exception:
        # Instrumentation must never be the fault. An older PyGObject without
        # log_set_handler simply keeps the behaviour it had.
        log.debug("could not take the GLib log domains back", exc_info=True)


def init():
    global _initialised
    if not _initialised:
        Gst.init([])
        _quieten_glib_warnings()
        _initialised = True


class PipelineWorker:
    """The one thread allowed to change the pipeline, and a way to replace it.

    Serialising every add and remove keeps the GPU driver from being used from
    two places at once, which this process has segfaulted over. The cost is a
    single point of failure: if that thread stops making progress -- wedged in
    a driver call, or gone entirely, which is what happened -- then every later
    job queues behind nothing and nothing works again until a restart. So it
    can be thrown away and replaced.
    """

    def __init__(self, name="gst-mutate"):
        self._name = name
        self._pool = self._make()

    def _make(self):
        return concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=self._name)

    @property
    def executor(self):
        return self._pool

    def submit(self, fn, *args):
        return self._pool.submit(fn, *args)

    def alive(self, timeout=5.0):
        """Whether it is still doing what it is given."""
        try:
            self._pool.submit(lambda: None).result(timeout=timeout)
            return True
        except Exception:
            return False

    def reset(self):
        """Abandon the current thread and start a fresh one.

        The old executor is not waited for: waiting is precisely the thing that
        does not finish when a worker has wedged.
        """
        old, self._pool = self._pool, self._make()
        try:
            old.shutdown(wait=False, cancel_futures=True)
        except TypeError:                       # older Python
            old.shutdown(wait=False)
        return old

    def shutdown(self, wait=True):
        self._pool.shutdown(wait=wait)


# What this machine can produce, in the order we would prefer it. H.265 is
# roughly half the bitrate for the same picture, which on a thin link is a
# better picture rather than a faster one.
CODEC_PREFERENCE = ("h265", "h264")

# Per codec: the hardware encoder, then the software ones in the order they
# would be chosen, then the parser and the payloader.
#
# More than one software encoder because they come from different packages and
# a machine has whichever it happens to have. x264enc is in gstreamer's "ugly"
# set and openh264enc is in "bad", and a Mint desktop installs bad and not
# ugly -- so a machine can have no x264enc, encode H.265 perfectly well, and
# report that it cannot do H.264 at all. Which breaks the promise the rest of
# this leans on: H.264 is the one every browser takes, and it is what a
# browser that tells us nothing is given.
_ELEMENTS = {
    "h264": (("vah264enc",), ("x264enc", "openh264enc"),
             "h264parse", "rtph264pay"),
    "h265": (("vah265enc",), ("x265enc",), "h265parse", "rtph265pay"),
}


def _first_present(names):
    """The first of these element factories this machine actually has."""
    for name in names:
        if Gst.ElementFactory.find(name):
            return name
    return None


# Every encoder this knows how to drive, best first, with the settings that
# particular one wants. They are not interchangeable: bitrate is kilobits here
# and bits there, the keyframe interval is key-int-max or gop-size depending,
# and handing one encoder another's properties makes it refuse to start rather
# than ignore them.
#
# Ordered hardware first, and within that by how widely the plugin is right:
# VA covers Intel and AMD through mesa, NVENC covers nvidia, v4l2 covers the
# ARM boards. Whatever is present is used; whatever is not is skipped. A
# machine with none of them encodes in software, which is slower and works.
#
# `converter` is what has to sit in front of it: the VA encoders take frames
# the GPU already holds, so they want vapostproc rather than videoconvert.
_VA = "vapostproc ! video/x-raw(memory:VAMemory),format=NV12,width={w},height={h}"
# add-borders wherever the scaler has it, which is every one that is not a
# fixed-function GPU block. Without it a picture whose shape does not match the
# size being sent is stretched to fit -- and that is not hypothetical: choosing
# a 2560x1440 monitor while the stream was set to the 2560x1610 of a virtual
# display stretched the real screen vertically. Borders are a black bar;
# stretching is every face in the game being the wrong shape.
#
# The way into a VA encoder on a machine with no vapostproc. Same elements as
# _SW but NV12, not I420: a VA encoder takes system-memory frames and uploads
# them itself, and NV12 is the only format its sink pad offers for them.
_VA_SYS = ("videoscale add-borders=true ! videoconvert ! "
           "video/x-raw,format=NV12,width={w},height={h}")
_SW = ("videoscale add-borders=true ! videoconvert ! "
       "video/x-raw,format=I420,width={w},height={h}")
# Windows' equivalent of _VA: the desktop arrives from d3d11screencapturesrc
# already in D3D11 memory, and d3d11convert does the format and the resize
# there, so the frame is never copied out of the GPU on its way to NVENC.
# Full range, and said so.
#
# The pipeline negotiated 2:3:7:1 on its own: limited range, BT.709 matrix,
# sRGB transfer, BT.709 primaries. A desktop is full-range RGB, so "limited"
# means every frame has its 0-255 squeezed into 16-235 before it is sent and
# stretched back at the other end -- which is lossy in the shadows and the
# highlights, and is the ordinary reason a remote desktop looks flatter than
# the desktop does. Asking for full range keeps all two hundred and
# fifty-six levels and puts the flag in the bitstream that tells the browser
# so.
#
# The transfer is pinned to BT.709 with it, because a browser decoding video
# assumes BT.709 whatever the file says, and content converted as sRGB and
# shown as BT.709 has its midtones lifted -- which is the other half of
# looking flat. Naming it makes the conversion and the assumption agree.
_FULL_RANGE = "colorimetry=(string)1:3:5:1"

_D3D11 = ("d3d11convert add-borders=true ! "
          "video/x-raw(memory:D3D11Memory),format=NV12,width={w},height={h},"
          + _FULL_RANGE)
# The same idea as _VA, for NVENC. cudaupload puts the frame in CUDA memory
# and cudaconvertscale does the format and the resize there, so it is uploaded
# once and never comes back; without them every frame is downloaded, converted
# on the CPU and re-uploaded, which is the bus crossed twice per frame to no
# purpose. NV12 because that is what NVENC wants from CUDA memory.
_CUDA = ("cudaupload ! cudaconvertscale ! "
         "video/x-raw(memory:CUDAMemory),format=NV12,width={w},height={h}")
# Before GStreamer 1.22 the convert and the scale are two elements.
_CUDA_SPLIT = ("cudaupload ! cudaconvert ! cudascale ! "
               "video/x-raw(memory:CUDAMemory),format=NV12,"
               "width={w},height={h}")

ENCODERS = {
    "h264": (
        ("vah264enc", "hardware", _VA,
         "{el} name=enc target-usage={usage} bitrate={kbps} "
         "key-int-max={keyint} cpb-size={cpb} b-frames=0"),
        ("vah264lpenc", "hardware", _VA,
         "{el} name=enc target-usage={usage} bitrate={kbps} "
         "key-int-max={keyint} cpb-size={cpb} b-frames=0"),
        # vbv-buffer-size is the CPB, and it is the difference between a
        # bitrate that is a target and one that is a limit. Without it NVENC
        # picks its own window and spreads a forced keyframe over so long that
        # the stream simply runs above its budget: a Windows host asked for
        # 5 Mb/s sent 9.3 Mb/s steadily for twenty minutes, because every
        # guest that lost the picture cost an IDR and nothing made the encoder
        # pay for it. The VA encoders have had cpb-size from the start, which
        # is why this only ever went wrong on Windows.
        #
        # Windows, and ahead of nvh264enc on purpose. Both are NVENC; this
        # one takes frames in D3D11 memory, which is where the desktop
        # capture already has them, while nvh264enc is the CUDA-mode encoder
        # and would need an upload. Measured on an RTX 4070 Ti: the D3D11
        # build of GStreamer ships no cudaupload or cudaconvertscale at all,
        # so the CUDA path is not merely slower there, it is unavailable.
        ("nvd3d11h264enc", "hardware", _D3D11,
         "{el} name=enc bitrate={kbps} gop-size={keyint} zerolatency=true "
         "rc-mode=cbr vbv-buffer-size={cpb}"),
        ("nvh264enc", "hardware", _CUDA,
         "{el} name=enc bitrate={kbps} gop-size={keyint} zerolatency=true "
         "rc-mode=cbr vbv-buffer-size={cpb}"),
        # Media Foundation: every Windows machine with any hardware encoder
        # at all, whoever made the GPU. Last of the hardware ones because it
        # is the most general and the least tunable.
        ("mfh264enc", "hardware", _SW,
         "{el} name=enc bitrate={kbps}"),
        ("v4l2h264enc", "hardware", _SW,
         "{el} name=enc extra-controls=\"controls,video_bitrate={bps}\""),
        ("x264enc", "software", _SW,
         "{el} name=enc speed-preset=ultrafast tune=zerolatency "
         "bitrate={kbps} key-int-max={keyint}"),
        ("openh264enc", "software", _SW,
         "{el} name=enc complexity=low rate-control=bitrate bitrate={bps} "
         "gop-size={keyint}"),
    ),
    "h265": (
        ("vah265enc", "hardware", _VA,
         "{el} name=enc target-usage={usage} bitrate={kbps} "
         "key-int-max={keyint} cpb-size={cpb} b-frames=0"),
        ("vah265lpenc", "hardware", _VA,
         "{el} name=enc target-usage={usage} bitrate={kbps} "
         "key-int-max={keyint} cpb-size={cpb} b-frames=0"),
        ("nvd3d11h265enc", "hardware", _D3D11,
         "{el} name=enc bitrate={kbps} gop-size={keyint} zerolatency=true "
         "rc-mode=cbr vbv-buffer-size={cpb}"),
        ("nvh265enc", "hardware", _CUDA,
         "{el} name=enc bitrate={kbps} gop-size={keyint} zerolatency=true "
         "rc-mode=cbr vbv-buffer-size={cpb}"),
        ("mfh265enc", "hardware", _SW,
         "{el} name=enc bitrate={kbps}"),
        ("x265enc", "software", _SW,
         "{el} name=enc speed-preset=ultrafast tune=zerolatency "
         "bitrate={kbps} key-int-max={keyint}"),
    ),
}


# Where the picture comes from, best first. Same shape as ENCODERS above and
# for the same reason: the machine has whichever of these it has, and asking
# is cheaper and more honest than deciding from sys.platform. A Wayland
# session and an X one are both Linux and do not offer the same element.
#
# `name=capture` is part of every template rather than being pasted in
# afterwards -- Stage.stop() silences the source by that name, and so does
# show_pointer.
#
# The third field is what that source calls "draw the mouse pointer into the
# picture", *and only where it can be changed while the pipeline is running*.
#
# They disagree about the name -- ximagesrc says show-pointer, d3d11 says
# show-cursor -- and about whether it may be touched at all. gst-inspect marks
# a property that is safe to set while PLAYING; d3d11screencapturesrc's
# show-cursor carries no such mark, and setting it on a running pipeline is an
# access violation. Two of the Windows crashes sat directly under "the mouse
# pointer is showing in the picture", which is this call.
#
# So None here means "do not touch it", and those sources have the cursor
# baked into their template instead. On a machine whose whole desktop is being
# driven by somebody remote that is the right setting anyway: a pointer you
# cannot see is a pointer you cannot aim.
# do-timestamp=true on every one of them, and it is the least cosmetic setting
# in this file.
#
# A live GstBaseSrc with it off stamps each frame from a frame counter: frame
# n is n/fps, exactly, for ever. That is a claim about when the picture was
# taken, and on these sources it is not true. The desktop captures hand over a
# frame when the desktop gives them one -- measured on the Windows host at
# 60fps, over 609 frames: a typical gap of 15.6ms, a worst of 77ms, and 135 of
# them nowhere near the 16.7ms the timestamps all claimed.
#
# The timestamp is what a guest's browser draws by. So the browser was being
# handed evenly spaced frames whose contents had advanced by wildly uneven
# amounts, and it drew them evenly, which makes everything in the picture
# speed up and slow down on a beat. Reported as the video pulsing, and no
# amount of buffering could touch it: the timestamps were not late, they were
# wrong. The jitter buffer had been raised to 60ms and read back from the
# receiver by then, which is what ruled late out.
#
# With it on, each frame carries the clock at the moment it was really
# grabbed, and a frame whose content is 30ms newer is shown 30ms later --
# which is all "smooth" means.
SOURCES = (
    ("ximagesrc",
     "ximagesrc name=capture display-name={display} use-damage=0 "
     "show-pointer=false do-timestamp=true",
     "show-pointer"),
    ("d3d11screencapturesrc",
     "d3d11screencapturesrc name=capture show-cursor=true do-timestamp=true",
     None),
    # Deprecated in GStreamer and kept as a fallback anyway: it is what a
    # machine with no working D3D11 path has left, and a soft picture beats
    # none. It warns on every start, which is the reason it is last.
    ("gdiscreencapsrc",
     "gdiscreencapsrc name=capture cursor=true do-timestamp=true",
     None),
)


# The ways Windows can be asked for the screen, and what to call them.
# How far behind the picture channel may get before frames are skipped
# rather than added to a queue nobody is draining.
#
# In time rather than bytes, because bytes mean nothing without knowing the
# rate: a megabyte is a moment at 60 Mb/s and four seconds on mobile data.
# A fifth of a second is the most that can sit in a send queue and still be
# worth showing when it arrives -- past that it is not a picture of now.
FRAME_QUEUE_SECONDS = 0.2

# What the browser's own count of arriving frames has to fall to before the
# encoder is told the link cannot carry what it is being given, and what it
# has to reach before it is given some back. The browser is the only witness
# that cannot be fooled: a send queue that reads empty proves the host handed
# the bytes on, not that anybody received them.
FRAMES_ARRIVING_LOW = 0.85
FRAMES_ARRIVING_GOOD = 0.97

# The most a guest drawing its own picture may be sent.
#
# Whole frames go down a WebRTC data channel, which is SCTP over DTLS over
# UDP, and SCTP is not an RTP stream: it is reliable, ordered and
# single-threaded through one association, and it has a throughput ceiling far
# below what the same link carries as media. Asked for 62500 kb/s -- the
# quality slider at its top, on a LAN -- the association fell over inside
# twenty seconds, gstsctpenc said "Could not write to resource", and the guest
# got a black screen with no error of its own because nothing arrived at all
# to fail on.
#
# So the picture channel has a ceiling of its own, separate from what the
# setting asks for. The guests watching the media track are unaffected: this
# only caps the shared encoder while somebody is being sent whole frames, and
# it is generous enough that nothing below it is a compromise at the sizes
# this streams. Above it, nobody gets a picture at all, which is worse than
# anybody's idea of a quality setting.
#
# Twenty thousand was the first number and the association went into an error
# state twice at it -- "Could not write to resource ... SCTP association went
# into error state" -- after backing up around 470 kB at 13.6 Mb/s. So it came
# down to eight, which stopped the errors and was far too low a price: at
# 2560x1600 eight megabits is a soft picture, and a guest who asks for fifty
# on a local network and is quietly given eight is being told nothing and
# shown the result. That is a worse fault than the one it fixed.
#
# Thirty now, because what broke the association has since been addressed
# directly. There is a pacer between the encoder and the socket -- a whole
# frame used to go down in one call as a burst of forty-odd packets -- and the
# rate comes down on the browser's own count of what arrives, which is the
# signal that was broken when eight was chosen. If the association starts
# erroring again this is the first number to look at, and the log says so
# plainly when it happens.
DATA_CHANNEL_CEILING_KBPS = 30000

# What the encoder is allowed to do about a link that cannot carry what it is
# being given.
#
# Every number here has been wrong once, and the way it was wrong is worth
# keeping: this controller walked a 62 Mb/s link down to 400 kb/s on a LAN and
# left it there, and both paths looked terrible because the encoder is shared.
# A controller that can do that is worse than none, so the shape is now:
# evidence it can trust, one step per second at most in either direction, a
# floor that is still a picture, and a climb fast enough to undo a mistake.
BITRATE_DOWN = 0.75          # a quarter off, when the evidence says to
BITRATE_UP = 1.15            # and fifteen percent back, per calm report
BITRATE_CALM = 2             # reports of quiet before it climbs a step

# Never below this fraction of what was asked for, nor this in absolute terms.
#
# 400 kb/s was the floor and it is not a picture at any size this streams --
# it is a smear that happens to move. A floor is meant to be the worst
# watchable answer, not the smallest number the arithmetic can reach.
BITRATE_FLOOR_KBPS = 1500
BITRATE_FLOOR_SHARE = 0.15

# The shortest gap between two steps down, whatever triggered them.
#
# Without it the queue-depth path fired per frame: the limit it compares
# against is derived from the rate, so lowering the rate lowered the limit,
# so the same backlog looked worse again. Measured: 1708 -> 1281 -> 960 ->
# 720 -> 540 -> 405 -> 400 inside one second, from one momentary backlog. A
# controller whose own output feeds its input needs a clock between the two.
BITRATE_STEP_SECONDS = 1.0

# How much of a rate that backed the channel up is worth trying again, and how
# fast that memory fades.
#
# Without it the controller oscillates rather than settles: it climbs 15% at a
# time until the data channel backs up, stalls for a few seconds while the
# queue drains, falls 25%, and climbs straight back into the same wall. Every
# excursion over the top is a freeze somebody is watching. Measured: it broke
# at 13652 kb/s -- "480829 bytes are waiting to be sent" -- and went round
# again every twenty seconds.
#
# So the rate that broke it is remembered and the climb stops below it, and
# the memory fades -- a fifth of a percent per calm report, which is minutes
# of nothing going wrong before it will try that high again. Slow on purpose:
# each probe that turns out to be wrong costs a stall somebody is watching,
# and a steady picture is worth more than the last few percent of a link.
BITRATE_PROBE_SHARE = 0.9
BITRATE_PROBE_FADE = 1.002


CAPTURE_APIS = {
    "dxgi": "Desktop Duplication",
    "wgc": "Windows Graphics Capture",
}


def pick_source():
    """The desktop capture this machine has, or None. (element, line, pointer)."""
    for element, line, pointer in SOURCES:
        if Gst.ElementFactory.find(element):
            return element, line, pointer
    return None


# And the sound. pulsesrc takes a named monitor source; wasapi2src takes
# `loopback=true`, which is Windows' way of saying the same thing -- record
# what is being played rather than what a microphone hears.
SOUNDS = (
    ("pulsesrc", "pulsesrc name=sound device={device} provide-clock=false"),
    ("wasapi2src", "wasapi2src name=sound loopback=true provide-clock=false"),
    ("wasapisrc", "wasapisrc name=sound loopback=true provide-clock=false"),
)


def pick_sound():
    """The loopback capture this machine has, or None. (element, line)."""
    for element, line in SOUNDS:
        if Gst.ElementFactory.find(element):
            return element, line
    return None


def _cuda_converter():
    """How to feed an NVENC encoder on this machine.

    nvcodec registers nvh264enc from the driver alone, so the encoder can be
    there when the CUDA filter elements are not -- they are a separate build
    option, and cudaconvertscale is one element since 1.22 and two before it.
    Where none of them are present, fall back to system-memory frames and let
    the encoder upload them itself: exactly the concession _VA_SYS makes for
    VA, and for the same reason -- slower than staying on the card, still far
    cheaper than encoding on the CPU.
    """
    if not Gst.ElementFactory.find("cudaupload"):
        return _SW
    if Gst.ElementFactory.find("cudaconvertscale"):
        return _CUDA
    if (Gst.ElementFactory.find("cudaconvert")
            and Gst.ElementFactory.find("cudascale")):
        return _CUDA_SPLIT
    return _SW


# How often a keyframe is sent when nothing has asked for one.
#
# Periodic keyframes are for a stream nobody can talk back on -- broadcast,
# recording, anything somebody might seek in. This is neither: every guest has
# a feedback channel and uses it, and a guest who loses the picture asks for a
# keyframe within a frame or two of noticing (see `_on_upstream`, and the
# "asked for a keyframe after losing the picture" line in the log).
#
# The cost of sending them anyway is not just bandwidth. Under CBR the encoder
# must fit an IDR inside its VBV window, so the frames after one are starved
# to pay for it: quality drops, recovers, drops again, on a fixed beat. At
# 144fps with the old interval of two seconds that beat was every 288 frames,
# and it is exactly what "the video pulses" describes.
#
# So: no periodic keyframe where the encoder can be told that (-1 on NVENC),
# and a long one where it cannot. The config's keyframe_interval still wins if
# somebody sets it, and a guest who needs a keyframe still gets one on asking.
KEYFRAME_FALLBACK_SECONDS = 10


# NVENC's low-delay tuning, where the element has it.
#
# `zerolatency=true` stops the encoder reordering frames, which is what its
# name says and all it does. It leaves the *preset* alone -- and the default
# one on these elements introduces itself as "Default (deprecated, use p1~7
# with tune)". That preset picks rate control aimed at quality over a whole
# clip, which under CBR means spending unevenly from frame to frame: the
# thing a still desktop never shows and a moving picture does.
#
# `tune=low-latency` is what NVIDIA's own guidance asks for in this job, and
# p4 is the middle of the seven speed presets rather than a guess at either
# end. Neither is invented for this project; they are the settings a real-time
# encoder is supposed to be given.
#
# Verified rather than assumed, because a nick this build does not have would
# fail at pipeline construction -- and a capture that will not build is a host
# that does not work at all. The value is set on a throwaway element and read
# back; only a change that actually took is used.
_NV_TUNING = "preset=p4 tune=low-latency"


def _takes(element, pairs):
    """Whether `element` really accepts every "property=nick" in `pairs`."""
    try:
        made = Gst.ElementFactory.make(element)
    except Exception:
        return False
    if made is None:
        return False
    for pair in pairs.split():
        name, _, nick = pair.partition("=")
        if made.find_property(name) is None:
            return False
        try:
            before = made.get_property(name)
            Gst.util_set_object_arg(made, name, nick)
            if made.get_property(name) == before:
                return False            # the nick was not understood
        except Exception:
            return False
    return True


def encoder_tuning(element):
    """Extra settings this particular encoder should be given, or ""."""
    if not element.startswith("nv"):
        return ""
    return " " + _NV_TUNING if _takes(element, _NV_TUNING) else ""


def keyframe_gap(element, settings, fps, wanted=0):
    """Frames between unrequested keyframes, for this particular encoder.

    Encoders disagree about both the name of the property and whether it can
    be told "never": NVENC takes -1, x264's key-int-max reads 0 as "pick one
    for me" rather than as never, and the VA encoders cap the number. So the
    element is asked what it will take rather than being handed a value that
    may make the pipeline refuse to start.
    """
    if wanted:
        return wanted                       # explicitly configured; not ours
    name = None
    for candidate in ("gop-size", "key-int-max"):
        if "%s={keyint}" % candidate in settings:
            name = candidate
            break
    far = max(1, int(fps) * KEYFRAME_FALLBACK_SECONDS)
    if name is None:
        return far
    try:
        made = Gst.ElementFactory.make(element)
        spec = made.find_property(name) if made is not None else None
    except Exception:
        spec = None
    if spec is None:
        return far
    if getattr(spec, "minimum", 0) == -1:
        return -1                           # never, until somebody asks
    top = getattr(spec, "maximum", far)
    return max(1, min(far, int(top)))


def pick_encoder(codec, allow_hardware=True):
    """The best encoder for this codec on this machine, or None.

    Returns (element, kind, converter, settings). Presence of the factory is
    the test: the VA plugin registers vah264enc only where a device can do it,
    and the nvidia plugin registers nvh264enc only where NVENC answers, so a
    factory that exists is a strong claim.

    It is only a claim, though, and nothing here rescues it if it turns out
    to be wrong: an encoder that registers and then refuses to start takes
    the session with it. Audio falls back -- see Stage.start -- and the
    encoder does not. Everything that can be checked without building a
    pipeline is therefore checked here, which is what the converter
    substitutions below are doing.
    """
    for element, kind, converter, settings in ENCODERS.get(codec, ()):
        if kind == "hardware" and not allow_hardware:
            continue
        if not Gst.ElementFactory.find(element):
            continue
        # vapostproc is the fast way into a VA encoder -- it converts and
        # scales on the card, so a frame never crosses the bus twice -- but it
        # is not the only way in. Skylake registers vah264lpenc and no
        # vapostproc at all, and requiring the pair sent an HD 530 to x264enc
        # while its own encoder sat there unused. Where the converter is
        # missing, hand the encoder ordinary system-memory frames and let it
        # upload them itself: slower than staying on the card, still far
        # cheaper than encoding on the CPU.
        if converter is _VA and not Gst.ElementFactory.find("vapostproc"):
            converter = _VA_SYS
        if converter is _CUDA:
            converter = _cuda_converter()
        return element, kind, converter, settings
    return None

_host_codecs = None


# The shortest gap between keyframes forced by guests asking for one.
#
# This was 0.5s, which sounds modest and is not. A keyframe costs something
# like fifteen ordinary frames, so at 30fps two of them a second is most of a
# second of the bitrate spent on recovery -- and because the encoder is
# shared, one guest's request is paid for by everybody. Two guests on a
# Windows host settled into exactly that: each asking about once a second,
# the limiter granting every one, and a stream asked for 5 Mb/s running at
# 9.3 Mb/s for twenty minutes. The extra traffic made frames late, late frames
# were dropped, and a dropped frame is another request. It sustains itself.
#
# So this is a bucket rather than a gap, because one gap cannot serve both
# cases honestly. A single guest who drops a frame should get a keyframe at
# once -- that is the common case, and making them wait is the two seconds of
# black this limiter was written to avoid. What must be refused is the
# *sustained* demand of several guests asking for ever.
#
# KEYFRAME_MIN_GAP is therefore the refill interval -- the long-run rate, one
# keyframe per this many seconds -- and KEYFRAME_BURST is how many may be
# spent at once by a room that has been quiet. An isolated blip finds a full
# bucket and is served immediately; a storm drains it and gets the refill rate.
# Retuned when the picture channel stopped retransmitting. A keyframe used to
# be what a guest asked for after losing the picture, which is rare; it is now
# the *only* way back from a single lost piece, because nothing is resent. At
# one and a half seconds a lost packet cost a second and a half of frozen
# picture -- the limiter, not the loss. A quarter of a second with four in
# hand is at worst four keyframes a second, which an eight megabit stream can
# afford far more easily than it can afford the freeze.
#
# The burst is where that is bought, not the gap. Two tests hold the long-run
# rate to at most forty percent of the frame budget and they are right to: a
# keyframe is worth about fifteen ordinary frames, so even one a second is
# half the picture at thirty. They also caught two attempts at this -- a
# quarter-second gap, then one second -- and the arithmetic they enforce is
# the whole of the trade: whatever is spent on the burst has to come off the
# refill. Three in hand, answered at once, and one every two seconds after.
#
# The burst is what matters. An isolated loss is nearly all of them, and three
# of those in quick succession are repaired instantly; only sustained loss is
# made to wait, and sustained loss cannot be repaired by spending the whole
# bitrate on keyframes anyway.
KEYFRAME_MIN_GAP = 2.0
KEYFRAME_BURST = 3

# Video is the first feed a guest is given, so it is the first transceiver.
VIDEO_TRANSCEIVER = 0

# How long the capture may produce nothing before it is worth a line in the
# log. Six frames at 30 fps, and about a fifth of a second: long enough that
# nobody would call it smooth, short enough to catch what a guest calls "it
# froze for a moment".
STALL_LOG_GAP = 0.2

# And how often that is worth saying. A host that is struggling stalls over and
# over, and the log this shares already has a source that can put four thousand
# lines in it in an afternoon.
STALL_LOG_GAP_QUIET = 5.0


def host_codecs(hardware=True):
    """The codecs this host can actually encode, best first.

    Probed once and remembered. Presence of an element is not quite proof it
    will run, but it is what can be known without building a pipeline, and a
    codec that then fails to start is caught by the usual fallback.
    """
    global _host_codecs
    if _host_codecs is not None:
        return _host_codecs
    init()
    found = []
    for codec in CODEC_PREFERENCE:
        _vas, _sws, parser, payloader = _ELEMENTS[codec]
        # Asked exactly as the pipeline will ask, so this cannot promise a
        # codec the pipeline then refuses to build -- which is how a machine
        # with no VA driver came to offer H.265 and hand out a lie.
        chosen = pick_encoder(codec, hardware)
        # H.265 only when the card can do it. Its whole advantage is fitting
        # the same picture into fewer bits, and x265enc will not pay for that
        # at thirty frames a second on a desktop -- a Skylake box here sat at
        # a load average of fifty-five encoding 1080p that way, while its own
        # HD 530 could have done H.264 in hardware and barely warmed up. So a
        # host that cannot encode H.265 in hardware offers H.264 instead,
        # rather than choosing the better codec and the worse session.
        if chosen and codec == "h265" and chosen[1] != "hardware":
            log.info("not offering H.265: only %s can encode it here, and "
                     "encoding H.265 in software costs more than the bitrate "
                     "it saves", chosen[0])
            continue
        if (chosen
                and Gst.ElementFactory.find(parser)
                and Gst.ElementFactory.find(payloader)):
            found.append(codec)
    if not found:
        found = ["h264"]
    _host_codecs = found
    log.info("this host can encode: %s", ", ".join(found))
    return found


# How many RTP packets each appsink may hold before it starts dropping them.
# See the sinks themselves for why four was not enough to survive a keyframe.
# How many frames go into one pacing report. Ten seconds at 60fps: long
# enough that a single hiccup does not dominate it, short enough to say
# something about the minute somebody is complaining about.
PACE_SAMPLE = 600

VIDEO_SINK_PACKETS = 1024
AUDIO_SINK_PACKETS = 128


def best_shared_codec(guest_codecs, hardware=True):
    """The best codec both ends can manage, or h264 if they cannot agree.

    A guest that tells us nothing gets H.264, which every browser decodes --
    guessing better than that on no information is how a black screen happens.
    """
    ours = host_codecs(hardware)
    theirs = {str(c).lower().replace("video/", "") for c in (guest_codecs or [])}
    if not theirs:
        return "h264" if "h264" in ours else ours[0]
    for codec in ours:
        if codec in theirs:
            return codec
    return "h264"


def _nice_type():
    """The GType of webrtcbin's ICE agent, or None if it cannot be had.

    It is registered by the webrtc plugin rather than published in a typelib,
    and only once something has actually instantiated it -- so a throwaway
    webrtcbin has to exist before the name resolves.
    """
    try:
        Gst.ElementFactory.make("webrtcbin", None)
        return GObject.type_from_name("GstWebRTCNice")
    except Exception:
        return None


# Every ICE agent this process has made, kept for ever on purpose.
#
# `ice-agent` is construct-only and webrtcbin does not take a reference of its
# own: the one Python holds *is* the agent's only reference, and webrtcbin
# frees the agent when it is disposed. That was worked out from two crashes
# that contradict each other unless it is true --
#
#   released before the pipeline was disposed -> the dispose crashed, because
#     webrtcbin still needed an agent that had just been freed
#   released after the pipeline was disposed  -> the release crashed, because
#     the dispose had already freed it
#
# -- and both were an access violation in gobject at the same address, on
# Windows, where faulthandler names the line. So there is no moment at which
# unreffing it from Python is safe, and the only safe thing is not to.
#
# The cost is honest and small: one NiceAgent per peer that ever existed, for
# the life of the process. The cost of the alternative is the host dying every
# time a guest leaves, which it did, three times, in an afternoon.
#
# The proper fix is upstream of here -- webrtcbin should take a reference for
# a property it keeps -- or a way to hand the object over without Python
# retaining one. Until then this is a leak that is written down rather than a
# crash that is not.
_PARKED_AGENTS = []


def make_ice_agent(cfg):
    """An ICE agent whose UDP ports fall in a range a router can forward.

    This is what makes fourth-player reachable from outside at all. Left to
    itself webrtcbin takes ephemeral ports -- eight of them, scattered across
    the whole range -- so there is nothing to write a firewall rule about, and
    a guest outside the network gets a black picture however much is forwarded.

    It has to be built here and passed to webrtcbin at construction: `ice-agent`
    is construct-only, and *reading* the one webrtcbin makes for itself
    corrupts it (`g_object_get_qdata: assertion G_IS_OBJECT (object) failed`,
    then a crash at negotiation). Returns None if any of that fails, which
    costs the port range and nothing else.
    """
    if not getattr(cfg, "bounded_ice_ports", True):
        log.info("letting webrtcbin make its own ICE agent; ports will be "
                 "ephemeral and cannot be forwarded")
        return None
    gtype = _nice_type()
    if gtype is None:
        log.warning("could not reach the ICE agent's type; ports will be "
                    "ephemeral and cannot be forwarded")
        return None
    try:
        ice = GObject.new(gtype)
        ice.set_property("min-rtp-port", cfg.rtp_port_min)
        ice.set_property("max-rtp-port", cfg.rtp_port_max)
        return ice
    except Exception as exc:
        log.warning("could not bound the ICE port range (%s); ports will be "
                    "ephemeral", exc)
        return None


# profile_idc + constraint flags, per RFC 6184. The level is appended.
_H264_PROFILE_IDC = {
    "constrained-baseline": "42e0",
    "baseline": "4200",
    "main": "4d00",
    "high": "6400",
}


def h264_profile_level_id(profile, height):
    """The profile-level-id a guest's browser will check us against.

    webrtcbin builds `a=fmtp` from the payloader's caps, and the payloader only
    learns the profile once it has seen an SPS -- which happens after the offer
    is made. So the offer went out with no fmtp at all, every browser applied
    the spec default of constrained baseline, and the ones that check properly
    refused the high-profile stream that then arrived. Stating it up front is
    the fix, and it is only honest because the encoder's profile is pinned.

    The level is rounded up to cover the frame size; advertising a level above
    what is sent is allowed, the reverse is not.
    """
    idc = _H264_PROFILE_IDC.get(profile, _H264_PROFILE_IDC["constrained-baseline"])
    level = "1e" if height <= 480 else "1f" if height <= 720 else "28"
    return idc + level


# HEVC levels, as (level-id, maximum luma samples per second). level-id is the
# level times thirty, per RFC 7798. The sample rate is what actually decides:
# 1080p fits inside level 3.1 by picture size and needs 4.1 to be played at
# sixty frames a second.
_H265_LEVELS = (
    (63, 11_059_200),      # 2.1
    (90, 16_711_680),      # 3.0
    (93, 33_423_360),      # 3.1
    (120, 66_846_720),     # 4.0
    (123, 133_693_440),    # 4.1
    (150, 267_386_880),    # 5.0
    (153, 534_773_760),    # 5.1
    (156, 1_069_547_520),  # 5.2
)


def h265_level_id(width, height, fps):
    """The lowest HEVC level that covers this picture at this rate.

    Advertising a level above what is sent is allowed; the reverse is not, and
    is what a black screen at 1080p was. A browser given no `a=fmtp` at all
    applies RFC 7798's defaults -- Main profile at level 3.1 -- which tops out
    around 720p30, so a 1080p60 stream arrived at a decoder configured for
    less than a third of it and nothing was drawn.
    """
    rate = max(1, int(width) * int(height) * max(1, int(fps)))
    for level, allowed in _H265_LEVELS:
        if rate <= allowed:
            return level
    return _H265_LEVELS[-1][0]


def h265_fmtp(width, height, fps):
    """The H.265 parameters a guest's browser reads before it decodes.

    Main profile and Main tier, which is what every hardware encoder here
    produces and every browser that lists H265 at all will take. tx-mode=SRST
    says single RTP stream transmission, which is the only mode anything
    implements and the default -- stated because a parameter a browser has to
    infer is a parameter it can infer differently.
    """
    return ("profile-space=0;profile-id=1;tier-flag=0;"
            f"level-id={h265_level_id(width, height, fps)};tx-mode=SRST")


def fmtp_for(codec, profile, width, height, fps):
    """The parameters to state for the stream actually being sent.

    One function so both codecs are decided in the same place. H.265 went out
    with nothing here for as long as it existed, because the H.264 branch was
    written first and the other half of the conditional was an empty string
    rather than a question anybody had asked.
    """
    if str(codec).lower() in ("h265", "hevc"):
        return h265_fmtp(width, height, fps)
    # The level is read off the picture that is actually sent, not the one
    # that was asked for: a browser told level 4.0 and handed 540p is being
    # told something untrue about the stream it is decoding.
    return (f"profile-level-id={h264_profile_level_id(profile, height)};"
            f"packetization-mode=1;level-asymmetry-allowed=1")


def _pick_monitor(wanted, made=None):
    """Which screen to capture: the one asked for, or the virtual one, or none.

    `wanted` is a device name -- "\\\\.\\DISPLAY10" -- because that is what
    survives. It used to be an index into the monitor list, and the list is
    renumbered whenever a screen is added or removed; making a virtual display
    does exactly that, so an index chosen a moment earlier could point at a
    different screen by the time it was used.

    Old configs hold an integer, and are still read as an index rather than
    being thrown away: somebody who set this last week should not have their
    choice silently forgotten.

    None means "whatever the capture would pick on its own", which is the
    primary screen.
    """
    screens = vdisplay.monitors()
    if isinstance(wanted, str) and wanted:
        for screen_ in screens:
            if screen_[5] == wanted:
                return screen_
        return None
    if isinstance(wanted, int) and wanted >= 0:
        for screen_ in screens:
            if screen_[0] == wanted:
                return screen_
        return None
    # Nothing asked for. The virtual display, if one was made for this
    # capture, is what it was made for.
    if made is not None and made.monitor_handle is not None:
        for screen_ in screens:
            if screen_[1] == made.monitor_handle:
                return screen_
    return None


def mic_line_index(sdp):
    """Which m-line in this offer is the guest's microphone, or None.

    A plain function so it can be exercised without a pipeline. Counted over
    the m-lines only, because that count is exactly how a browser indexes its
    transceivers -- the third m-line is the third transceiver.

    Ours is the second audio line: the first carries the game's sound out, and
    the microphone is the only line offered the other way round.
    """
    lines = [line for line in sdp.splitlines() if line.startswith("m=")]
    audio = [index for index, line in enumerate(lines)
             if line.startswith("m=audio")]
    return audio[1] if len(audio) > 1 else None


def describe_sdp(text):
    """The shape of an SDP in one line, for the log.

    Whether the two sides bundled decides how many transports have to connect,
    and a session that bundles on the first connection and not on the rebuilt
    one is two different connections wearing the same name -- which is not
    visible from anything else that gets logged.
    """
    kinds = [line.split()[0][2:] for line in text.splitlines()
             if line.startswith("m=")]
    ports = [line.split()[1] for line in text.splitlines() if line.startswith("m=")]
    bundled = any(line.startswith("a=group:BUNDLE") for line in text.splitlines())
    refused = [k for k, port in zip(kinds, ports) if port == "0"]
    return "%d m-line(s) [%s]%s%s" % (
        len(kinds), ", ".join(kinds),
        ", bundled" if bundled else ", NOT bundled",
        ", refused: " + ",".join(refused) if refused else "")


def with_fmtp(sdp, fmtp, encoding="H264"):
    """Add the parameters a browser needs, if webrtcbin left them out.

    It leaves them out every time: the payloader learns the profile from the
    stream's first SPS, and the offer is written before a single frame has
    flowed. So every offer went out with no `a=fmtp` at all, browsers applied
    the spec default of constrained baseline with single-NAL packetisation, and
    the strict ones refused the high-profile stream that arrived instead.

    Done here rather than by forcing the payloader's caps, because a capsfilter
    is a filter: one that names a profile-level-id the payloader does not
    produce matches nothing and passes nothing, which turns a picture that was
    merely refused by some browsers into no picture for anybody.

    H.265 has the same hole and it is worse there, because its default is not
    merely a conservative profile but level 3.1 -- about 720p30. A 1080p60
    stream offered with no fmtp reached a decoder configured for less than a
    third of it, and drew nothing at all.

    The payload type is read out of the rtpmap line rather than assumed. It is
    pinned to 96 in the pipeline, but an fmtp attached to the wrong number is
    silently ignored, which is the same black screen with a longer search.
    """
    if not fmtp:
        return sdp
    out, added, want = [], False, f" {encoding}".upper()
    for line in sdp.splitlines(True):
        out.append(line)
        if added or not line.startswith("a=rtpmap:"):
            continue
        head, _, rest = line[len("a=rtpmap:"):].partition(" ")
        if not rest.upper().startswith(encoding.upper() + "/"):
            continue
        pt = head.strip()
        if f"a=fmtp:{pt}" in sdp:            # webrtcbin wrote one after all
            return sdp
        ending = "\r\n" if line.endswith("\r\n") else "\n"
        out.append(f"a=fmtp:{pt} {fmtp}{ending}")
        added = True
    return "".join(out)


def _caps(width, height):
    return f"video/x-raw(memory:VAMemory),format=NV12,width={width},height={height}"


class Stage:
    """Capture, encode once, and fan the result out."""

    # Pausing the capture when the last guest leaves. Class attributes, not
    # set in __init__, because a Stage is built by __new__ in the suites --
    # __init__ wants a real display -- and take_peer must answer on one of
    # those too. `_attaching` counts guests part-way through add_peer: they
    # are not in self.peers yet, and idling on top of one would hand it a
    # paused pipeline.
    _idle_timer = None
    _attaching = 0

    def __init__(self, cfg, loop, codec=None):
        init()
        self.cfg = cfg
        # The codec for this capture, which may differ from the configured one
        # when it was negotiated with a guest.
        self.codec = (codec or cfg.codec or "h264").lower()
        self.loop = loop
        self.peers = {}
        self._glib_loop = None
        self._thread = None
        self.has_audio = False
        self.source_sound_name = ""
        self._chosen_monitor = None
        # Where this machine is reachable from outside, discovered once. Only
        # the address is used; see fourthplayer/net.py for why not the port.
        self.public_ip = cfg.public_ip
        if cfg.advertise_public_ip and not self.public_ip:
            found = net.public_address()
            if found:
                self.public_ip = found[0]
                log.info("public address is %s; guests outside the network will "
                         "be offered it on the forwarded ports", self.public_ip)
            else:
                log.info("could not discover a public address; only guests on "
                         "this network will be able to connect")
        # Every add and remove of a peer goes through this one thread. Adding
        # a peer while another is being torn down means two threads mutating
        # one pipeline, and the symptom is the replacement refusing to reach
        # PLAYING -- so a guest who reloaded got a black screen. One worker
        # keeps them ordered; being a worker at all keeps them off the event
        # loop, which is what stopped the server freezing for seconds at a
        # time while a live peer was dismantled.
        self.worker = PipelineWorker()
        self._reset_keyframe_limit()
        self._last_sample = {}
        self._stalls = {}
        self._said_stall = {}
        self._last_frame = 0.0
        # What the encoder is being run at, as against what was asked for.
        self._rate_now = None
        self._rate_calm = 0
        self._rate_stepped = 0.0
        self._rate_broke = None
        self._last_pts = Gst.CLOCK_TIME_NONE
        self._gaps = []
        self._stamps = []

        # Bits the encoder may hold back to smooth a burst. Smoothing is delay.
        cpb = max(16, int(cfg.bitrate_kbps * cfg.cpb_ms / 1000))
        hevc = self.codec in ("h265", "hevc")
        # Asking for hardware that is not there used to fail at pipeline
        # construction -- `no element "vapostproc"` -- and the session simply
        # would not start. On a machine with no VA driver, which is every
        # virtual machine and plenty of real ones, that is the first thing a
        # new install does and the last thing it explains. `check` noticed and
        # told the user to edit the config; doing it here means they never have
        # to. Software encoding is slower, not broken.
        # Whatever this machine can actually do, best first. Asking for
        # hardware that is not there used to fail at pipeline construction --
        # `no element "vapostproc"` -- and the session simply would not start;
        # then it hard-coded VA, which is right on AMD and Intel and leaves an
        # nvidia or an ARM board encoding in software for no reason.
        chosen = pick_encoder("h265" if hevc else "h264", cfg.hardware_encode)
        if chosen is None:
            raise RuntimeError("this machine has no encoder for %s"
                               % ("H.265" if hevc else "H.264"))
        element, kind, converter, settings = chosen
        # After the encoder is known, because what it will accept here is not
        # the same from one to the next. See keyframe_gap.
        keyint = keyframe_gap(element, settings, cfg.fps,
                              cfg.keyframe_interval)
        log.info("keyframes: %s", "only when a guest asks" if keyint == -1
                 else "every %d frames (%.1fs), and whenever a guest asks"
                      % (keyint, keyint / max(1, cfg.fps)))
        self.encoder_name = element
        self.encoder_kind = kind
        if kind == "software" and cfg.hardware_encode:
            log.warning("no hardware encoder on this machine, so encoding in "
                        "software with %s (slower, and it works)", element)
        else:
            log.info("encoding with %s (%s)", element, kind)

        # And a software encoder is not asked for more than a CPU can carry.
        # This is a guard rather than a preference: 1080p in software took a
        # machine to load average fifty and kept it there, with no ssh and no
        # web server, until somebody could reach the power button. A smaller
        # picture is a far smaller thing than that.
        width, height = cfg.width, cfg.height
        cap = max(240, int(cfg.software_max_height or 0) or 720)
        if kind == "software" and height > cap:
            width = max(2, int(round(width * cap / height)) // 2 * 2)
            height = cap
            log.warning("%dx%d is more than a software encoder should be "
                        "asked for; using %dx%d instead (software_max_height "
                        "in the config raises this)",
                        cfg.width, cfg.height, width, height)
        encoder = settings.format(el=element, usage=cfg.target_usage,
                                  kbps=cfg.bitrate_kbps,
                                  bps=cfg.bitrate_kbps * 1000,
                                  keyint=keyint, cpb=cpb)
        tuning = encoder_tuning(element)
        if tuning:
            encoder += tuning
            log.info("tuning %s for low delay (%s)", element, tuning.strip())
        # Pin the profile between encoder and parser: the payloader reads it
        # from these caps to build profile-level-id, and without it a browser
        # is guessing.
        profile = "" if hevc else f"! video/x-h264,profile={cfg.h264_profile} "
        self._fmtp = fmtp_for(self.codec, cfg.h264_profile,
                              width, height, cfg.fps)
        parser = "h265parse" if hevc else "h264parse"
        payloader = "rtph265pay" if hevc else "rtph264pay"
        encoding = "H265" if hevc else "H264"
        self.encoding = encoding
        # What is actually going out, which is not always what was asked for.
        self.sending_width, self.sending_height = width, height
        convert = converter.format(w=width, h=height)

        # config-interval=-1 puts SPS/PPS in front of every keyframe. Without it
        # a guest who joins mid-session has the parameter sets they need only if
        # they happened to be listening at the start, which they never are.
        # Whatever this machine captures its desktop with. X11 here, D3D11 on
        # Windows; the difference is a table lookup rather than anything the
        # rest of this function needs to know.
        source = pick_source()
        if source is None:
            raise RuntimeError(
                "this machine has no way to capture its screen: none of %s"
                % ", ".join(name for name, _line, _ptr in SOURCES))
        source_element, source_line, self._pointer_property = source
        self.source_name = source_element
        log.info("capturing with %s", source_element)

        # How Windows is asked for the screen, where that is a choice. See
        # config.capture_api: the two interfaces differ in who decides when a
        # frame happens, which is the difference between a picture whose
        # contents advance evenly and one that judders however well it is
        # drawn.
        wanted_api = str(getattr(cfg, "capture_api", "") or "").strip().lower()
        if not wanted_api and source_element == "d3d11screencapturesrc":
            # Nobody chose, so choose the better one where it exists.
            #
            # Measured on this machine, same game, same everything else:
            #
            #   Desktop Duplication  sampled every 15.5ms typical, worst 32ms,
            #                        146 of 600 nowhere near 16.7ms
            #   Graphics Capture     sampled every 16.7ms typical, worst 35ms,
            #                        17 of 600 uneven
            #
            # The typical interval goes from wrong to exactly right and the
            # uneven quarter becomes an uneven twentieth. Checked rather than
            # assumed, because a property a build does not have is a pipeline
            # that will not start, and that is a host with no capture at all.
            if _takes("d3d11screencapturesrc", "capture-api=wgc"):
                wanted_api = "wgc"
        if wanted_api and source_element == "d3d11screencapturesrc":
            if wanted_api in CAPTURE_APIS:
                source_line += " capture-api=%s" % wanted_api
                log.info("asking Windows for the screen through %s",
                         CAPTURE_APIS[wanted_api])
            else:
                log.warning("no such way of capturing the screen: %r; leaving "
                            "it to the element", wanted_api)

        # Or not the screen at all.
        #
        # Every experiment about the smoothness of this picture has had to
        # reason around an instrument: a marker bit that arrives twice per
        # frame, a timeline that is even because it comes from a counter, a
        # clock read from a Python probe that the GIL can delay. This one has
        # no instrument in it. videotestsrc generates frames with exact
        # contents at exact times, so if the picture is smooth with this on,
        # everything from the encoder to the guest's eye is sound and the
        # fault is in the capture -- and if it is not smooth, the capture was
        # never the problem and neither was anything I have changed today.
        #
        # Scrolling bars rather than the bouncing ball this started as. A
        # single ball on a 2560-wide picture is a small thing moving against a
        # large still one, and it was reported as not moving at all. Bars fill
        # the frame: the whole picture slides, every edge in it is a chance to
        # see a stutter, and there is nothing to squint at.
        #
        # horizontal-speed is pixels per frame, so the number is worked out
        # from the frame rate to keep the bars moving at the same speed on the
        # screen whatever the rate is set to -- otherwise changing the frame
        # rate changes what is being watched as well as how it arrives, and
        # the two cannot be told apart.
        if getattr(cfg, "test_pattern", False):
            step = max(1, int(round(600.0 / max(1, cfg.fps))))
            source_line = ("videotestsrc name=capture is-live=true "
                           "pattern=smpte horizontal-speed=%d" % step)
            # The D3D11 and CUDA converters take frames the GPU already holds
            # and this one is made on the CPU, so it needs putting there.
            if "d3d11" in converter:
                source_line += " ! d3d11upload"
            elif "cuda" in converter:
                source_line += " ! cudaupload"
            self._pointer_property = None
            self.source_name = "videotestsrc"
            log.warning("sending a test pattern instead of the screen: bars "
                        "moving %d pixels a frame, about 600 a second",
                        step)

        # A screen of our own, if one was asked for and this machine can make
        # one. Made at exactly the size being sent, so the capture is the
        # picture and nothing is scaled: the whole point of it is the case
        # where the desktop is smaller than what the guest wants.
        self.vdisplay = None
        if cfg.virtual_display:
            display = vdisplay.VirtualDisplay()
            if display.open(width, height, cfg.fps):
                self.vdisplay = display
            else:
                log.warning("a virtual display was asked for and could not be "
                            "made; sending this machine's own screen instead")

        # Which screen to send, decided once and in one place.
        #
        # Making a virtual display and choosing which screen to send are two
        # different questions, and they used to be one: the capture was
        # pointed at the virtual display whenever there was one, so choosing a
        # screen did nothing at all while it was on. Reported as the switch
        # appearing to try and always staying put.
        wanted = getattr(cfg, "monitor", "")
        chosen = _pick_monitor(wanted, self.vdisplay)
        # A real screen is sent at its own size.
        #
        # The custom size belongs to the virtual display: it is the size that
        # display is *made* at, chosen to match a particular guest's screen,
        # and it is meaningful only there. Applying it to a physical monitor
        # scales that monitor's picture to a shape it is not -- which is how
        # choosing the real screen while the stream was set to 2560x1610 came
        # out stretched.
        #
        # The borders above would now letterbox it rather than stretch it,
        # which is better and still wrong: a 2560x1440 screen would be sent
        # inside a 2560x1610 frame with bars, spending bitrate on black. Sent
        # at its own size there is nothing to scale and nothing to pad.
        on_virtual = (self.vdisplay is not None and chosen is not None
                      and chosen[1] == self.vdisplay.monitor_handle)
        if chosen is not None and not on_virtual:
            its_width, its_height = chosen[2], chosen[3]
            # Still inside what a software encoder can carry. The cap above
            # was worked out against the configured size, and taking a screen
            # at its own size must not walk round it -- 1080p in software once
            # took a machine to a load average of fifty and kept it there.
            if kind == "software" and its_height > cap:
                its_width = max(2, int(round(its_width * cap / its_height)) // 2 * 2)
                its_height = cap
            if (its_width, its_height) != (width, height):
                log.info("sending %s at %dx%d rather than the %dx%d set for "
                         "the virtual display, so nothing is scaled",
                         chosen[5], its_width, its_height, width, height)
                width, height = its_width, its_height
                self.sending_width, self.sending_height = width, height
                convert = converter.format(w=width, h=height)
        # Kept, because start() needs it and cannot see this local. The first
        # version of the refresh-rate change referred to `chosen` from there
        # and raised NameError, which took the capture down with it -- and
        # py_compile cannot see a name that is only missing at runtime.
        self._chosen_monitor = chosen
        # A test pattern has no monitor, and monitor-handle= on a videotestsrc
        # is a pipeline that will not build -- which on this host means no
        # capture at all rather than a diagnostic anybody can read.
        if getattr(cfg, "test_pattern", False):
            chosen = None
        if chosen is not None:
            # By handle rather than index: an index is a position in a list,
            # and adding or removing a screen renumbers it -- which making a
            # virtual display does, every time.
            source_line += " monitor-handle=%d" % chosen[1]
            log.info("sending %s (%dx%d)%s", chosen[5], chosen[2], chosen[3],
                     " -- the virtual one" if self.vdisplay is not None
                     and chosen[1] == self.vdisplay.monitor_handle else "")
        elif wanted not in ("", -1, None):
            log.warning("screen %r was asked for and this machine has %s; "
                        "sending the usual one", wanted,
                        ", ".join(m[5] for m in vdisplay.monitors()) or "none")

        # Capturing, and whether anything holds the rate of it.
        #
        # Three shapes come out of two switches:
        #
        #   neither    the capture's own rate, named by the filter below and
        #              not kept by anything. What this was before today.
        #   pacing     a videorate behind the capture, dropping whatever
        #              arrived too early for its slot.
        #   both       the capture asked for twice the rate, and the videorate
        #              choosing the fresher of each pair.
        #
        # Oversampling turns pacing on by itself: without something to bring
        # the rate back down, capturing twice as often just sends twice as
        # many frames.
        oversample = bool(getattr(cfg, "oversample", False))
        # Stamping frames when they arrive and then pacing them is the two
        # halves of this cancelling out: a videorate exists to put timestamps
        # back on a grid, which is exactly what the stamping is undoing. So
        # true time wins and the pacing goes.
        self._true_time = bool(getattr(cfg, "true_time", False))
        if self._true_time and oversample:
            # These two cannot both be had. Oversampling captures at twice
            # the rate and needs the videorate to bring it back down; true
            # time exists to stop a videorate putting the timestamps back on
            # a grid. Setting both produced a pipeline that asked for 120 and
            # then 60 with nothing in between to convert, which gstreamer
            # would not build at all -- and a host whose capture will not
            # build cannot give anybody video.
            log.warning("capturing twice as often needs the pacing, and "
                        "stamping frames on arrival turns the pacing off; "
                        "keeping the timestamps and dropping the oversampling")
            oversample = False
        pace = ((bool(getattr(cfg, "pace_frames", True)) or oversample)
                and not self._true_time)
        # Only ever with something between it and the filter below. Two caps
        # filters back to back is not a pipeline.
        oversampling = (f"! video/x-raw(ANY),framerate={cfg.fps * 2}/1 "
                        if (oversample and pace) else "")
        pacing = "! videorate drop-only=true " if pace else ""
        if self._true_time:
            log.info("frames will be stamped when they arrive, not when they "
                     "were due; nothing is holding the rate")
        log.info("frames: captured at %d/s%s, sent at %d/s",
                 cfg.fps * 2 if oversample else cfg.fps,
                 " and paced" if pace else " with nothing holding the rate",
                 cfg.fps)

        description = (
            # The pointer is off while nobody is driving: a mouse cursor
            # sitting over a game is noise, and there is nothing to point
            # with. Stage.show_pointer turns it on for as long as somebody
            # holds the desk -- see there for why it cannot simply be left on.
            f"{source_line.format(display=cfg.display)} "
            f"{oversampling}"
            f"{pacing}"
            # The frame rate below is a *claim* without this. A caps filter
            # says what the pictures are, not when they arrive, and the
            # desktop captures do not pace themselves to it: measured on this
            # host, asked for 120 a second, it produced 137 -- with a median
            # gap of 4.6ms against the 8.3ms it had been asked for, a worst of
            # 23ms, and one frame in ten arriving more than half a frame late.
            # Frames in clumps, which is what "they do not arrive at a steady
            # rate" is from the other end, and what every native streaming
            # client calls frame pacing and does on the way in.
            #
            # drop-only on purpose. videorate will otherwise repeat a frame to
            # fill a gap, and a repeated frame is a real frame to the encoder:
            # bitrate spent saying nothing changed, on a desktop that is still
            # for most of its life. Dropping needs no frame held back either,
            # so this costs no delay -- it only ever discards something that
            # arrived too early for its slot.
            # (ANY) matters and is not decoration. This filter exists to pin
            # the frame rate and nothing else, but `video/x-raw` on its own
            # also says "in system memory" -- which is true of ximagesrc and
            # false of d3d11screencapturesrc, whose frames are already on the
            # GPU. Without (ANY) the capture cannot link to the converter at
            # all: "d3d11convert0 can't handle caps video/x-raw,
            # framerate=(fraction)30/1". Saying (ANY) leaves the memory alone
            # and constrains only what this line is for.
            f"! video/x-raw(ANY),framerate={cfg.fps}/1 "
            f"! {convert} "
            f"! {encoder} "
            f"{profile}"
            f"! {parser} config-interval=-1 "
            # A tee, and the second branch is the whole reason this exists.
            #
            # A browser that decodes the picture itself wants whole access
            # units with start codes -- the shape an encoder produces and a
            # WebCodecs decoder takes. What goes out on the media track is the
            # same frames cut into RTP packets, and putting them back together
            # in a browser turned out to need machinery no browser wants to
            # lend: an encoded transform needs a receiver that has not started
            # yet, stops delivering when it is attached to one that has, and
            # never delivers again once it is taken off. The client this is
            # modelled on does not use it either -- it carries frames on a
            # data channel, which is what this branch is for.
            f"! tee name=frames "
            f"frames. ! queue max-size-buffers=0 max-size-bytes=0 "
            f"max-size-time=0 "
            f"! video/x-{'h265' if hevc else 'h264'},"
            f"stream-format=byte-stream,alignment=au "
            f"! appsink name=fsink emit-signals=true sync=false "
            f"max-buffers=8 drop=true "
            f"frames. ! queue max-size-buffers=0 max-size-bytes=0 "
            f"max-size-time=0 "
            f"! {payloader} pt=96 config-interval=-1 aggregate-mode=zero-latency "
            f"mtu={cfg.rtp_mtu} "
            f"! application/x-rtp,media=video,encoding-name={encoding},"
            f"payload=96,clock-rate=90000 "
            # These buffers are RTP packets, not frames, and that is the
            # whole reason this number is what it is.
            #
            # It was 4. A packet is at most `rtp_mtu` bytes, so four of them
            # is around five kilobytes -- and a keyframe is hundreds of
            # kilobytes that the payloader pushes as one burst, hundreds of
            # packets arriving inside a millisecond of each other. The
            # consumer is `_forward`, which is Python: it copies the buffer
            # once per guest and pushes it. Fast, but not faster than a burst
            # arriving, and with drop=true everything that did not fit was
            # thrown away.
            #
            # So a keyframe arrived at the guests with holes in it, they could
            # not decode it, and they asked for another keyframe -- which was
            # dropped the same way. That is the storm in the log: "guests are
            # losing the picture faster than sending keyframes can fix". It
            # gets worse with each guest added, because `_forward` does a
            # copy per guest before it can take the next packet, which is why
            # it showed up as a problem that needed two clients to see.
            #
            # A thousand packets is over a megabyte of keyframe at any MTU
            # this offers, and costs nothing when it is not needed: the queue
            # only holds what has not been handed out yet, which in the steady
            # state is nothing. drop=true stays, because a consumer that has
            # genuinely stopped must not grow this without limit -- it is now
            # a last resort rather than something every keyframe hits.
            f"! appsink name=vsink emit-signals=true sync=false "
            f"max-buffers={VIDEO_SINK_PACKETS} drop=true"
        )
        self._description = description
        self._audio_description = self._audio_branch() if cfg.audio else ""
        self._build(with_audio=bool(self._audio_description))

    def _audio_branch(self):
        """The game's sound, or nothing at all if this machine cannot give it.

        The queue is not decoration. Without one, every element after pulsesrc
        runs on pulsesrc's own streaming thread -- including the appsink
        callback, which hands each packet to every guest's pipeline in turn.
        So a moment's hesitation anywhere in that chain, in any guest, stops
        the capture being read; PulseAudio's ring buffer overruns; and the
        samples in it are gone before anything encodes them. GStreamer says so
        plainly -- "Can't record audio fast enough" -- and the guest hears it
        as static, because a hole in the sound is exactly what the decoder has
        to invent its way across. 2.74% of one guest's samples were invented.

        A queue puts a thread boundary there: pulsesrc only ever fills the
        queue, and the encoding and the handing-out happen on the other side
        of it. Leaky downstream, so a genuinely stalled encoder drops the
        oldest audio rather than blocking the capture again -- the whole point
        is that nothing downstream can ever hold up the microphone.
        """
        cfg = self.cfg
        for element in ("opusenc", "rtpopuspay"):
            if not Gst.ElementFactory.find(element):
                log.warning("no %s: the session will be silent", element)
                return ""
        sound = pick_sound()
        if sound is None:
            log.warning("no loopback capture on this machine (none of %s): "
                        "the session will be silent",
                        ", ".join(name for name, _line in SOUNDS))
            return ""
        sound_element, sound_line = sound
        # Kept so the setup page can say which of them is being used. On a
        # machine with several it is the first thing worth knowing when the
        # sound is wrong, and it was only ever in the log.
        self.source_sound_name = sound_element
        log.info("recording the sound with %s", sound_element)
        return (
            f" {sound_line.format(device=cfg.audio_device)} "
            f"! queue name=soundq max-size-time={cfg.audio_queue_ms}000000 "
            f"max-size-buffers=0 max-size-bytes=0 leaky=downstream "
            f"! audioconvert ! audioresample "
            f"! audio/x-raw,rate=48000,channels=2 "
            f"! opusenc bitrate={cfg.audio_bitrate_kbps * 1000} "
            f"frame-size={cfg.audio_frame_ms} inband-fec=true "
            f"! rtpopuspay pt=97 mtu={cfg.rtp_mtu} "
            f"! application/x-rtp,media=audio,encoding-name=OPUS,payload=97,clock-rate=48000 "
            # Sixteen Opus packets is a third of a second, and the same
            # argument as the video sink applies with less force: the burst is
            # smaller, but a pause in `_forward` still costs whole packets,
            # and a lost Opus packet is a hole in somebody's voice. Guests
            # were reporting two to four percent of their samples invented on
            # a local network, which is not what a local network does.
            f"! appsink name=asink emit-signals=true sync=false "
            f"max-buffers={AUDIO_SINK_PACKETS} drop=true")

    def _build(self, with_audio):
        description = self._description
        if with_audio:
            description += self._audio_description
        log.debug("pipeline: %s", description)
        try:
            self.pipeline = Gst.parse_launch(description)
        except Exception:
            # Said at a level somebody will see. A description that will not
            # build leaves this host with no capture and every guest met by
            # "the host could not start your video" -- and until now the only
            # thing in the log was gstreamer's own one-line complaint, which
            # names an element without saying what was around it.
            log.error("this pipeline would not build, so there is no capture: "
                      "%s", description)
            raise
        self.encoder = self.pipeline.get_by_name("enc")
        self.vsink = self.pipeline.get_by_name("vsink")
        self.fsink = self.pipeline.get_by_name("fsink")
        self.asink = self.pipeline.get_by_name("asink")
        self.has_audio = self.asink is not None
        # The caps the guests' pipelines have to be told about. They are not
        # known until the first buffer arrives.
        self.video_caps = None
        self.audio_caps = None
        self.vsink.connect("new-sample", self._on_video)
        if self.fsink is not None:
            self.fsink.connect("new-sample", self._on_frame)
        if self.asink is not None:
            self.asink.connect("new-sample", self._on_audio)
        self._watch_the_capture()

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        bus.connect("message::warning", self._on_warning)

    @property
    def mutations(self):
        """The executor to hand to run_in_executor. Always the current one."""
        return self.worker.executor

    def reset_worker(self, why="it stopped responding"):
        """Replace the pipeline worker after it has stopped making progress.

        One thread owns every change to the pipeline, which is what keeps the
        GPU driver from being used from two places at once. The cost is that if
        that thread ever stops -- wedged in a driver call, or gone altogether,
        which is what happened here: the process was left with no worker at all
        and every later job queued behind nothing for ever -- then nothing
        works again until a restart. It "worked initially" and never after.

        A queue nobody is serving is worth abandoning. The old executor is left
        to its fate rather than waited for, precisely because waiting is the
        thing that does not finish.
        """
        log.warning("replacing the pipeline worker: %s", why)
        self.worker.reset()

    def worker_alive(self, timeout=5.0):
        return self.worker.alive(timeout)

    # -- lifecycle ----------------------------------------------------------

    def start(self):
        self._glib_loop = GLib.MainLoop()
        self._thread = threading.Thread(target=self._glib_loop.run,
                                        name="gst-mainloop", daemon=True)
        self._thread.start()
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            # A screen that is no longer the screen it was.
            #
            # d3d11screencapturesrc is given an HMONITOR, and Windows
            # invalidates those whenever the display arrangement changes --
            # which making a virtual display and then setting its mode does,
            # twice. The handle can be good when the pipeline is built and
            # stale by the time it starts, and then the capture refuses, and
            # then the session cannot be restored at all. Somebody's PIN
            # stopped working because of it.
            #
            # Losing which screen is sent is a far smaller thing than losing
            # the session, so the choice is dropped and the primary is used.
            # Same rule as the audio below, and for the same reason.
            if "monitor-handle=" in (self._description or ""):
                log.warning("the capture refused the screen it was given (the "
                            "handle went stale, which a display being added or "
                            "resized does); sending the main screen instead")
                self.pipeline.set_state(Gst.State.NULL)
                self._description = re.sub(r" monitor-handle=\d+", "",
                                           self._description)
                self._build(with_audio=bool(self._audio_description))
                return self.start()
            # Refused outright, which is what a missing or busy sound server
            # produces: pulsesrc answers "Connection refused" and takes the
            # whole pipeline down with it. Losing sound must not cost the
            # picture, and this branch is the one that actually fires -- the
            # retry below only ever covered a pipeline that stalled on its way
            # to PLAYING, so an audio device that failed immediately killed the
            # session and the session was the thing everybody wanted.
            if self.has_audio:
                log.warning("the pipeline refused to start with audio; "
                            "retrying without it")
                self.pipeline.set_state(Gst.State.NULL)
                self._build(with_audio=False)
                return self.start()
            raise RuntimeError("the capture pipeline refused to start")
        # set_state is asynchronous: it returns ASYNC while the pipeline is
        # still getting there. Returning here would let a guest who joins a
        # second later attach a peer to a pipeline that is not yet PLAYING,
        # and that peer never fires on-negotiation-needed -- the join succeeds,
        # no offer is ever made, and the guest sits on a black screen with no
        # error anywhere. Wait for it.
        change, state, _pending = self.pipeline.get_state(START_TIMEOUT)
        if change == Gst.StateChangeReturn.FAILURE or state != Gst.State.PLAYING:
            self.pipeline.set_state(Gst.State.NULL)
            # A missing or busy sound device must not cost anybody the picture.
            # Losing audio is a disappointment; losing the session is a
            # failure, and one that would be hard to diagnose from a guest's
            # black screen.
            if self.has_audio:
                log.warning("the pipeline would not start with audio; "
                            "retrying without it")
                self._build(with_audio=False)
                return self.start()
            raise RuntimeError(
                f"the capture pipeline stalled reaching PLAYING (got {state.value_nick})")
        # Ask the screen for the frame rate that is being sent.
        #
        # A screen that draws 59 frames a second cannot be captured at 120:
        # the extra frames are the same picture twice, costing bitrate and
        # buying no smoothness at all. This host was sitting at 59Hz on a
        # panel that offers 143, so choosing a higher frame rate in the
        # picture settings did nothing anybody could see.
        #
        # So the frame rate carries the refresh rate with it. Only ever
        # downwards to what the screen actually offers, never above what was
        # asked for, and only on the screen being captured -- a second monitor
        # somebody is working on is not this host's to reconfigure.
        self._match_refresh(getattr(self, "_chosen_monitor", None))
        # Which Windows desktop the input is on, and whether this host could
        # follow it. Said once per capture because it is the difference
        # between "the stream went black" and "the machine locked and this
        # host is not allowed to see the sign-in screen" -- which look
        # identical from a phone, and only one of them is a fault.
        if windesktop.supported():
            log.info("desktop: %s", windesktop.explain())
        # What the colour actually negotiated as.
        #
        # "The colouring feels kind of flat" has two ordinary causes and they
        # want opposite fixes, so guessing between them is worth nothing. A
        # desktop is full-range RGB; the encoder wants YUV. If the conversion
        # compresses 0-255 into 16-235 and the browser is not told, the
        # picture comes back with its contrast squeezed out -- flat. And if
        # the matrix disagrees at either end -- BT.601 against BT.709 -- the
        # hues go with it. Neither is visible from here without asking, and
        # nothing in this pipeline has ever said which it picked.
        try:
            pad = self.encoder.get_static_pad("sink") if self.encoder else None
            caps = pad.get_current_caps() if pad is not None else None
            if caps is not None and caps.get_size():
                one = caps.get_structure(0)
                log.info("colour: %s, colorimetry %s",
                         one.get_value("format"),
                         one.get_value("colorimetry") or "(not stated)")
        except Exception:
            log.debug("could not read the colour the encoder negotiated",
                      exc_info=True)
        log.info("capture running: %dx%d @%d, %d kb/s, %s, audio %s",
                 self.sending_width, self.sending_height, self.cfg.fps,
                 self.cfg.bitrate_kbps, self.encoder_name,
                 "on" if self.has_audio else "off")
        # Asking for more than the desktop has is not a sharper picture. It is
        # the same pixels scaled up, carried at whatever bitrate was set for
        # them, and it looks like the stream being soft for no reason -- which
        # is a thing somebody will spend an evening on. Said once, here, where
        # the two numbers are both known.
        try:
            # The virtual screen, when there is one: it was made at exactly
            # the size being sent, so comparing against the machine's own
            # panel would warn about an upscale that is not happening.
            desktop = ((self.vdisplay.width, self.vdisplay.height)
                       if getattr(self, "vdisplay", None) is not None
                       else screen.desktop_size(self.cfg.display))
        except Exception:
            desktop = None
        if desktop and (self.sending_width > desktop[0]
                        or self.sending_height > desktop[1]):
            log.warning("the picture asked for (%dx%d) is larger than this "
                        "machine's desktop (%dx%d), so it is being scaled up: "
                        "no more detail than %dx%d, at the bitrate of the "
                        "bigger one. Raise the desktop resolution to get a "
                        "genuinely sharper stream.",
                        self.sending_width, self.sending_height,
                        desktop[0], desktop[1], desktop[0], desktop[1])
        # A session almost always opens before anybody joins it, and one that
        # is restored at boot may never be joined at all -- which is the case
        # this was written for. Arming only when the last guest leaves meant
        # the clock was never started on a session nobody had joined yet, so
        # the console went on encoding for nobody exactly as before. Measured
        # after deploying the first version of this: 46% of a core, still.
        if not self.watchers():
            self._arm_idle()

    def _match_refresh(self, chosen):
        """Put the captured screen at the frame rate being sent, if it can.

        `chosen` is the monitor row the capture settled on, or None for
        whatever the capture picks itself. Never raises: a refresh rate that
        will not change is a less smooth picture, not a broken session.
        """
        if not getattr(self.cfg, "match_refresh", True):
            return
        if self.vdisplay is not None:
            # It was asked for at the right rate. That is not the same as
            # running at it: the driver is free to attach the monitor at a
            # mode of its own, and this host then captures a screen refreshing
            # at one rate while asking the encoder for another -- which is a
            # beat, and beats are what a pulse is made of.
            #
            # Checked rather than assumed, and said either way, because "made
            # at 60" was written here as a reason not to look.
            try:
                name = self.vdisplay.device_name
                hz = vdisplay.current_refresh(name) if name else None
                wanted = int(self.cfg.fps)
                if hz is None:
                    log.info("the virtual screen will not say what rate it "
                             "is running at")
                elif hz == wanted:
                    log.info("the virtual screen is running at %dHz, which is "
                             "what is being sent", hz)
                else:
                    log.warning("the virtual screen is running at %dHz while "
                                "%d frames a second are being sent: every "
                                "%.1f seconds one frame has nowhere to go, "
                                "which is a stutter on a beat",
                                hz, wanted,
                                1.0 / abs(hz - wanted) if hz != wanted else 0)
                    if vdisplay.set_refresh(name, wanted):
                        log.info("the virtual screen was moved to %dHz", wanted)
            except Exception:
                log.debug("could not check the virtual screen's rate",
                          exc_info=True)
            return
        try:
            wanted = int(self.cfg.fps)
            device = chosen[5] if chosen else None
            if device is None:
                # Whatever Windows calls the primary, which is what the
                # capture will have taken.
                primary = [m for m in vdisplay.monitors() if m[4]]
                device = primary[0][5] if primary else None
            if not device:
                return
            hz = vdisplay.best_refresh(device, wanted)
            if hz is None:
                return
            if hz < wanted:
                log.info("%s offers at most %dHz at this size, so %d frames a "
                         "second is sending the same picture twice; %d would "
                         "look the same and cost less", device, hz, wanted, hz)
            # Only ever upwards. A desktop's refresh rate belongs to whoever
            # sits at it, and lowering it to match a modest stream would make
            # their screen worse to be kind to a guest.
            current = vdisplay.current_refresh(device)
            if current is not None and hz <= current:
                return
            vdisplay.set_refresh(device, hz)
        except Exception:
            log.debug("could not match the screen's refresh rate",
                      exc_info=True)

    def stop(self):
        """Stop capturing. Must not block, whatever state anything is in.

        The pipeline is silenced first and the worker is never waited for. It
        used to be waited for, and the case that matters is precisely the one
        where it will not answer: a stage is replaced *because* its worker
        wedged, so stopping it hung, the old pipeline kept capturing, and the
        replacements piled up. Five ximagesrc threads were found running at
        once, which is five screen captures competing for one GPU.
        """
        # Silence the sources first. Taking a whole pipeline to NULL can block
        # for a long time when webrtcbin has live transports in it, and an
        # abandoned pipeline that is still capturing costs a screen grab and an
        # encode for as long as the process lives. Stopping the two sources is
        # immediate and ends that cost even if the rest hangs -- by name,
        # because which elements they are depends on the machine.
        self._cancel_idle()
        for name in ("capture", "sound"):
            element = self.pipeline.get_by_name(name)
            if element is not None:
                try:
                    element.set_state(Gst.State.NULL)
                except Exception:
                    pass
        # And the screen that was made for this capture, if one was. Before
        # the slow part below rather than after it, because that part is
        # allowed not to finish -- and a monitor left behind is one somebody
        # finds in their display settings later and cannot explain. It would
        # go when the process ends whatever happens, since the driver ties it
        # to the open handle, but a recapture would otherwise make a second
        # one first and leave the room briefly with two.
        if getattr(self, "vdisplay", None) is not None:
            try:
                self.vdisplay.close()
            except Exception:
                pass
            self.vdisplay = None
        for element in (self.encoder,):
            if element is not None:
                try:
                    element.set_state(Gst.State.NULL)
                except Exception:
                    pass
        # Then the pipeline itself -- and somebody has to hold it until that
        # finishes, for the same reason Peer.detach does.
        #
        # This is where the capture leaked. NULL on a pipeline this size is
        # asynchronous, nothing waited for it, and `_recapture` drops its
        # reference to the whole Stage the moment stop() returns. The
        # transition was left in flight with no owner, so GStreamer's threads
        # kept the pipeline alive for ever -- and this pipeline holds
        # `vah264enc`, the hardware encoder, whose VA context brings a set of
        # Mesa driver threads with it. Measured on 2026-09-12: threads went
        # 25 at startup, 48, then 96, with 64 MB malloc arenas behind them, and
        # the process put on well over a gigabyte in nineteen minutes across
        # four recaptures. The `gst_object_unref: assertion ref_count > 0`
        # lines in the journal sit exactly on "encoding with vah264enc",
        # which is the previous encoder still being alive as the next one is
        # built.
        #
        # A recapture happens whenever the picture or the codec changes -- a
        # second guest arriving and forcing H.264 does it -- so this was the
        # expensive leak, not the per-guest one.
        #
        # Sources are already silenced above, so the GPU cost has stopped
        # whatever this thread goes on to find. Nothing waits on it.
        # Taken off the Stage, not merely borrowed from it.
        #
        # This is the other half of the fault Peer.detach had, and the half
        # that was left. stop() took a local reference to the pipeline and
        # left self.pipeline, self.encoder, self.vsink and self.asink where
        # they were -- so when _recapture dropped the Stage, all four were
        # released by the garbage collector, at whatever moment it next ran,
        # with the teardown thread possibly still working.
        #
        # That is exactly what the third Windows crash showed. faulthandler
        # printed "Garbage-collecting" above a frame in _on_audio: the
        # collector had run at an ordinary allocation, found a wrapper whose C
        # object was already gone, and the process died. It is why the crash
        # kept appearing somewhere new each time -- the place it lands has
        # nothing to do with the cause.
        pipeline, self.pipeline = self.pipeline, None
        extras = [self.encoder, self.vsink, self.asink]
        self.encoder = self.vsink = self.asink = None
        # The caps came off those sinks and outlive them otherwise.
        self.video_caps = self.audio_caps = None

        def see_it_to_null():
            nonlocal pipeline
            started = time.monotonic()
            try:
                pipeline.set_state(Gst.State.NULL)
                _, state, _ = pipeline.get_state(TEARDOWN_TIMEOUT)
            except Exception as exc:
                log.warning("could not stop the pipeline cleanly: %s", exc)
                return
            finally:
                # The elements first, then the pipeline that owns them -- the
                # same order Peer.detach uses, and for the same reason.
                extras.clear()
                pipeline = None
            took = time.monotonic() - started
            if state != Gst.State.NULL:
                log.warning("the capture pipeline did not reach NULL in "
                            "%.1fs (stuck at %s); its encoder and driver "
                            "threads are still held", took, state)
            elif took > 1.0:
                log.info("the capture pipeline took %.1fs to stop", took)

        threading.Thread(target=see_it_to_null, name="teardown-capture",
                         daemon=True).start()
        for peer_id in list(self.peers):
            peer = self.peers.pop(peer_id, None)
            if peer is not None:
                try:
                    peer.detach()
                except Exception:
                    pass
        if self._glib_loop:
            self._glib_loop.quit()
        self.worker.shutdown(wait=False)

    def ensure_playing(self, timeout=5 * Gst.SECOND):
        """Put the pipeline back to PLAYING if something knocked it out.

        A GStreamer error is posted against the whole pipeline, not the branch
        that raised it -- so one guest's data channel failing left the capture
        stopped, and every later guest was refused with "webrtcbin would not
        follow the pipeline into PLAYING". The session stayed open and served
        nobody.
        """
        # stop() takes the pipeline off this Stage, and this runs on the
        # worker -- so a recapture can retire the Stage with one of these
        # already queued behind it. There is nothing to put back.
        pipeline = self.pipeline
        if pipeline is None:
            return False
        _change, state, _pending = pipeline.get_state(0)
        if state == Gst.State.PLAYING:
            return True
        log.warning("pipeline is %s; putting it back to PLAYING",
                    state.value_nick)
        pipeline.set_state(Gst.State.PLAYING)
        _change, state, _pending = pipeline.get_state(timeout)
        if state != Gst.State.PLAYING:
            log.error("pipeline would not return to PLAYING (%s)", state.value_nick)
            return False
        return True

    # -- peers --------------------------------------------------------------

    def watchers(self):
        """How many guests are actually being sent a picture.

        A peer built with media=False is a second controller on a machine that
        already has one: it has an input channel and no video, so it is not a
        reason to keep encoding.
        """
        return sum(1 for peer in self.peers.values() if getattr(peer, "media", True))

    def _cancel_idle(self):
        timer, self._idle_timer = self._idle_timer, None
        if timer is not None:
            timer.cancel()

    def _arm_idle(self):
        """Pause the capture in a little while, if nobody has arrived by then."""
        if not IDLE_CAPTURE:
            return
        self._cancel_idle()
        timer = threading.Timer(IDLE_AFTER,
                                lambda: self.worker.submit(self.idle_if_empty))
        timer.daemon = True
        self._idle_timer = timer
        timer.start()

    def idle_if_empty(self):
        """Take the capture to PAUSED, unless somebody turned up meanwhile.

        Runs on the worker, because every state change does: two threads in
        the GPU driver at once is something this process has segfaulted over.
        The count is re-read here rather than trusted from when the timer was
        armed -- thirty seconds is a long time in a session.
        """
        if self.watchers() or self._attaching:
            return False
        pipeline = self.pipeline
        if pipeline is None:
            return False
        _change, state, _pending = pipeline.get_state(0)
        if state != Gst.State.PLAYING:
            return False
        log.info("no guests for %.0fs; pausing the capture", IDLE_AFTER)
        pipeline.set_state(Gst.State.PAUSED)
        return True


    def add_peer(self, peer_id, on_signal, configure=None, media=True):
        """Attach one guest. `on_signal(kind, payload)` is called on the asyncio loop.

        `configure` runs after the peer exists and before it is wired up, which
        is the only window in which its callbacks can be set without racing the
        first thing it does.

        `media=False` builds a peer with the input channel and no picture, for
        a second controller on a machine that already has one.
        """
        # Before ensure_playing below, not after: an idle that fires between
        # the two would pause the pipeline this guest is about to be given.
        # The counter is what keeps it held off for the whole attach, which
        # takes long enough to matter -- there is a warning below for one that
        # takes over a second.
        self._cancel_idle()
        self._attaching += 1
        try:
            return self._attach_peer(peer_id, on_signal, configure, media)
        finally:
            self._attaching -= 1

    def _attach_peer(self, peer_id, on_signal, configure, media):
        started = time.monotonic()
        # A peer left over under this name is wreckage, not a guest: the only
        # way one survives is an attach that failed partway. Clear it out
        # rather than refusing, because refusing makes the slot unusable for
        # the rest of the session -- every later guest met
        # "peer slot0 is already attached" and got nothing.
        stale = self.peers.pop(peer_id, None)
        if stale is not None:
            log.warning("peer %s was still registered; discarding it", peer_id)
            try:
                stale.detach()
            except Exception:
                pass

        # The capture still has to be running to have anything to hand out.
        # It is no longer at the mercy of the guests, though: their pipelines
        # are their own, so this is now only about the capture itself.
        if not self.ensure_playing():
            raise RuntimeError("the capture pipeline is not running")

        peer = Peer(self, peer_id, on_signal, media=media)
        if configure is not None:
            configure(peer)
        try:
            peer.attach()
        except Exception:
            # Registered only once it is genuinely attached. Doing it first
            # meant a failure here left the name taken by something that had
            # never worked and would never be cleaned up.
            try:
                peer.detach()
            except Exception:
                pass
            raise
        self.peers[peer_id] = peer
        took = time.monotonic() - started
        if took > 1.0:
            log.warning("peer %s took %.1fs to attach", peer_id, took)
        else:
            log.debug("peer %s attached in %.2fs", peer_id, took)
        # A guest who joins between keyframes sees nothing until the next one.
        # At a one-second interval that is a second of black, so ask for one now.
        self.force_keyframe()
        return peer

    def take_peer(self, peer_id, expected=None):
        """Unregister a peer and hand it back, without tearing it down yet.

        Freeing the name immediately matters: a guest who reloads is replaced
        within milliseconds, and the new peer wants the same slot id while the
        old one is still shutting down.

        `expected` is which peer the caller means. A name is a slot number and
        a slot can change hands, so a caller holding an old peer could unhook
        whoever is in that slot now -- silently, because popping a dict says
        nothing. The peer that was unhooked keeps running and keeps its
        transports, so its guest sees a picture that stops with no error at
        either end, and two guests taking the name from each other is a pair
        of pictures that freeze in turn. Say which one you mean and a stale
        caller takes nothing.
        """
        held = self.peers.get(peer_id)
        if expected is not None and held is not None and held is not expected:
            log.warning("peer %s was asked for by somebody holding an older "
                        "one; leaving the current peer where it is", peer_id)
            return None
        peer = self.peers.pop(peer_id, None)
        if peer is not None and not self.watchers():
            # The last guest just left. Nothing is stopped yet -- a reload is
            # back within milliseconds, and IDLE_AFTER is what tells the two
            # apart.
            self._arm_idle()
        return peer

    def remove_peer(self, peer_id):
        peer = self.take_peer(peer_id)
        if peer:
            peer.detach()
        return peer is not None

    def _reset_keyframe_limit(self):
        """The state request_keyframe needs, in one place.

        One method rather than two assignments in __init__ because the fake
        Stage in the tests borrows request_keyframe and used to set this state
        by hand: adding a counter here broke that test, which is the polite
        version of what happens when a stand-in is built from an assumption
        about what the real object holds. Now there is one initialiser and
        anything that borrows the method can call it.
        """
        self._keyframe_tokens = float(KEYFRAME_BURST)
        self._keyframe_filled = time.monotonic()
        self._keyframes_refused = 0

    def request_keyframe(self, who="", now=None):
        """A guest has lost the picture and wants a fresh start.

        Rate-limited, because the encoder is shared: four guests on a bad
        connection all asking at once would otherwise turn the stream into
        keyframes, which is the one thing guaranteed to make a struggling link
        worse. Two guests did exactly that -- see KEYFRAME_MIN_GAP.

        A bucket rather than a gap, so that an isolated request is answered at
        once and only sustained demand is refused. Waiting is what this was
        written to avoid; spending the whole bitrate on recovery is what it
        turned into.
        """
        # The clock is an argument so a test can drive it. Rate limiters are
        # exactly the code where "sleep and hope" makes a slow, flaky test
        # that proves less than it appears to.
        now = time.monotonic() if now is None else now
        self._keyframe_tokens = min(
            float(KEYFRAME_BURST),
            self._keyframe_tokens
            + (now - self._keyframe_filled) / KEYFRAME_MIN_GAP)
        self._keyframe_filled = now
        if self._keyframe_tokens < 1.0:
            # Refused, and counted. A storm used to be invisible from here:
            # the log only ever recorded the requests that were granted, so a
            # host spending half its bitrate on recovery looked like a host
            # handing out the occasional keyframe.
            self._keyframes_refused += 1
            return
        if self._keyframes_refused:
            log.info("peer %s asked for a keyframe after losing the picture "
                     "(and %d request(s) were refused since the last one; "
                     "guests are losing the picture faster than sending "
                     "keyframes can fix)", who, self._keyframes_refused)
            self._keyframes_refused = 0
        else:
            log.info("peer %s asked for a keyframe after losing the picture", who)
        self._keyframe_tokens -= 1.0
        self.worker.submit(self.force_keyframe)

    def force_keyframe(self):
        # The encoder is taken off this Stage by stop(), and a keyframe may
        # already be queued on the worker when that happens -- a guest asking
        # for one at the moment a recapture begins is not unusual.
        encoder = self.encoder
        if encoder is None:
            return
        pad = encoder.get_static_pad("src")
        if pad:
            pad.send_event(GstVideo.video_event_new_upstream_force_key_unit(
                Gst.CLOCK_TIME_NONE, True, 0))

    # -- bus ----------------------------------------------------------------

    def _on_error(self, _bus, message):
        err, debug = message.parse_error()
        log.error("pipeline error: %s (%s)", err.message, debug)
        # A screen the capture will not take.
        #
        # This arrives here rather than as a refusal from set_state, which is
        # why dropping the handle in start() was not enough: the state change
        # is asynchronous, so the capture fails afterwards, on the bus, and
        # ensure_playing then retried the same broken description for ever.
        # The log filled with "Failed to prepare capture object" and the
        # session could never be restored -- which is somebody's PIN not
        # working, with nothing anywhere connecting the two.
        #
        # Dropped once. If it fails again without a handle it is a real
        # failure and the usual retry is the right answer.
        if ("prepare capture" in (err.message or "").lower()
                and "monitor-handle=" in (self._description or "")):
            log.warning("the capture will not take the screen it was given "
                        "(the handle went stale, which adding or resizing a "
                        "display does); sending the main screen instead")
            self._description = re.sub(r" monitor-handle=\d+", "",
                                       self._description)
            self.worker.submit(self._rebuild_without_screen)
            return
        # This bus carries the capture only. A guest's pipeline has its own bus
        # and its own errors, which is the point of them being separate.
        self.worker.submit(self.ensure_playing)

    def _rebuild_without_screen(self):
        """Start again with the screen choice dropped. Never raises."""
        try:
            if self.pipeline is not None:
                self.pipeline.set_state(Gst.State.NULL)
            self._build(with_audio=bool(self._audio_description))
            self.start()
        except Exception:
            log.exception("could not start the capture without a screen "
                          "choice either")

    # -- fanning the encoded stream out to the guests ------------------------

    def _on_video(self, sink):
        return self._forward(sink, "video")

    def _on_audio(self, sink):
        return self._forward(sink, "audio")

    def _on_frame(self, sink):
        """One whole encoded frame, for the guests decoding it themselves.

        Pulled and handed out even when nobody wants it, because the
        alternative is an appsink that fills and blocks the tee, which stops
        the branch everybody else is watching. drop=true on the sink keeps
        that bounded; this keeps it empty.
        """
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buffer = sample.get_buffer()
        key = not buffer.has_flags(Gst.BufferFlags.DELTA_UNIT)
        # Microseconds, which is what an EncodedVideoChunk takes. The clock is
        # the capture's, so the differences are real elapsed time even though
        # the two machines have never agreed what time it is.
        stamp = 0
        if buffer.pts != Gst.CLOCK_TIME_NONE:
            stamp = buffer.pts // 1000
        ok, info = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        try:
            data = bytes(info.data)
        finally:
            buffer.unmap(info)
        behind = 0
        for peer in list(self.peers.values()):
            try:
                peer.send_frame(data, key, stamp)
                behind = max(behind, getattr(peer, "channel_behind", 0))
            except Exception as exc:
                log.debug("peer %s would not take a frame: %s", peer.id, exc)
        self._ease_the_rate(behind)
        return Gst.FlowReturn.OK

    def rate_now(self):
        """What the encoder is actually being asked for, in kb/s.

        The pacer's rate: what is being produced is what has to be put on the
        wire, and anything slower turns the pacer itself into the bottleneck.
        """
        if self._rate_now:
            return self._rate_now
        return max(1, int(self.cfg.bitrate_kbps))

    def frame_queue_limit(self):
        """How many bytes may sit in a send queue before frames are skipped.

        From what was *asked* for, never from what is being sent right now.
        This used to read `self._rate_now`, which is the number this limit
        exists to control: lowering the rate lowered the limit, so the same
        backlog looked worse, so it lowered the rate again. One momentary
        backlog of 44843 bytes took it 1708 -> 1281 -> 960 -> 720 -> 540 ->
        405 -> 400 inside a single second. A controller whose output feeds its
        own input has to be broken somewhere, and this is the somewhere.
        """
        rate = max(1, int(self.cfg.bitrate_kbps))
        return int(rate * 1000 / 8 * FRAME_QUEUE_SECONDS)

    def _ease_the_rate(self, behind):
        """Match the encoder to a link that cannot carry what it is given.

        Two signals, because one of them turned out to be blind.

        A send queue that will not drain is the obvious one: the bytes simply
        sit there, and a guest watching sees the picture slow down while the
        queue fills and speed up while it drains, which is exactly how it was
        described. But that queue read empty for a guest on mobile data who
        was receiving thirty-six of every sixty frames sent -- an empty queue
        only proves the host handed the bytes to SCTP. So the browser's own
        count is the other signal, and the more trustworthy one; see
        `note_arrivals`.

        This half is the emergency: a queue over the limit is already delay
        somebody is watching, so it comes down at once rather than waiting for
        the next report. Climbing back belongs to `note_arrivals`, which is
        the only place with evidence that the link is actually carrying
        anything.
        """
        limit = self.frame_queue_limit()
        if behind > limit:
            self._rate_calm = 0
            # Where it broke, so the climb can stop below it rather than
            # walking into the same wall every twenty seconds.
            now = self._rate_now or self._ceiling()
            self._rate_broke = min(self._rate_broke or now, now)
            self._set_rate(down=True,
                           why="%d bytes are waiting to be sent and %d is the "
                               "most worth keeping" % (behind, limit))

    def note_arrivals(self):
        """A guest has said how much of the picture is reaching it.

        Both halves of the climb and the fall live here, on the guest's
        once-a-second report, so the rate moves at most once a second in
        either direction.

        That cadence is the point. The climb used to run off a count of
        frames, which on a link that was behaving meant a new bitrate every
        seven hundred milliseconds -- and every one of those is an NVENC
        reconfiguration in the middle of a stream. Asking the encoder to
        change its mind that often is not free, and it was being asked on no
        evidence at all beyond "nothing has gone wrong for forty frames".
        """
        arriving = self._arriving()
        if arriving < FRAMES_ARRIVING_LOW:
            self._rate_calm = 0
            self._set_rate(down=True,
                           why="only %.0f%% of the frames sent are arriving"
                               % (arriving * 100))
            return
        if arriving < FRAMES_ARRIVING_GOOD:
            # Between the two there is nothing to do. A band rather than a
            # line, because a rate that is exactly right still reports the odd
            # bad second, and an encoder that answers every one of those
            # oscillates for ever.
            return
        self._rate_calm += 1
        # A calm report is also evidence that whatever broke it may not any
        # more, so the memory of that fades -- slowly, and only while things
        # are going well.
        if self._rate_broke:
            self._rate_broke = int(self._rate_broke * BITRATE_PROBE_FADE)
            if self._rate_broke >= self._ceiling():
                self._rate_broke = None
        if self._rate_calm < BITRATE_CALM:
            return
        self._rate_calm = 0
        self._set_rate(down=False,
                       why="%d seconds of the picture arriving whole"
                           % BITRATE_CALM)

    def _ceiling(self):
        """The most this encoder may be asked for, whoever is watching.

        The setting, unless somebody is being sent whole frames down a data
        channel -- see DATA_CHANNEL_CEILING_KBPS for why that is a different
        number and not a smaller version of the same one.
        """
        want = int(self.cfg.bitrate_kbps)
        for peer in list(self.peers.values()):
            if getattr(peer, "frames_wanted", False):
                return min(want, DATA_CHANNEL_CEILING_KBPS)
        return want

    def apply_ceiling(self):
        """Bring the encoder under the ceiling at once, not by degrees.

        The adaptive path walks the rate down a quarter at a time on evidence
        from the guest, and that evidence arrives once a second -- far too
        slow when the pipeline has just been built at 62500 kb/s and the
        channel that has to carry it fails within twenty. So the moment a
        guest asks for whole frames, the rate is clamped rather than eased.
        """
        want = max(BITRATE_FLOOR_KBPS, self._ceiling())
        now = self._rate_now if self._rate_now is not None \
            else int(self.cfg.bitrate_kbps)
        if now <= want:
            return
        encoder = self.encoder
        if encoder is None:
            return
        try:
            encoder.set_property("bitrate", want)
        except Exception:
            log.debug("this encoder will not change its bitrate mid-stream",
                      exc_info=True)
            return
        log.info("a guest is being sent whole frames, so the picture comes "
                 "down to what a data channel can carry: %d kb/s -> %d kb/s",
                 now, want)
        self._rate_now = want

    def _arriving(self):
        """The worst-off guest's share of the frames sent to it.

        The worst rather than the average: the encoder is shared, so a rate
        that suits three guests and drowns the fourth is a rate that is too
        high. Guests that have never reported count as fine, which is what
        they are until they say otherwise.
        """
        shares = [getattr(peer, "frames_arriving", 1.0)
                  for peer in list(self.peers.values())
                  if getattr(peer, "frames_wanted", False)]
        return min(shares) if shares else 1.0

    def _floor(self):
        """The worst picture worth sending, rather than the smallest number.

        A share as well as an absolute, because 1500 kb/s is a fair floor at
        720p and a smear at 1440p.

        A share of the *ceiling* rather than of the setting, which is not a
        detail. With a setting of 62500 and a data channel's ceiling of 8000,
        fifteen percent of the setting is 9375 -- above the ceiling -- so the
        floor clamped to the ceiling, floor and ceiling became the same
        number, and the rate could not be stepped down at all. A controller
        that cannot back off is how an association gets driven into an error
        state, which is the fault this ceiling was lowered to prevent.
        """
        roof = self._ceiling()
        return min(roof, max(BITRATE_FLOOR_KBPS,
                             int(roof * BITRATE_FLOOR_SHARE)))

    def _set_rate(self, down, why):
        """Move the encoder's bitrate one step, and say why."""
        encoder = self.encoder
        if encoder is None:
            return
        floor = self._floor()
        asked = max(floor, self._ceiling())
        if self._rate_now is None:
            self._rate_now = asked
        # One step a second at most, whatever asked for it. Without this the
        # queue-depth path fired per frame and took the rate to the floor in
        # under a second on one momentary backlog.
        now = time.monotonic()
        if down and now - self._rate_stepped < BITRATE_STEP_SECONDS:
            return
        if down:
            want = max(floor, int(self._rate_now * BITRATE_DOWN))
        else:
            # Never climb back into a rate that has been seen to break this
            # channel. Only on the way up: capping the step that *discovered*
            # it would make the fall deeper than the quarter it is meant to be.
            if self._rate_broke:
                asked = max(floor,
                            min(asked,
                                int(self._rate_broke * BITRATE_PROBE_SHARE)))
            if self._rate_now >= asked:
                return
            want = min(asked, int(self._rate_now * BITRATE_UP) + 1)
        if want == self._rate_now:
            return
        try:
            encoder.set_property("bitrate", want)
        except Exception:
            log.debug("this encoder will not change its bitrate mid-stream",
                      exc_info=True)
            return
        log.info("the link is %s: %d kb/s -> %d kb/s (%s)",
                 "narrower than the picture" if down
                 else "keeping up, so giving some back",
                 self._rate_now, want, why)
        self._rate_now = want
        self._rate_stepped = now

    def _note_gap(self, kind):
        """Say so when this host stops producing, rather than only suspecting it.

        A guest whose picture freezes cannot tell a packet lost on the way from
        a frame that was never encoded -- and the two want opposite fixes. The
        browser counts what it received, the browser's report says whether it
        asked for anything back, and this is the other half: what left here,
        and when it stopped. A gap of several frames is not normal at any frame
        rate this offers.
        """
        now = time.monotonic()
        last = self._last_sample.get(kind, 0.0)
        self._last_sample[kind] = now
        if not last or now - last < STALL_LOG_GAP:
            return
        self._stalls[kind] = self._stalls.get(kind, 0) + 1
        said = self._said_stall.get(kind, 0.0)
        if said and now - said < STALL_LOG_GAP_QUIET:
            return
        self._said_stall[kind] = now
        count = self._stalls[kind]
        log.warning("%s stopped for %.0f ms before this packet: nothing was "
                    "sent, so every guest's picture held still for it "
                    "(%d gap%s so far)",
                    kind, (now - last) * 1000, count, "" if count == 1 else "s")

    def _watch_the_capture(self):
        """Record when the desktop is really sampled, against what it claims.

        There are two clocks on every captured frame and they are not the
        same one. The timestamp is what the guest's browser is told to draw
        by; the moment the frame actually left the capture is when the
        picture inside it was true. A live GstBaseSrc with do-timestamp off
        derives the first from a frame counter, so it is perfectly even
        whatever the machine was doing -- which is how this host could report
        a flawless timeline over a picture that plainly was not.

        If the two disagree, the guest is being handed evenly spaced frames
        whose contents advanced by uneven amounts, and no buffer anywhere can
        fix that: the timestamps are wrong, not late. This measures the
        disagreement rather than assuming it either way.
        """
        self._grabbed = []
        self._grab_last = 0.0
        self._grab_stamps = []
        self._grab_pts = Gst.CLOCK_TIME_NONE
        element = self.pipeline.get_by_name("capture")
        pad = element.get_static_pad("src") if element is not None else None
        if pad is None:
            return
        pad.add_probe(Gst.PadProbeType.BUFFER, self._on_grabbed)

    def _on_grabbed(self, _pad, info):
        """One frame, straight off the capture. Kept as cheap as it looks.

        Both clocks again, and here for a second reason: everything between
        this pad and the guest can rewrite a timestamp, and one thing in that
        chain exists to. Comparing the stamp here with the stamp at the far
        end says whether the capture is telling the truth *and* whether
        anything downstream is flattening it back out.
        """
        now = time.monotonic()
        last, self._grab_last = self._grab_last, now
        if last:
            self._grabbed.append(now - last)
        try:
            buffer = info.get_buffer()
            if getattr(self, "_true_time", False):
                # The stamp this frame should have had. A pad probe can write
                # it -- checked on the machine, because PyGObject exposes no
                # make_writable and a buffer that refused the change would
                # leave the grid in place with nothing saying so.
                when = self._running_time()
                if when is not None:
                    buffer.pts = when
                    buffer.dts = Gst.CLOCK_TIME_NONE
            pts = buffer.pts
        except Exception:
            return Gst.PadProbeReturn.OK
        was, self._grab_pts = self._grab_pts, pts
        if (pts != Gst.CLOCK_TIME_NONE and was != Gst.CLOCK_TIME_NONE
                and pts > was):
            self._grab_stamps.append((pts - was) / float(Gst.SECOND))
        return Gst.PadProbeReturn.OK

    def _running_time(self):
        """Where this pipeline's clock is now, or None before it is running."""
        try:
            clock = self.pipeline.get_pipeline_clock()
            base = self.pipeline.get_base_time()
            if clock is None or base == Gst.CLOCK_TIME_NONE:
                return None
            now = clock.get_time()
            if now == Gst.CLOCK_TIME_NONE or now < base:
                return None
            return now - base
        except Exception:
            return None

    def _grab_report(self):
        """What the capture did, or "" if it has not been watched."""
        taken = self._grabbed
        if len(taken) < 30:
            return ""
        self._grabbed = []
        stamped = self._grab_stamps
        self._grab_stamps = []
        taken.sort()
        nominal = 1.0 / max(1, self.cfg.fps)
        rough = sum(1 for g in taken if g > nominal * 1.5 or g < nominal * 0.5)
        said = ("; the desktop was really sampled every %.1fms typical, "
                "worst %.0fms, %d of %d nowhere near %.1fms"
                % (taken[len(taken) // 2] * 1000, taken[-1] * 1000,
                   rough, len(taken), nominal * 1000))
        if len(stamped) >= 30:
            stamped.sort()
            odd = sum(1 for g in stamped
                      if g > nominal * 1.5 or g < nominal * 0.5)
            said += ("; and stamped at the capture every %.1fms typical, "
                     "worst %.0fms, %d of %d uneven"
                     % (stamped[len(stamped) // 2] * 1000, stamped[-1] * 1000,
                        odd, len(stamped)))
        return said

    def _note_pace(self, buffer):
        """How evenly frames are actually leaving, as a number rather than a guess.

        "The frames just do not arrive smoothly at a steady rate" is a real
        complaint and an unfalsifiable one from the other end: a guest is
        looking at the end of a chain with a capture, an encoder, a fan-out
        and a network in it, and any of those could be the uneven part. The
        browser already reports what it received. This is what was sent.

        Frames, not packets, and a frame is a change of presentation
        timestamp. Every packet carved out of one frame carries that frame's
        PTS, so the first packet with a new one is a new frame.

        The marker bit was tried first and is wrong here, which is worth
        writing down because it looks so obviously right: the marker means
        "last packet of this access unit", and measured through this host's
        own H.265 chain it arrives *twice* per frame -- 600 frames in, 1200
        markers out. Counting those said the encoder was producing 270
        frames a second when it had been asked for 120, which was a story
        about the instrument rather than the picture.

        Costs one comparison per packet and one clock read per frame, and
        says nothing until it has a full sample to speak about.
        """
        pts = buffer.pts
        if pts == Gst.CLOCK_TIME_NONE or pts == self._last_pts:
            return                      # still the frame we already counted
        was, self._last_pts = self._last_pts, pts
        now = time.monotonic()
        last = self._last_frame
        self._last_frame = now
        if not last:
            return
        self._gaps.append(now - last)
        # And the timeline the guest is handed, which is the one that decides
        # when their browser draws. Arrival can be as ragged as the network
        # likes and still look right, because a jitter buffer exists to absorb
        # exactly that. A ragged *timestamp* cannot be absorbed by anything:
        # it is an instruction to draw unevenly, and the browser obeys it.
        if was != Gst.CLOCK_TIME_NONE and pts > was:
            self._stamps.append((pts - was) / float(Gst.SECOND))
        if len(self._gaps) < PACE_SAMPLE:
            return
        gaps = sorted(self._gaps)
        stamps = sorted(self._stamps)
        self._gaps = []
        self._stamps = []
        total = sum(gaps)
        nominal = 1.0 / max(1, self.cfg.fps)
        # Late by more than half a frame is the threshold because that is
        # where a frame misses its slot on the guest's display and either
        # doubles the one before it or is skipped -- which is what uneven
        # looks like, rather than what it measures.
        late = sum(1 for g in gaps if g > nominal * 1.5)
        said = ("pacing: %d frames in %.1fs (%.1f/s, asked for %d), typical "
                "gap %.1fms, worst %.0fms, %d late by more than half a frame"
                % (len(gaps), total, len(gaps) / total if total else 0,
                   self.cfg.fps, gaps[len(gaps) // 2] * 1000,
                   gaps[-1] * 1000, late))
        if stamps:
            ragged = sum(1 for g in stamps if g > nominal * 1.5)
            said += ("; the timeline says typical %.1fms, worst %.0fms, "
                     "%d uneven (a frame should be %.1fms)"
                     % (stamps[len(stamps) // 2] * 1000, stamps[-1] * 1000,
                        ragged, nominal * 1000))
        # A rate that does not match the timestamps is not untidy, it is
        # wrong. Every frame is stamped one frame-interval after the last, so
        # producing 64 a second while claiming 60 describes 1.06 seconds of
        # video for every second that really passes -- and whoever is watching
        # it falls further behind for as long as it goes on, in hitches.
        # Nothing about the picture looks wrong on this end.
        rate = len(gaps) / total if total else 0
        asked = max(1, int(self.cfg.fps))
        if rate and abs(rate - asked) / asked > 0.02:
            self._off_rate = getattr(self, "_off_rate", 0) + 1
            if self._off_rate in (1, 10, 100):
                log.warning("the capture is producing %.1f frames a second "
                            "while every one of them is stamped as %d -- the "
                            "timeline runs %.0f%% %s than real time, which a "
                            "guest sees as a stutter. %s",
                            rate, asked, abs(rate - asked) / asked * 100,
                            "slow" if rate > asked else "fast",
                            "Turn 'Even out the frame rate' on."
                            if not getattr(self.cfg, "pace_frames", True)
                            else "Something is overriding the pacing.")
        log.info("%s%s", said, self._grab_report())

    def _forward(self, sink, kind):
        """Hand one encoded packet to every guest.

        This is where the guests stop sharing anything. Each one has its own
        pipeline, so a failure inside theirs -- a data channel giving up, a
        transport erroring -- is theirs alone. It used to be a `tee` inside one
        pipeline, and a GStreamer error belongs to the pipeline rather than the
        branch that raised it: one guest's SCTP association failing therefore
        stopped the capture and ended the session for everybody.
        """
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        self._note_gap(kind)
        buffer = sample.get_buffer()
        caps = sample.get_caps()
        if kind == "video":
            self.video_caps = caps
        else:
            self.audio_caps = caps
        if kind == "video":
            self._note_pace(buffer)
        for peer in list(self.peers.values()):
            try:
                peer.push(kind, buffer, caps)
            except Exception as exc:
                log.debug("peer %s would not take a %s buffer: %s",
                          peer.id, kind, exc)
        return Gst.FlowReturn.OK

    def show_pointer(self, yes):
        """Draw the mouse pointer into the picture, or stop. True if it took.

        Off by default and on only while somebody holds the keyboard and
        mouse. Two reasons it is not simply left on: a cursor parked over a
        game is noise nobody asked for, and -- the one that matters -- the
        pointer is not part of the screen's contents. X draws it from a
        separate cursor image, so it has to be composited in by hand, which is
        work per frame for something almost nobody is looking at.

        It is why the cursor could be seen in Kodi and nowhere else: Kodi
        draws its own pointer as part of its picture, so that one arrives in
        the capture whatever this says. Everything else on the machine relies
        on the X cursor, which was being left out.
        """
        if self.pipeline is None:
            return False
        element = self.pipeline.get_by_name("capture")
        if element is None:
            return False
        # What this source calls it, because they disagree: ximagesrc says
        # show-pointer and d3d11screencapturesrc says show-cursor. A source
        # that cannot draw the pointer at all says so rather than being asked.
        prop = getattr(self, "_pointer_property", "show-pointer")
        if not prop:
            # Not "cannot draw it" -- it is drawn, always. What it cannot do is
            # change its mind while running, so there is nothing to do here and
            # nothing has gone wrong.
            log.debug("%s draws the pointer always; nothing to switch",
                      getattr(self, "source_name", "this capture"))
            return False
        try:
            element.set_property(prop, bool(yes))
        except Exception:
            log.exception("could not change whether the pointer is captured")
            return False
        log.info("the mouse pointer is %s in the picture",
                 "showing" if yes else "hidden")
        return True

    def _peer_named(self, text):
        for peer_id, peer in self.peers.items():
            if f"peer_{peer_id}" in text or f"_{peer_id}" in text:
                return peer
        return None

    def _on_warning(self, _bus, message):
        err, debug = message.parse_warning()
        log.warning("pipeline warning: %s (%s)", err.message, debug)


class Peer:
    """One guest's webrtcbin: a send-only video track and an input channel.

    Or, for a guest who only brought a controller, the input channel alone.
    Two people on one sofa share one screen and need one picture between them,
    so the second controller's peer carries no media: it costs an ICE
    negotiation and a data channel, and not a second copy of the encode.
    """

    def __init__(self, stage, peer_id, on_signal, media=True):
        self.stage = stage
        self.id = peer_id
        self._on_signal = on_signal
        # Whether this one gets the picture. False for a guest who is sitting
        # next to somebody who already has it.
        self.media = media
        self.channel = None
        self.desk_channel = None
        # The picture as whole frames, for a guest that decodes it
        # itself. Off until one asks: see _on_picture_asked.
        self.frame_channel = None
        self.frames_wanted = False
        self.frames_arriving = 1.0
        self._said_shut = False
        self.frames_skipped = 0
        self._await_key = False
        self._outbox = collections.deque()
        self._pacing = False
        self._paced_at = 0.0
        self._allowance = 0.0
        self._sent_frames = 0
        self._reports = 0
        self._reported_seq = None
        self._desk_at = 0.0
        self._desk_gaps = []
        # What fraction of the frames sent to this guest reach it. Unknown
        # until it says so, and "all of them" is the honest starting guess:
        # nothing has gone wrong yet.
        self.frames_arriving = 1.0
        self.channel_behind = 0
        self.on_input = None          # set by the session; called with raw bytes
        self.on_desk = None           # ditto, for keyboard and mouse messages
        self.on_dead = None           # called when the media connection is over
        self.on_broken = None         # called when this peer's branch errors
        # Whether this peer currently has a usable path. A peer *object* is not
        # evidence of one: after a guest's network changes, the object survives
        # with every address in it dead, and ICE settles on "disconnected"
        # rather than "failed" -- so nothing declares it over and it sat there
        # holding a slot for the whole session.
        self.ice_ok = False
        self.ice = None               # held so webrtcbin's agent outlives it
        self._candidate_kinds = set()
        self._announced = set()
        self._route_logged = False
        self.webrtc = None
        self.pipeline = None          # this guest's own pipeline
        self._sources = {}            # kind -> appsrc
        self._caps = {}               # kind -> caps last set
        # What we actually handed to this peer. The difference between "the
        # server sent nothing" and "the network ate it" is the first question
        # to ask about a black picture, and without this it cannot be answered
        # from the host at all.
        self.sent = {"video_bytes": 0, "video_packets": 0,
                     "audio_bytes": 0, "audio_packets": 0}
        self._offered = False
        self._assembled = False
        # Every signal we connect, so all of them can be disconnected before
        # the elements are destroyed. A handler that fires during teardown
        # reaches into a GObject whose C half is already gone, and PyGObject
        # follows it straight into a segfault:
        #   g_object_get_qdata -> g_type_check_instance_is_fundamentally_a
        # This process died that way three times in twenty minutes.
        self._handlers = []

    # -- wiring -------------------------------------------------------------

    def attach(self):
        """Build this guest's own pipeline: appsrc in, webrtcbin out."""
        cfg = self.stage.cfg

        # A pipeline of their own. Everything that can go wrong for this guest
        # now goes wrong in here, where it reaches nobody else.
        self.pipeline = Gst.Pipeline.new(f"guest_{self.id}")
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        self._connect(bus, "message::error", self._on_own_error)

        self.ice = make_ice_agent(cfg)
        if self.ice is not None:
            self.webrtc = Gst.ElementFactory.make_with_properties(
                "webrtcbin", ["ice-agent"], [self.ice])
        else:
            self.webrtc = None
        if self.webrtc is None:
            self.webrtc = Gst.ElementFactory.make("webrtcbin", None)
        self.webrtc.set_property("name", f"peer_{self.id}")
        self.webrtc.set_property("bundle-policy", "max-bundle")
        self.webrtc.set_property("latency", cfg.jitter_ms)
        if cfg.stun_server:
            self.webrtc.set_property("stun-server", cfg.stun_server)
        if cfg.turn_server:
            self.webrtc.set_property("turn-server", cfg.turn_server)
        self.pipeline.add(self.webrtc)

        self._connect(self.webrtc, "on-negotiation-needed", self._on_negotiation_needed)
        self._connect(self.webrtc, "on-ice-candidate", self._on_ice_candidate)
        self._connect(self.webrtc, "notify::ice-connection-state", self._on_ice_state)
        # Anything this guest sends us arrives as a new pad. Today that is
        # only their microphone; the handler says so rather than assuming it,
        # because a pad that turns up for another reason must not be wired
        # into somebody's speakers.
        self._connect(self.webrtc, "pad-added", self._on_incoming)

        # Video first so the offer's m-lines come out in that order and
        # transceiver 0 is always the picture. A peer that carries no media
        # gets neither, and its offer is the data channel by itself -- which
        # is a legal offer and the whole point of it.
        if self.media:
            self._feed("video", None)
            if self.stage.has_audio:
                self._feed("audio", None)
            # A line for the guest's microphone, added whether or not they
            # ever switch it on.
            #
            # Added up front on purpose: a track attached to an m-line that is
            # already there costs nothing but a replaceTrack in the browser,
            # where adding the line later is a renegotiation -- a fresh offer
            # and answer mid-game, which this has quite enough of. So the line
            # exists, empty, and turning the microphone on fills it.
            #
            # Only where there is somewhere for it to go. An m-line offered by
            # a host that cannot play it anywhere is a promise nobody can keep.
            if micsink.where(self.stage.cfg):
                self._add_mic_line()
                # How long webrtcbin holds incoming media before it comes out.
                #
                # 200ms by default, and the comment further down this file
                # used to dismiss it -- "nothing comes in here" -- which was
                # true until a microphone did. It is the larger half of the
                # delay a guest hears as a slow conversation.
                #
                # Only set where something is actually received: on a peer
                # with no microphone line this property governs nothing, and
                # changing it would be noise in the log.
                try:
                    hold = max(10, int(getattr(self.stage.cfg,
                                               "guest_mic_latency_ms", 40)))
                    self.webrtc.set_property("latency", hold)
                    log.info("peer %s: holding incoming audio %dms "
                             "(webrtcbin's default is 200)", self.id, hold)
                except Exception:
                    log.debug("this webrtcbin has no latency property",
                              exc_info=True)

        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("this guest's pipeline would not start")

        index = 0
        while True:
            transceiver = self.webrtc.emit("get-transceiver", index)
            if transceiver is None:
                break
            # Every line but the microphone's is one-way outward. That one is
            # the only thing this host ever receives, and turning it round
            # here would silently undo it.
            if transceiver is not getattr(self, "mic_transceiver", None):
                self._one_way(transceiver, index)
            index += 1

        # Unreliable and unordered on purpose: pad state is a snapshot, so a
        # retransmitted frame is always worse than the one behind it.
        options = Gst.Structure.new_from_string(
            "options, ordered=(boolean)false, max-retransmits=(int)0")
        self.channel = self.webrtc.emit("create-data-channel", "input", options)
        if self.channel is None:
            raise RuntimeError("webrtcbin would not create the input channel")
        self._connect(self.channel, "on-message-data", self._on_channel_data)
        self._connect(self.channel, "on-open", self._on_input_open)
        self._connect(self.channel, "on-close",
                      lambda _c: log.info("peer %s: input closed", self.id))

        # Reliable and ordered, which is the opposite of the channel above and
        # for the opposite reason. A pad frame is a snapshot: losing one is
        # nothing, because the next one is the whole truth again. A keyboard
        # message is an event: a lost release leaves a key held down on
        # somebody's computer and there is no later message that corrects it.
        # See deskwire.py -- the cost is that a lost packet makes the pointer
        # late rather than wrong, which is the right way round for a desktop.
        desk_options = Gst.Structure.new_from_string(
            "options, ordered=(boolean)true")
        self.desk_channel = self.webrtc.emit("create-data-channel", "desk",
                                             desk_options)
        if self.desk_channel is None:
            raise RuntimeError("webrtcbin would not create the desk channel")
        self._connect(self.desk_channel, "on-message-string", self._on_desk_data)
        self._connect(self.desk_channel, "on-message-data", self._on_desk_data)

        # The picture again, as whole frames, for a guest that decodes it
        # itself. Created for every guest and used by the ones that ask -- an
        # empty channel costs a few bytes of SDP.
        #
        # Unordered, and never retransmitted.
        #
        # Three goes at this, and each one left the cost in place. Ordered and
        # reliable froze the picture for a second per lost packet. Unordered
        # changed nothing, which proved the stall was the sender waiting on
        # its own timeout rather than the receiver blocking. Pacing the pieces
        # onto the wire -- which every media stack does and this had never
        # done -- changed nothing either, so the loss is not a burst overruning
        # a queue. The one number never moved: 1049, 1062, 1069, 1085ms, while
        # the guest's page beat steadily at 18ms and nothing was ever lost.
        #
        # That number is SCTP's minimum retransmission timeout, and it is the
        # cost of asking for a retransmission at all. So stop asking. A video
        # frame that arrives a second late is not a picture, it is a freeze
        # with a picture at the end of it -- the frame was always worthless by
        # the time it came, and what the wait bought was nothing.
        #
        # This is what Moonlight and Sunshine do over UDP, and what the media
        # track beside this one does: send it once, and if a piece is missing,
        # give up on that frame and ask for a keyframe. A lost packet now
        # costs one frame and a keyframe -- a blink -- instead of a second of
        # held picture. The browser already does exactly this: every piece
        # says which frame it belongs to, so a frame that cannot be completed
        # is dropped and the run ends until a keyframe restarts it.
        #
        # Ordered, with a five-hundred-millisecond lifetime.
        #
        # This is moonlight-web's configuration exactly, arrived at from the
        # other direction, and their comments record the same fight. Ordered,
        # because frames reference their predecessor so delivery order *is*
        # decode order -- they tried unordered and it "turned every SCTP
        # retransmit into a false frameId gap and an IDR cycle", which is what
        # happened here too. A lifetime rather than a retransmit count,
        # because a count "kept retransmitting second-old frames in order
        # ahead of the keyframe once the link was back".
        #
        # They do not avoid the stall. They bound it: past the lifetime the
        # sender gives the message up, and the far end skips to what is
        # current. Five hundred is theirs and is the number that matters --
        # a hundred and fifty was tried here and the stall stayed at a full
        # second, which is the thing to check rather than assume.
        #
        # 2026-09-24: the obvious next idea does not work, so do not spend a
        # day on it. If the stall is SCTP's minimum retransmission timeout,
        # lower the floor -- usrsctp has
        # usrsctp_sysctl_set_sctp_rto_min_default() for exactly this. It
        # cannot be reached. usrsctp is linked *statically* into
        # libgstsctp-1.0.so.0 and exports none of its symbols:
        #
        #     nm -D --defined-only libgstsctp-1.0.so.0 | grep -c usrsctp   ->  0
        #
        # and sctpenc's own properties are three: remote-sctp-port,
        # sctp-association-id, use-sock-stream. There is no knob, and none
        # can be reached from this process. Changing it means rebuilding the
        # plugin, on every machine, for ever.
        #
        # And the freeze on this host is not only a lost packet. sctpenc says
        # "Could not write to resource", and the picture channel is reported
        # hundreds of kilobytes behind -- 671581 bytes in one case. That is
        # the send buffer full, which is a throughput limit rather than a
        # retransmission: 1440p60 at twenty-odd megabits is more than usrsctp
        # will pass, whatever the timeouts say. Which is why dropping the
        # bitrate helps, and why the honest fix is the media line beside this
        # one -- RTP over UDP, with the loss recovery a media stack already
        # has -- and WebCodecs used only to decode what arrives on it.
        video_options = Gst.Structure.new_from_string(
            "options, ordered=(boolean)true, max-packet-lifetime=(int)500")
        self.frame_channel = self.webrtc.emit("create-data-channel", "picture",
                                              video_options)
        if self.frame_channel is not None:
            # Read back, because a property that is silently not applied looks
            # exactly like one that is. The picture channel was configured
            # with a lifetime and stalled for a full second anyway, five
            # times over, which is what a lifetime is supposed to prevent --
            # and "the setting is there" was never checked against "the
            # setting took". Same habit as encoder_tuning.
            try:
                got = self.frame_channel.props.max_packet_lifetime
                log.info("peer %s: the picture channel gives up on a piece "
                         "after %sms (asked for 500)", self.id, got)
            except Exception:
                log.warning("peer %s: this webrtcbin will not say whether the "
                            "picture channel has a packet lifetime at all, so "
                            "a lost packet may cost a full retransmission "
                            "timeout", self.id, exc_info=True)
            self._connect(self.frame_channel, "on-open", self._on_picture_open)
            self._connect(self.frame_channel, "on-close",
                          lambda _c: setattr(self, "frames_wanted", False))
            self._connect(self.frame_channel, "on-message-string",
                          self._on_picture_asked)

        self._assembled = True
        self._negotiate()

    def _on_picture_open(self, _channel):
        log.info("peer %s: the picture channel is open", self.id)

    def _on_picture_asked(self, _channel, message):
        """A guest turning its own decoding on or off.

        Off by default and asked for explicitly, because sending whole frames
        to somebody who is not decoding them is the picture twice over -- once
        on the media track they are watching and once down here into nothing.
        """
        text = str(message or "").strip()
        # A guest whose frame had a hole in it. Everything after a dropped
        # frame decodes against something that never arrived, so the picture
        # stays wrong until a keyframe -- and with an infinite GOP there is no
        # next one unless somebody asks. Rate-limited on the Stage, which is
        # where a storm from four guests has to be answered.
        if text.lower() == "key":
            self.stage.request_keyframe(self.id)
            return
        if text.startswith("{"):
            self._take_picture_report(text)
            return
        want = text.lower() in ("1", "on", "yes", "true")
        was = getattr(self, "frames_wanted", False)
        self.frames_wanted = want
        # A fresh start knows nothing about the link, and the share left over
        # from the last one would otherwise hold the encoder down -- or, worse,
        # let it climb on a report that predates this decoder.
        self.frames_arriving = 1.0
        self._reported_seq = None
        log.info("peer %s: %s sending whole frames down the picture channel",
                 self.id, "started" if want else "stopped")
        if want:
            self.stage.apply_ceiling()
            log.info("peer %s: forcing a keyframe, because a decoder that has "
                     "just started has nothing to decode against", self.id)
            self.stage.force_keyframe()
        elif was:
            # The media line has been carrying nothing while this guest drew
            # its own picture, so the browser's decoder is starting from a gap
            # rather than from the last frame it saw.
            log.info("peer %s: forcing a keyframe, because the media line is "
                     "carrying the picture again", self.id)
            self.stage.force_keyframe()

    def _take_picture_report(self, text):
        """What the browser says it actually received.

        The third version of this, and the first that compares two numbers
        that mean the same thing. Both of the others were wrong in a way that
        did real damage, because what they feed is the encoder:

        The first compared "frames the browser saw in its last second" against
        "frames this end sent between its last two reports". Those are not the
        same second -- the report's own travel time moves the boundary -- so a
        window that straddled a few frames read as 69% on a LAN carrying
        everything, and the rate walked from 62 Mb/s to 1.5.

        The second compared running totals, on the reasoning that totals
        cancel whatever the windows do. They do -- if both sides count from
        the same beginning. The browser's worker is rebuilt whenever the
        painter restarts and its total goes back to zero, while this end keeps
        counting, so the first report after a restart claimed thousands of
        frames missing at once and slammed the encoder to the floor. The log
        read "the browser is 7836 frames behind what was sent, 7836 more than
        last time out of 60 sent (0% arriving)", which is not a link problem
        being reported, it is a counter being compared with a different
        counter.

        So every frame carries a number this end assigned, and the browser
        reports the last one it saw along with how many it received since its
        previous report. Both numbers are the host's, so there is no origin to
        agree about and nothing to reset: a restart shows up as a sequence
        this end recognises, and is re-anchored rather than believed.
        """
        try:
            report = json.loads(text)
        except Exception:
            log.debug("peer %s sent something unreadable up the picture "
                      "channel: %r", self.id, text[:120])
            return
        if "seq" not in report:
            return                      # a browser too old to number them
        seq = int(report.get("seq") or 0)
        got = int(report.get("got") or 0)
        last = self._reported_seq
        self._reported_seq = seq
        if last is None or seq < last:
            return                      # first report, or a restart: anchor only
        expected = seq - last
        # More than a couple of seconds of frames between two reports means
        # the reports themselves stopped, not that the picture did.
        if expected <= 0 or expected > 600:
            return
        self.frames_arriving = max(0.0, min(1.0, got / float(expected)))
        self._reports += 1
        if self._reports % 10 == 1 or self.frames_arriving < FRAMES_ARRIVING_LOW:
            log.info("peer %s: the browser received %d of the %d frames sent "
                     "since its last word (%.0f%%), painted %d, with %dms in "
                     "hand", self.id, got, expected,
                     self.frames_arriving * 100,
                     int(report.get("shown") or 0),
                     int(report.get("reserve") or 0))
        self.stage.note_arrivals()

    def send_frame(self, data, key, stamp):
        """One encoded frame to a guest that asked for them.

        Chunked, because SCTP will not carry an arbitrarily large message and
        a keyframe is easily larger than a browser's limit. Each piece says
        whether it starts a frame and whether it ends one, so the other end
        can put them back together without knowing anything else.
        """
        if not getattr(self, "frames_wanted", False):
            return
        channel = self.frame_channel
        if channel is None:
            return
        if channel.props.ready_state != GstWebRTC.WebRTCDataChannelState.OPEN:
            # Said once per peer rather than per frame: sixty a second would
            # bury everything else, and the interesting fact is that it is
            # happening at all.
            if not self._said_shut:
                self._said_shut = True
                log.info("peer %s: the picture channel is %s, so frames are "
                         "not being sent", self.id,
                         channel.props.ready_state.value_nick)
            return
        self._said_shut = False

        # Whether the channel is keeping up at all.
        #
        # The host produces a perfect stream -- 600 frames in 10 seconds,
        # every gap 16.7ms -- and the browser reported receiving 36 a second
        # of it. Frames were going missing between the two, and the only
        # thing between them is this channel: reliable and ordered, so
        # nothing is lost in flight, which leaves the send queue. A queue
        # that has grown to megabytes is one that is not being drained, and
        # everything added to it from then on is latency rather than picture.
        #
        # So when it is that far behind, frames that are not keyframes are
        # skipped deliberately and counted. Dropping the right frames on
        # purpose is always better than losing arbitrary ones by accident,
        # and a keyframe is never the right one to drop: everything after it
        # depends on it.
        try:
            waiting = int(channel.props.buffered_amount)
        except Exception:
            # Said once, because a signal that silently reads zero is worse
            # than no signal: it looks like a link that is keeping up.
            if not getattr(self, "_said_blind", False):
                self._said_blind = True
                log.warning("peer %s: this webrtcbin will not say how much is "
                            "waiting to be sent, so the browser's own count "
                            "is the only measure of the link", self.id,
                            exc_info=True)
            waiting = 0
        self.channel_behind = waiting

        # Skipping a frame means dropping to the next keyframe, not punching a
        # hole in the stream.
        #
        # This dropped individual non-key frames to relieve the queue, which
        # is what a display pipeline does -- it conceals the damage and
        # carries on. A WebCodecs decoder does not conceal anything: a frame
        # that references one it never received is a `Decoding error`, and
        # with an infinite GOP there is no next keyframe to recover on unless
        # somebody asks. So the skip killed the decoder, the page tore it down
        # and built it again, and that is ten seconds of black. The log has
        # the two events in the same second:
        #
        #   the picture channel is 293137 bytes behind, so frames are skipped
        #   the decoder stopped: Decoding error.
        #
        # Dropping to the next keyframe instead costs one short freeze and
        # leaves a decoder that is still working. The keyframe is asked for at
        # once, and the Stage's own rate limiter decides how often that may
        # really happen.
        if self._await_key:
            if not key:
                self.frames_skipped += 1
                return
            self._await_key = False
            log.info("peer %s: a keyframe arrived, so the picture resumes "
                     "(%d frames dropped waiting for it)",
                     self.id, self.frames_skipped)
        elif waiting > self.stage.frame_queue_limit() and not key:
            self._await_key = True
            self.frames_skipped += 1
            log.warning("peer %s: the picture channel is %d bytes behind, so "
                        "the picture drops to the next keyframe rather than "
                        "leaving the decoder a hole", self.id, waiting)
            self.stage.request_keyframe(self.id)
            return

        # Small enough to be paced, rather than as large as a browser will
        # take.
        #
        # Sixty thousand meant a whole frame was usually one message, handed
        # to SCTP in a single call and put on the wire as a burst of forty-odd
        # packets back to back, sixty times a second, with nothing spreading
        # them out. RTP does not do that -- every WebRTC media stack has a
        # pacer between the encoder and the socket precisely because a burst
        # overruns a queue somewhere and loses a packet. A data channel has no
        # pacer, so this is one: smaller pieces, released at the rate the
        # picture is actually being encoded at. See `_drain`.
        limit = 16000
        self._sent_frames += 1
        if self._sent_frames % 600 == 0:
            log.info("peer %s: the picture channel has sent %d frames and is "
                     "%d bytes behind, having skipped %d",
                     self.id, self._sent_frames, waiting, self.frames_skipped)
        total = len(data)
        # Numbered, and told how many there are.
        #
        # The channel abandons a piece it cannot deliver in time, so "the
        # pieces arrive in order" is still true but "they all arrive" is not.
        # Without a number, a lost middle piece is a head and a tail
        # concatenated into something that is not a frame and is fed to the
        # decoder as though it were. With one, the gap is plain and the frame
        # is dropped on purpose.
        pieces = max(1, (total + limit - 1) // limit)
        at = 0
        index = 0
        while at < total:
            end = min(at + limit, total)
            flags = (1 if key else 0)
            if at == 0:
                flags |= 2
            if end >= total:
                flags |= 4
            head = struct.pack("<BQHHI", flags, int(stamp), index, pieces,
                               self._sent_frames & 0xFFFFFFFF)
            self._outbox.append(head + data[at:end])
            at = end
            index += 1
        self._pace()

    # How often the pacer wakes, and how much headroom it allows over the rate
    # the encoder is producing at.
    #
    # Two milliseconds is about an eighth of a frame at sixty a second, which
    # is fine enough that a frame leaves as a handful of small bursts rather
    # than one large one, and coarse enough that a timer can keep it. The
    # headroom is what lets a queue that has fallen behind catch up without
    # the pacer itself becoming the bottleneck: a fifth over is enough to
    # absorb a keyframe without being enough to put the burst back.
    PACE_TICK_MS = 2
    PACE_HEADROOM = 1.2

    def _pace(self):
        """Start the pacer if it is not already running."""
        if self._pacing or not self._outbox:
            return
        self._pacing = True
        self._paced_at = time.monotonic()
        self._allowance = 0.0
        GLib.timeout_add(self.PACE_TICK_MS, self._drain)

    def _drain(self):
        """Put out as many pieces as the elapsed time has paid for.

        A leaky bucket, which is all a pacer is. The rate is what the encoder
        is being asked to produce, so in the steady state this hands SCTP a
        frame's worth of bytes over a frame's worth of time instead of all at
        once -- and a burst of forty packets back to back is what overruns a
        queue and loses one. Losing one costs a second: SCTP's minimum
        retransmission timeout, measured on the guest's own page as pieces
        stopping for 1073ms while its animation frames carried on at 18ms.
        """
        channel = self.frame_channel
        if channel is None or not self._outbox:
            self._pacing = False
            return False
        now = time.monotonic()
        rate = max(1, int(self.stage.rate_now())) * 1000 / 8.0   # bytes/second
        self._allowance += (now - self._paced_at) * rate * self.PACE_HEADROOM
        self._paced_at = now
        # A bucket that has been idle must not save up a burst to spend later,
        # which would be the very thing this exists to prevent.
        self._allowance = min(self._allowance, rate * 0.05)
        try:
            state = channel.props.ready_state
        except Exception:
            state = None
        if state is not None and state != GstWebRTC.WebRTCDataChannelState.OPEN:
            self._outbox.clear()
            self._pacing = False
            return False
        while self._outbox and self._allowance >= len(self._outbox[0]):
            piece = self._outbox.popleft()
            self._allowance -= len(piece)
            try:
                channel.emit("send-data", GLib.Bytes.new(piece))
            except Exception:
                self._outbox.clear()
                break
        if not self._outbox:
            self._pacing = False
            return False
        return True

    def on_answer(self, sdp_text):
        """Say what the guest agreed to for the microphone line, once.

        Worth logging because "the microphone is on" at one end and silence at
        the other has two quite different causes: a line the guest never
        activated, and a line that is active with nothing arriving. Only the
        answer can tell them apart, and it was invisible.
        """
        try:
            lines = [l for l in sdp_text.splitlines() if l.startswith("m=")]
            index = mic_line_index(sdp_text)
            if index is None or index >= len(lines):
                return
            # The direction attribute in that section.
            block, seen, direction = [], -1, "(none stated)"
            for line in sdp_text.splitlines():
                if line.startswith("m="):
                    seen += 1
                elif seen == index and line.startswith("a=") and \
                        line[2:].strip() in ("sendonly", "recvonly",
                                             "sendrecv", "inactive"):
                    direction = line[2:].strip()
            log.info("peer %s: the guest answered the microphone line %s",
                     self.id, direction)
        except Exception:
            log.debug("could not read the microphone line's direction",
                      exc_info=True)

    def _on_incoming(self, _webrtc, pad):
        """A guest is sending something. Play it, if it is their microphone."""
        if pad.get_direction() != Gst.PadDirection.SRC:
            return
        caps = pad.get_current_caps() or pad.query_caps(None)
        text = caps.to_string() if caps else ""
        if "media=(string)audio" not in text and "media=audio" not in text:
            log.info("peer %s sent something that is not audio; ignoring it",
                     self.id)
            return
        device = micsink.where(self.stage.cfg)
        if not device:
            log.warning("peer %s is sending a microphone and this host has "
                        "nowhere to play it; set the microphone device",
                        self.id)
            return
        try:
            self._play_mic(pad, device)
        except Exception:
            log.exception("peer %s: could not play their microphone", self.id)

    def _play_mic(self, pad, device):
        """Build the decode-and-play chain and hang this pad off it."""
        names = {name: ident for name, ident in micsink.sinks(Gst) if ident}
        tail = micsink.describe(device, names,
                                getattr(self.stage.cfg,
                                        "guest_mic_latency_ms", 40))
        chain = Gst.parse_bin_from_description(
            "rtpopusdepay ! opusdec plc=true ! " + tail, True)
        if chain is None:
            raise RuntimeError("could not build the microphone chain")
        chain.set_name("mic_%s" % self.id)
        self.pipeline.add(chain)
        chain.sync_state_with_parent()
        sink_pad = chain.get_static_pad("sink")
        if sink_pad is None or pad.link(sink_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError("could not link the microphone")
        self._mic_chain = chain
        log.info("peer %s: their microphone is playing into %s",
                 self.id, device)

    def _add_mic_line(self):
        """Offer a one-way audio line the guest may speak into."""
        try:
            caps = Gst.Caps.from_string(
                "application/x-rtp,media=audio,encoding-name=OPUS,"
                "payload=111,clock-rate=48000,encoding-params=(string)2")
            self.mic_transceiver = self.webrtc.emit(
                "add-transceiver",
                GstWebRTC.WebRTCRTPTransceiverDirection.RECVONLY, caps)
            if self.mic_transceiver is None:
                log.info("this webrtcbin would not add a microphone line")
        except Exception as exc:
            # Older webrtcbins have no add-transceiver. Losing the microphone
            # must not cost the session.
            log.info("no microphone line for this guest (%s)", exc)
            self.mic_transceiver = None

    def _one_way(self, transceiver, index):
        """This guest receives and never sends, and may ask for a packet again.

        do-nack is what makes webrtcbin offer an rtx payload type and hold
        what it sent long enough to send it a second time. The caps asking for
        nack are the other half, and neither half is any use alone: without
        the caps the browser is never told it may ask, and without this it
        asks for packets nothing here can still produce.
        """
        transceiver.set_property(
            "direction", GstWebRTC.WebRTCRTPTransceiverDirection.SENDONLY)
        if index != VIDEO_TRANSCEIVER:
            # Opus carries its own error correction in the following packet,
            # so a retransmitted one would arrive after the gap it was for.
            return
        try:
            transceiver.set_property("do-nack", True)
        except Exception:
            log.debug("this webrtcbin has no do-nack, so a lost packet still "
                      "costs a keyframe")

    def _rtp_caps(self, kind):
        """The caps this guest's appsrc announces, stated rather than waited for.

        webrtcbin builds the offer from whatever the source claims the moment
        it is asked, and a sample from the capture may not have arrived yet --
        so an appsrc left to learn its caps from the first buffer produced an
        offer with no video in it at all. The guest negotiated audio, took
        input, and never got a picture.

        These are known: this program built the pipeline that produces them.
        """
        if kind == "audio":
            # encoding-params is the channel count, and leaving it out is not
            # cosmetic: it becomes "a=rtpmap:97 OPUS/48000" in the offer, where
            # every browser expects "opus/48000/2". Stating the caps by hand
            # dropped it, and the sound went with it -- the GStreamer test
            # guest accepts either, so nothing caught it.
            #
            # Everything after it becomes the a=fmtp line, and until it was
            # written there was no a=fmtp line at all. That is not a missing
            # nicety: RFC 7587 says an Opus stream with nothing said about it
            # is *mono*, and both browsers believe it. The host has been
            # encoding 48 kHz stereo and every guest has been folding it down
            # to one channel on arrival -- a game's music mixed into the
            # middle of the head, which is exactly what "the sound is poor
            # while the picture is fine" sounds like.
            #
            #   sprop-stereo    what is being sent. The one that matters.
            #   stereo          what would be accepted back. Nothing is sent
            #                   this way, so it is a declaration of manners.
            #   useinbandfec    the encoder has it on; without this the
            #                   decoder is not told it may use it.
            #   minptime        matches frame-size on the encoder.
            #   maxaveragebitrate  what the encoder is actually doing, said in
            #                   the place a receiver looks for it.
            cfg = self.stage.cfg
            return Gst.Caps.from_string(
                "application/x-rtp,media=(string)audio,encoding-name=(string)OPUS,"
                "payload=(int)97,clock-rate=(int)48000,encoding-params=(string)2,"
                "sprop-stereo=(string)1,stereo=(string)1,"
                "useinbandfec=(string)1,"
                f"minptime=(string){cfg.audio_frame_ms},"
                f"maxaveragebitrate=(string){cfg.audio_bitrate_kbps * 1000}")
        encoding = self.stage.encoding
        # rtcp-fb-nack is what puts "a=rtcp-fb:96 nack" in the offer, and it is
        # the difference between a lost packet costing a frame and costing a
        # picture. Without it a guest's only recourse is "send me a keyframe",
        # which webrtcbin advertises by itself (nack pli, ccm fir) -- so one
        # dropped packet froze the picture until a whole new keyframe had been
        # encoded and had arrived, up to two seconds away. With it the browser
        # asks for the packet it missed and gets it back in a round trip.
        return Gst.Caps.from_string(
            f"application/x-rtp,media=(string)video,encoding-name=(string){encoding},"
            f"payload=(int)96,clock-rate=(int)90000,"
            f"rtcp-fb-nack=(boolean)true")

    def _feed(self, kind, caps):
        """One appsrc carrying the encoded stream into this guest's webrtcbin."""
        caps = caps or self._rtp_caps(kind)
        src = Gst.ElementFactory.make("appsrc", f"{kind}src_{self.id}")
        src.set_property("is-live", True)
        src.set_property("format", Gst.Format.TIME)
        src.set_property("emit-signals", False)
        # Never block the capture thread, and never grow without limit: a guest
        # whose connection has stalled drops packets instead of holding the
        # encoder up or eating memory.
        src.set_property("block", False)
        # The buffers come from another pipeline with its own clock and base
        # time, so their timestamps mean nothing here. Let the source stamp
        # them on arrival instead of handing webrtcbin times it cannot place.
        src.set_property("do-timestamp", True)
        # Two limits, and the one that matters is the time. `queue_ms` is
        # documented as how much encoded video may pile up for a guest before
        # frames are dropped, and until now nothing read it: the only limit
        # was two megabytes, which at this bitrate is seconds of video and so
        # seconds of delay for a guest whose link went quiet for a moment.
        #
        # Time rather than bytes because the stream is bursty by nature: every
        # packet of a frame is pushed at once, and a keyframe is several times
        # the size of the frames around it. A byte limit worth 60 ms of the
        # average bitrate would be overrun by every keyframe; a time limit is
        # not, because the whole burst arrives inside a millisecond of itself.
        src.set_property("max-bytes", 2 * 1024 * 1024)
        try:
            src.set_property("max-time", self.stage.cfg.queue_ms * Gst.MSECOND)
        except Exception:
            pass                                # older GStreamer: the bytes cap stands
        try:
            src.set_property("leaky-type", 2)      # drop the oldest
        except Exception:
            pass                                    # older GStreamer: fine
        src.set_property("caps", caps)
        self._caps[kind] = caps
        if kind == "video":
            # A browser that has lost a frame asks for a new keyframe, and
            # webrtcbin turns that request into an upstream force-key-unit
            # event. It arrives here and stops: the encoder is in the capture
            # pipeline, not this one, so nothing was listening and the guest
            # waited for the next periodic keyframe -- two seconds at thirty
            # frames a second. That is the black screen after a blip.
            pad = src.get_static_pad("src")
            if pad is not None:
                pad.add_probe(Gst.PadProbeType.EVENT_UPSTREAM,
                              self._on_upstream)
        self.pipeline.add(src)
        src.link_pads("src", self.webrtc, "sink_%u")
        self._sources[kind] = src

    def _on_upstream(self, _pad, info):
        """Pass a guest's request for a keyframe across to the encoder."""
        event = info.get_event()
        if event is not None and event.type == Gst.EventType.CUSTOM_UPSTREAM:
            structure = event.get_structure()
            if structure is not None and structure.has_name("GstForceKeyUnit"):
                self.stage.request_keyframe(self.id)
        return Gst.PadProbeReturn.OK

    def push(self, kind, buffer, caps):
        """Take one encoded packet from the capture."""
        src = self._sources.get(kind)
        if src is None or self.webrtc is None:
            return
        # Not the picture twice.
        #
        # A guest drawing its own picture is being sent every frame down the
        # data channel, and was being sent the same picture again as RTP on
        # the media line it is no longer watching. Two full copies of the
        # stream to one guest: at the top of the quality setting that is
        # 62500 kb/s each way, 125 Mb/s to one browser, and the browser
        # decoding both -- once into an element holding nothing but the sound,
        # once in the worker. It is the reason the data channel had no room to
        # work in, and it cost twice the bandwidth to produce a worse picture.
        #
        # The line stays open and carries nothing, so switching back is the
        # same instant switch it always was -- with a keyframe, because a
        # receiver that has had a gap has nothing to decode against.
        if kind == "video" and getattr(self, "frames_wanted", False):
            return
        # The caps were stated when the source was made and are not changed
        # here: renegotiating mid-stream on a cosmetic difference would
        # interrupt a picture that is working.
        #
        # A shallow copy per guest, with the capture's timestamps cleared: the
        # same buffer goes to several pipelines, so it must not be written to,
        # and the times on it belong to a clock this pipeline has never seen.
        outgoing = buffer.copy()
        outgoing.pts = Gst.CLOCK_TIME_NONE
        outgoing.dts = Gst.CLOCK_TIME_NONE
        outgoing.duration = Gst.CLOCK_TIME_NONE
        src.emit("push-buffer", outgoing)
        self.sent[f"{kind}_bytes"] += buffer.get_size()
        self.sent[f"{kind}_packets"] += 1

    def _on_own_error(self, _bus, message):
        """An error inside this guest's pipeline, and nobody else's."""
        err, debug = message.parse_error()
        log.warning("peer %s: %s (%s)", self.id, err.message, debug)
        # An association that has errored carries nothing ever again, and the
        # media line has been carrying nothing on purpose while it worked. If
        # both are silent the guest has a black screen and no way to say so,
        # which is exactly what was reported. So the picture goes back on the
        # line that still exists, at once, without waiting for the browser to
        # notice and ask.
        if self.frames_wanted and "sctp" in str(debug or "").lower():
            self.frames_wanted = False
            log.warning("peer %s: the picture channel's association has "
                        "failed, so the picture goes back on the media line",
                        self.id)
            self.stage.force_keyframe()
        if self.on_broken is not None:
            self.stage.loop.call_soon_threadsafe(self.on_broken, err.message)

    def detach(self):
        """Take this guest's pipeline out of service and see it to NULL.

        The waiting is done on a thread of this pipeline's own, for two
        reasons that pull the same way.

        NULL is asynchronous on a pipeline holding live ICE and DTLS
        transports: set_state returns ASYNC and the rest happens later on
        GStreamer's own threads. Nothing here used to wait for it, so Python
        dropped its last reference while the state change was still in flight
        -- and a pipeline mid-transition is kept alive by those threads, which
        then never end. The process was left holding the whole thing. Measured
        on the console after a day: four `peer_slot0` pipelines still running
        with their appsrcs, nice agents and RTP sessions, for guests who had
        long since left, and 1.8 GB of anonymous memory with nobody connected
        at all -- against 87 MB for a capture with no guests.

        What made that a broken host rather than a big one: past MemoryHigh the
        kernel throttles the cgroup instead of killing it, so the service never
        fell over. It went slow -- memory.pressure full at 77%, three million
        throttle events -- until an attach could no longer finish inside
        PIPELINE_TIMEOUT, and everybody who typed the PIN after that was told
        the host did not answer.

        And the wait cannot happen where detach is called from: that is
        `stage.mutations`, the single worker that also serves every add_peer.
        A teardown measured at 7.8s would hold the next guest's attach up for
        7.8s, which is the starvation reset_worker exists to paper over. So the
        reference goes to a thread whose only job is to hold it until NULL
        lands, and the worker goes straight back to its queue.
        """
        if self.pipeline is None:
            return
        self._disconnect_all()
        self.on_dead = self.on_broken = self.on_input = self.on_desk = None
        pipeline, self.pipeline = self.pipeline, None
        # Handed to the thread rather than dropped here, and that is not
        # tidiness -- see the note on `held` below.
        # Everything this peer owns that GStreamer or GLib is still holding,
        # taken off it now and released in a deliberate order once the
        # pipeline has stopped. The order is in see_it_to_null, and it is the
        # fix rather than a tidiness.
        extras = [self.webrtc, self.channel, self.desk_channel,
                  self._sources, self._caps]
        # The ICE agent is handed to the parking lot rather than released.
        # See _PARKED_AGENTS for why it must never be unreffed from here.
        if self.ice is not None:
            _PARKED_AGENTS.append(self.ice)
            self.ice = None
        self.webrtc = self.channel = self.desk_channel = None
        self.frame_channel = None
        self.frames_wanted = False
        self._sources = {}
        self._caps = {}
        who = self.id

        def see_it_to_null():
            nonlocal pipeline
            # `held` keeps this guest's webrtcbin, its data channels and its
            # appsrcs alive for exactly as long as the pipeline they belong
            # to, and it is here because letting go of them early crashed the
            # process.
            #
            # Four core dumps on the console say the same thing, the oldest
            # from 2026-09-10: SIGSEGV in
            # g_type_check_instance_is_fundamentally_a, reached through
            # g_object_unref from libgstwebrtc's dispose under gst_bin_remove,
            # and on top of the stack PyObject_SetAttr on a worker thread.
            # That attribute assignment is the `self.webrtc = ... = None`
            # above: Python letting go of a wrapper while the pipeline
            # underneath it was still mid-teardown, and the unref landing on
            # an object webrtcbin had already finished with.
            #
            # It is a use-after-free, and the reason it matters far beyond one
            # crash is what a use-after-free does to glibc's heap. Corrupt the
            # free lists and malloc stops being able to reuse anything: the
            # arena grows without bound, `malloc_trim` reclaims nothing, and
            # no object tracker sees a thing because at the object level
            # nothing is leaking -- it is being freed, twice. Every symptom
            # this project has been chasing has that shape. See the leak entry
            # in the README.
            #
            # So nothing this pipeline owns is released until the pipeline has
            # actually reached NULL, and then all of it goes together.
            started = time.monotonic()
            try:
                pipeline.set_state(Gst.State.NULL)
                # This frame holds the last reference until the state change
                # has actually finished, which is the entire point of the
                # thread. Do not be tempted to drop it and return early.
                _, state, _ = pipeline.get_state(TEARDOWN_TIMEOUT)
            except Exception as exc:
                log.warning("peer %s did not stop cleanly: %s", who, exc)
                return
            finally:
                # Released here, in this order, and the order is the whole fix.
                #
                # faulthandler caught the fault at threading.py:998 -- `del
                # self._target` -- with no frame of ours on the stack. That is
                # the thread dropping this closure, and with it every object
                # the closure captured, in whatever order Python happened to
                # choose. Three access violations in gobject-2.0-0.dll, all at
                # the same offset, were that release.
                #
                # So it is done here instead, deliberately:
                #
                #   1. the wrappers -- webrtcbin, the channels, the appsrcs.
                #      The pipeline holds references of its own to these, so
                #      letting ours go changes nothing yet.
                #   2. the pipeline, whose last reference this is. Dropping it
                #      disposes the bin, and that is when webrtcbin tears
                #      itself down and reaches for its ICE agent.
                #
                # The ICE agent is not in either step. It is never released
                # from here at all -- see _PARKED_AGENTS.
                extras.clear()
                pipeline = None
            took = time.monotonic() - started
            if state != Gst.State.NULL:
                # The leak, said out loud. It is the one thing that used to
                # happen in complete silence, and a day of it is a host that
                # stops answering the PIN.
                log.warning("peer %s did not reach NULL in %.1fs (stuck at "
                            "%s); its threads and memory are still held",
                            who, took, state)
            elif took > 1.0:
                log.warning("peer %s took %.1fs to stop", who, took)

        threading.Thread(target=see_it_to_null, name=f"teardown-{who}",
                         daemon=True).start()

    def _connect(self, obj, signal, handler):
        self._handlers.append((obj, obj.connect(signal, handler)))

    def _disconnect_all(self):
        """Take our callbacks off before anything is destroyed.

        Order matters more than it looks: tearing down a webrtcbin makes it
        emit -- ICE state changes, a data channel closing -- and by then the
        Python side is halfway through dismantling the very objects those
        handlers reach for.
        """
        for obj, handler_id in self._handlers:
            try:
                obj.disconnect(handler_id)
            except Exception:
                pass                    # already gone is the outcome we wanted
        self._handlers = []

    def _on_negotiation_needed(self, _element):
        if self.webrtc is None:
            return
        self._negotiate()

    def _negotiate(self):
        """Make exactly one offer, and only once there is something to offer.

        Guarded twice over. `_assembled` keeps an early request from describing
        a peer with no track and no channel; `_offered` keeps the later ones --
        the video track and the data channel each ask -- from offering again
        while the first is still in flight.
        """
        if self._offered or not self._assembled or self.webrtc is None:
            return
        self._offered = True
        promise = Gst.Promise.new_with_change_func(
            self._on_offer_created, self.webrtc, None)
        self.webrtc.emit("create-offer", None, promise)

    def _on_offer_created(self, promise, element, _data):
        if self.webrtc is None:
            return
        # wait() before get_reply(). The change callback can run before the
        # promise has actually settled, and the reply then carries a NULL
        # description -- which surfaces much later as an AttributeError on
        # `.sdp`, nowhere near the cause.
        promise.wait()
        reply = promise.get_reply()
        if reply is None:
            log.error("peer %s: create-offer returned nothing", self.id)
            return
        offer = reply.get_value("offer")
        if offer is None or offer.sdp is None:
            log.error("peer %s: create-offer produced an empty description", self.id)
            return
        element.emit("set-local-description", offer, Gst.Promise.new())
        text = with_fmtp(offer.sdp.as_text(), self.stage._fmtp,
                         self.stage.encoding)
        log.info("peer %s: offering %s", self.id, describe_sdp(text))
        # The guest is told how much video to hold before it starts playing.
        # It is the only end that can do anything about arrival that is
        # uneven for the picture: webrtcbin's own `latency` buffers media
        # coming *in*, which for a long time was nothing at all.
        #
        # It is no longer nothing -- a guest's microphone comes in -- and that
        # property is set where the microphone line is added, because 200ms of
        # it was most of why a voice arrived late.
        # Which m-line the guest may speak on, named rather than guessed.
        #
        # The page used to look for the transceiver whose direction was
        # "sendonly". That cannot work: with no track attached yet, a browser
        # answers an offered recvonly line as *inactive*, so the line could
        # not be found, so no track could be attached, so it stayed inactive.
        # A deadlock, and the microphone button never appeared.
        #
        # The index is what the host knows for certain -- it added the
        # transceiver -- so it says so and the page uses it directly.
        self._emit("offer", {"sdp": text, "type": "offer",
                             "jitter": self.stage.cfg.jitter_ms,
                             "mic_line": self._mic_line_index(text)})

    def _mic_line_index(self, sdp):
        """Which m-line is the microphone's, or None if there is not one.

        Counted from the offer rather than asked of webrtcbin, because the
        m-line order is what the page indexes its transceivers by, and that
        order is a property of the text that was sent.

        Ours is the second audio line: the first is the game's sound going
        out, and this one is the only line offered the other way round.
        """
        if getattr(self, "mic_transceiver", None) is None:
            return None
        return mic_line_index(sdp)

    def _on_ice_candidate(self, _element, mline_index, candidate):
        # Candidate types decide whether anybody outside can reach us at all:
        # `host` is a LAN address, `srflx` is what STUN discovered our public
        # address to be, `relay` came from a TURN server. A guest on the
        # internet with only host candidates offered will connect, negotiate,
        # and show a black screen forever.
        parts = candidate.split()
        kind = ""
        if "typ" in parts:
            index = parts.index("typ")
            if index + 1 < len(parts):
                kind = parts[index + 1]
        if kind and kind not in self._candidate_kinds:
            self._candidate_kinds.add(kind)
            log.info("peer %s: gathered a %s candidate", self.id, kind)
        self._emit("ice", {"candidate": candidate, "sdpMLineIndex": mline_index})

        extra = self._forwarded_candidate(parts, kind)
        if extra:
            log.info("peer %s: also offering %s:%s at the public address",
                     self.id, parts[4] if len(parts) > 4 else "?",
                     parts[5] if len(parts) > 5 else "?")
            self._emit("ice", {"candidate": extra, "sdpMLineIndex": mline_index})

    def _forwarded_candidate(self, parts, kind):
        """The same socket, announced at the public address and *same port*.

        Without this, port forwarding cannot work behind a symmetric NAT --
        which is most home routers. STUN reports the external port the router
        happened to allocate for talking to the STUN server, and that mapping
        is per-destination: no guest can use it. The forward, meanwhile, sends
        WAN 40005 straight to this machine's 40005 and works for everybody --
        and ICE never discovers it, because nothing on this machine can observe
        a static rule in the router.

        So we say it ourselves: for each LAN candidate, an identical one at the
        public address. It is exactly what a 1:1 NAT address is for in every
        other WebRTC server. A guest that cannot reach it simply loses that
        candidate; the LAN ones still work for people in the house.
        """
        public = self.stage.public_ip
        if not public or kind != "host" or len(parts) < 6:
            return None
        address, port = parts[4], parts[5]
        if not net.is_private(address) or ":" in address:
            return None
        # Port 9 is the discard port: ICE-TCP candidates use it as a
        # placeholder for a socket that does not accept datagrams. Shadowing
        # one produces a candidate pointing at nothing.
        if port == "9" or (len(parts) > 2 and parts[2].upper() != "UDP"):
            return None
        if (address, port) in self._announced:
            return None
        self._announced.add((address, port))

        fields = list(parts)
        fields[4] = public
        # Foundation and priority must differ from the candidate it shadows, or
        # a browser treats it as a duplicate and ignores it.
        if fields[0].startswith("candidate:"):
            fields[0] = "candidate:" + fields[0].split(":", 1)[1] + "9"
        # And the priority must be a real srflx priority, not a host one.
        # This used to subtract 100, which left it ranked level with the LAN
        # candidate it shadows -- so a guest in the house would nominate the
        # public address, and if anything on the way (a forward, hairpin NAT)
        # was not working they got a connected session with no picture, while
        # the LAN path that would have worked sat unused. ICE priority is
        # (2^24 * type preference) + ..., with host 126 and srflx 100, so a
        # genuine srflx sits exactly this far below its own base. Announcing
        # it at its true rank makes it what it was always described as: the
        # fallback for people who cannot reach the LAN address.
        try:
            fields[3] = str(max(1, int(fields[3]) - (126 - 100) * (1 << 24)))
        except ValueError:
            pass
        # srflx, with the local socket recorded as its base, which is what a
        # reflexive candidate means and what browsers expect to parse.
        typ = fields.index("typ")
        fields[typ + 1] = "srflx"
        fields = fields[:typ + 2] + ["raddr", address, "rport", port]
        return " ".join(fields)

    def _on_input_open(self, _channel):
        """The pad channel is up, which means the path is.

        `ice_ok` is set here as well as from ice-connection-state, because for
        a peer with no media that state never arrives. webrtcbin derives it
        from its RTP transceivers, and an input-only guest -- somebody sitting
        next to the person who has the picture -- has none, so it stays at
        `new` and notifies nobody. The seat was then judged absent by
        GuestConnection.has_media and swept twenty-five seconds after it was
        made, however busily its controller was being used: a second player
        who joined, worked, and vanished just as a game got going.

        An open SCTP channel is a stronger statement than any ICE state
        anyway. Nothing opens one without a path.
        """
        self.ice_ok = True
        log.info("peer %s: input open", self.id)

    def _on_ice_state(self, element, _param):
        if self.webrtc is None:
            return                      # torn down while this was in flight
        state = element.get_property("ice-connection-state")
        self.ice_ok = state.value_nick in ("connected", "completed")
        log.info("peer %s: ice %s", self.id, state.value_nick)
        self._emit("ice-state", {"state": state.value_nick})
        # "disconnected" is recoverable and often just a moment of packet loss,
        # so it is deliberately not in here. "failed" and "closed" are not.
        if state.value_nick in ("connected", "completed"):
            self._log_route()
        if state.value_nick in ("failed", "closed") and self.on_dead:
            self.stage.loop.call_soon_threadsafe(
                self.on_dead, f"media connection {state.value_nick}")

    def _log_route(self):
        """Which pair of addresses the media is actually using.

        The one fact worth having when a guest reports a black screen: whether
        the stream is going over the LAN, over the public address, or through a
        relay. Everything else -- ports forwarded, candidates gathered -- is a
        guess about this.
        """
        if self._route_logged or self.webrtc is None:
            return
        self._route_logged = True

        def describe(pool, ident):
            """One candidate as "address:port (type)", or something honest.

            This function did not exist. It was called twice below, raised
            NameError every time a pair was nominated, and the NameError was
            caught by the `except Exception` around it and logged at debug --
            so the one line that says whether media is going over the LAN, the
            public address or a relay has never appeared. The comment above
            about this "silently producing nothing at all" was more right than
            it knew.
            """
            found = pool.get(ident)
            if found is None:
                return "unknown"
            address = found.get_string("address") or "?"
            kind = found.get_string("candidate-type") or ""
            ok, port = found.get_int("port")
            where = "%s:%d" % (address, port) if ok else address
            return "%s (%s)" % (where, kind) if kind else where

        def report(promise, _a, _b):
            try:
                promise.wait()
                stats = promise.get_reply()
                if stats is None:
                    return
                pairs, locals_, remotes = {}, {}, {}
                # Walk the fields by index. GstStructure.foreach's callback
                # signature is awkward from Python and a mismatch there is
                # swallowed as "could not read the route", which is how this
                # silently produced nothing at all the first time.
                for i in range(stats.n_fields()):
                    name = stats.nth_field_name(i)
                    value = stats.get_value(name)
                    if not isinstance(value, Gst.Structure):
                        continue
                    kind = value.get_string("type") or ""
                    if kind == "candidate-pair":
                        pairs[name] = value
                    elif kind == "local-candidate":
                        locals_[value.get_string("id") or name] = value
                    elif kind == "remote-candidate":
                        remotes[value.get_string("id") or name] = value

                for pair in pairs.values():
                    ok, nominated = pair.get_boolean("nominated")
                    if not (ok and nominated):
                        continue
                    log.info("peer %s: media route %s <- %s", self.id,
                             describe(locals_, pair.get_string("local-candidate-id") or ""),
                             describe(remotes, pair.get_string("remote-candidate-id") or ""))
            except Exception as exc:
                log.debug("could not read the media route: %s", exc)

        try:
            self.webrtc.emit("get-stats", None,
                             Gst.Promise.new_with_change_func(report, None, None))
        except Exception as exc:
            log.debug("get-stats unavailable: %s", exc)

    def on_pipeline_thread(self, fn, *args):
        """Run something that touches webrtcbin on the one thread that may.

        GStreamer is thread-safe in principle and the AMD VAAPI driver under it
        is not, in practice: this process took two SIGSEGVs inside
        radeonsi_drv_video.so with PyGObject on the stack, both while a peer
        was being negotiated from the asyncio thread as the encoder ran. Every
        add and remove already goes through one worker; the answer and the
        candidates now go the same way, so nothing reaches webrtcbin from two
        threads at once.
        """
        return self.stage.worker.submit(fn, *args)

    def set_remote_answer(self, sdp_text):
        self.on_pipeline_thread(self._set_remote_answer, sdp_text)

    def _set_remote_answer(self, sdp_text):
        if self.webrtc is None:
            return
        ok, message = GstSdp.SDPMessage.new()
        if ok != GstSdp.SDPResult.OK:
            raise RuntimeError("could not allocate an SDP message")
        GstSdp.sdp_message_parse_buffer(sdp_text.encode(), message)
        log.info("peer %s: answered with %s", self.id, describe_sdp(sdp_text))
        self.on_answer(sdp_text)
        answer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.ANSWER, message)
        self.webrtc.emit("set-remote-description", answer, Gst.Promise.new())

    def add_ice_candidate(self, mline_index, candidate):
        self.on_pipeline_thread(self._add_ice_candidate, mline_index, candidate)

    def _add_ice_candidate(self, mline_index, candidate):
        if self.webrtc is not None:
            self.webrtc.emit("add-ice-candidate", mline_index, candidate)

    # -- input --------------------------------------------------------------

    def _on_channel_data(self, _channel, glib_bytes):
        if self.on_input is None or self.webrtc is None:
            return
        data = glib_bytes.get_data() if hasattr(glib_bytes, "get_data") else bytes(glib_bytes)
        self.stage.loop.call_soon_threadsafe(self.on_input, data)

    def _on_desk_data(self, _channel, payload):
        if self.on_desk is None or self.webrtc is None:
            return
        # How evenly the pointer's movements are arriving.
        #
        # "Cursor control feels a little jittery" has two candidates -- the
        # picture it is being watched through, and the path the movements
        # travel -- and they want opposite fixes. A hand moving smoothly
        # produces movements at the mouse's own report rate, so a spacing that
        # is even here means the input path is clean and the unevenness is in
        # the picture; one that arrives in bursts means it is not. Nothing has
        # ever measured this, and guessing between the two from how it feels
        # is what the last several rounds of the picture were.
        now = time.monotonic()
        if self._desk_at:
            gap = (now - self._desk_at) * 1000.0
            if gap < 500:                 # a pause is not a burst
                self._desk_gaps.append(gap)
        self._desk_at = now
        if len(self._desk_gaps) >= 600:
            ordered = sorted(self._desk_gaps)
            middle = ordered[len(ordered) // 2]
            worst = ordered[-1]
            rough = sum(1 for g in self._desk_gaps
                        if abs(g - middle) > middle * 0.5)
            # A batch, not a movement: one message carries every movement
            # the mouse made since the last, so this still reads 16.6ms and
            # correctly so. The number that mattered was the first one --
            # 16.6 where the mouse reports at 8, which said the browser was
            # summing them -- and it is named as batches now so nobody reads
            # it as movements later and concludes the wrong thing.
            log.info("peer %s: a batch of pointer movement arrives every "
                     "%.1fms typical, worst %.0fms, %d of %d more than half "
                     "a step off",
                     self.id, middle, worst, rough, len(self._desk_gaps))
            self._desk_gaps = []
        if hasattr(payload, "get_data"):
            payload = payload.get_data()
        elif not isinstance(payload, (str, bytes, bytearray)):
            payload = bytes(payload)
        self.stage.loop.call_soon_threadsafe(self.on_desk, payload)

    def _emit(self, kind, payload):
        # Read the callback at call time: the session may have re-pointed it at
        # a new socket since this peer was created.
        self.stage.loop.call_soon_threadsafe(
            lambda: self._on_signal(kind, payload) if self._on_signal else None)
