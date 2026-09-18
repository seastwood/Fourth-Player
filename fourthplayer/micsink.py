"""A guest's microphone, played into the machine as a recording device.

The point of it is a call: somebody playing from another house wants to be
heard in Discord on the console, and Discord takes a microphone rather than a
web page.

There is no virtual microphone on Windows and nothing to write one with from
here, so this uses the same trick every streaming tool uses: a loopback cable.
The audio is *played* into the playback half -- "Speakers (VB-Audio Virtual
Cable)" -- and whatever wants to listen selects the recording half, "CABLE
Output", as its microphone. Linux needs no cable at all: a null sink's monitor
is already a recording device, so the same arrangement falls out of PulseAudio
for free.

The audio arrives as Opus over the same WebRTC connection that carries the
picture out, which is the part worth being pleased about: no second protocol,
no second port, no daemon to keep running, and it is encrypted and
NAT-traversed because the picture already had to be.

What this module is: where it goes, and the pipeline that puts it there. Who
is allowed to turn it on is accounts.py's business -- `mic` is granted like
`desk` and asks for an authenticator code for the same reason, because it puts
a live microphone from somebody's room onto this machine.
"""
import logging
import sys
import time

log = logging.getLogger(__name__)

# What a sink has to be called to be a plausible loopback cable. Only used to
# suggest one; a device named in the config is taken as given, because
# somebody with a different cable knows better than this list does.
CABLE_HINTS = ("vb-audio virtual cable", "cable in", "voicemeeter",
               "virtual cable", "vac ", "loopback", "null sink",
               "fourth-player")


def where(cfg):
    """The device a guest's microphone should be played into, or ""."""
    return (getattr(cfg, "guest_mic_device", "") or "").strip()


# How long a list of audio outputs is worth before it is asked for again.
#
# Asking is not free and is not local: starting a GstDeviceMonitor drives the
# platform's device enumeration -- WASAPI on Windows -- on the machine that is
# at that moment capturing the sound it enumerates. The setup page refreshes
# every five seconds and used to ask twice a refresh, once for the list and
# once for the suggestion drawn from it. Reported as opening the setup page
# making the stream freeze over and over, which is exactly the shape: a stall
# every five seconds for as long as the page is open.
#
# It also ran on the event loop, so the pause was not only the audio device's
# -- it was every guest's signalling and data channel too.
#
# Twenty seconds is chosen against what it costs to be wrong. A cable plugged
# in while the page is open takes up to that long to appear in the list, which
# is a short wait; the alternative was a stutter in everybody's picture.
SINKS_TTL = 20.0

_sinks_cache = (0.0, None)


def forget_sinks():
    """Ask the machine again next time. For after something has changed."""
    global _sinks_cache
    _sinks_cache = (0.0, None)


def sinks(gst, now=None):
    """Every audio output this machine has, as (display name, device id).

    The id is what the sink element wants, and it is not the display name:
    wasapi2sink takes a strid, pulsesink takes a node name. Both are read from
    the device monitor rather than guessed at.

    Remembered for SINKS_TTL, because this is asked for on a timer by a page
    that is only ever drawing a dropdown.
    """
    global _sinks_cache
    stamp = time.monotonic() if now is None else now
    when, remembered = _sinks_cache
    if remembered is not None and stamp - when < SINKS_TTL:
        return list(remembered)
    found = []
    monitor = None
    try:
        monitor = gst.DeviceMonitor.new()
        monitor.add_filter("Audio/Sink", None)
        monitor.start()
        for device in monitor.get_devices():
            props = device.get_properties()
            ident = ""
            if props is not None:
                for key in ("device.strid", "device.id", "node.name",
                            "device.name"):
                    try:
                        value = props.get_string(key)
                    except Exception:
                        value = None
                    if value:
                        ident = value
                        break
            found.append((device.get_display_name(), ident))
    except Exception as exc:
        log.debug("could not list the audio outputs: %s", exc)
    finally:
        # Stopped whatever happened. A monitor left running holds a
        # notification client registered with the platform, and one per
        # refresh of a page somebody leaves open is a slow leak into the
        # audio subsystem this host depends on.
        if monitor is not None:
            try:
                monitor.stop()
            except Exception:
                pass
    _sinks_cache = (stamp, list(found))
    return found


def suggest(gst):
    """A sink that looks like a loopback cable, or "" if none does.

    Offered rather than chosen: picking one automatically would mean a guest's
    voice arriving in somebody's speakers the first time they pressed the
    button, and the difference between that and what was wanted is a room full
    of feedback.
    """
    # Ranked by which hint matched, not by which device came first. The
    # machine this was written against lists a Voicemeeter input before the
    # plain cable, so "the first device matching any hint" suggested the
    # complicated one -- and CABLE_HINTS is already written best-first.
    # Ranked by which hint matched, then against the multi-channel variants a
    # cable offers alongside its ordinary one. VB-Audio presents both "CABLE
    # In 16 Ch" and a plain "Speakers" endpoint, and for one person talking
    # the sixteen-channel one is the wrong half of a right answer.
    best, rank = "", (len(CABLE_HINTS), 1)
    # Through the cached list: this is asked for beside it, on the same
    # refresh, and enumerating the machine's audio devices twice to draw one
    # dropdown was half of the stall it used to cause.
    for name, _ident in sinks(gst):
        low = (name or "").lower()
        for index, hint in enumerate(CABLE_HINTS):
            if hint in low:
                here = (index, 1 if "16 ch" in low or " ch " in low else 0)
                if here < rank:
                    best, rank = name, here
                break
    return best


def sink_element():
    """The element that plays audio out on this platform."""
    return "wasapi2sink" if sys.platform == "win32" else "pulsesink"


def describe(device, name_to_id=None, latency_ms=40):
    """The tail of a pipeline that plays audio into `device`.

    The device is matched by display name against what the monitor reported,
    because a display name is what somebody can recognise in a list and a
    strid is a GUID. If it cannot be matched it is passed through as-is: a
    person who typed an id knows what they meant.
    """
    element = sink_element()
    ident = (name_to_id or {}).get(device, device)
    # Not leaky, and sized for a conversation.
    #
    # This queue was 100ms with leaky=downstream, on the reasoning that late
    # audio is worse than missing audio. That is right for the game's sound
    # going out and wrong for somebody talking: a voice arriving 150ms late is
    # a conversation, and a voice with syllables removed is not. Any hesitation
    # in the sink -- and low-latency=true on wasapi2sink guarantees some, since
    # it asks for the smallest buffer the device will give -- threw speech
    # away. Reported exactly as words being cut off before they finished.
    #
    # 250ms is enough to ride out a device hiccup and still inside what a
    # conversation tolerates, and nothing is dropped: a full queue now pushes
    # back rather than discarding what somebody said.
    # buffer-time is the device's own buffer and it defaults to 200ms, which
    # is a fifth of a second of delay for no benefit on a machine that is
    # decoding one voice. latency-time is how often it is topped up; the
    # default 10ms is already fine.
    hold = max(10, int(latency_ms or 40))
    device_us = hold * 1000
    if element == "wasapi2sink":
        extra = " buffer-time=%d latency-time=10000" % device_us
    else:
        extra = " buffer-time=%d latency-time=10000" % device_us
    # The queue absorbs a hiccup without discarding speech. Capacity, not
    # delay: it only holds anything when the sink is behind.
    return ("queue max-size-time=%d max-size-buffers=0 max-size-bytes=0 "
            "! audioconvert ! audioresample "
            "! %s device=\"%s\"%s sync=false"
            % (max(60, hold * 3) * 1000000, element, ident, extra))
