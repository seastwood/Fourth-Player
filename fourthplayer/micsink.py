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


def sinks(gst):
    """Every audio output this machine has, as (display name, device id).

    The id is what the sink element wants, and it is not the display name:
    wasapi2sink takes a strid, pulsesink takes a node name. Both are read from
    the device monitor rather than guessed at.
    """
    found = []
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
        monitor.stop()
    except Exception as exc:
        log.debug("could not list the audio outputs: %s", exc)
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


def describe(device, name_to_id=None):
    """The tail of a pipeline that plays audio into `device`.

    The device is matched by display name against what the monitor reported,
    because a display name is what somebody can recognise in a list and a
    strid is a GUID. If it cannot be matched it is passed through as-is: a
    person who typed an id knows what they meant.
    """
    element = sink_element()
    ident = (name_to_id or {}).get(device, device)
    # low-latency where the element has it: this is somebody talking, and a
    # buffer that would be unremarkable for music is a conversation with a
    # delay in it.
    extra = " low-latency=true" if element == "wasapi2sink" else ""
    return ("queue max-size-time=100000000 leaky=downstream "
            "! audioconvert ! audioresample "
            "! %s device=\"%s\"%s sync=false" % (element, ident, extra))
