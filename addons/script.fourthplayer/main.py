"""Fourth Player, from the sofa.

Opens and closes sessions, shows the link and QR to send people, watches who is
connected, adds time, removes a player, and starts the service when it is not
running.

Everything is one JSON line over the server's Unix socket. No decision lives
here: how long a session may run, who may join, when a pad is released and what
a slot means all belong to the server, which can be tested without Kodi.
"""

import json
import os

import xbmc
import xbmcgui

import fpclient as C
import panels

ADDON_NAME = "Fourth Player"

DURATIONS = [30, 60, 120, 240]
# Two answers that are not a number of minutes from the list: type your own,
# and don't have one. Both are still a number of minutes on the wire -- zero
# means no deadline.
CUSTOM = "custom"
NO_LIMIT = "unlimited"
EXTENSIONS = [15, 30, 60]

CONFIG_PATH = os.path.expanduser("~/.config/fourth-player/config.json")

# Frame rate costs more than resolution on the hardware this was built for, so
# the presets trade it first.
# Latency first: the bitrate has to fit the thinnest link in use, because
# nothing here adapts it. Anything that does not fit becomes delay.
# Each of these sets every field, so switching between them cannot leave a
# stale one behind from the last choice -- tests/test_addon.py enforces that.
#
# 1080p30 is the default here, and the story behind that is worth keeping
# because it was got wrong twice.
#
# A GameCube game kept hanging the graphics card mid-session -- a real reset,
# VRAM lost, the picture black and the emulator unkillable. It happened sooner
# at 1080p than at 720p, so 1080p looked like the cause and the default was put
# back. Then it happened at 720p as well. Then it turned out that one game did
# it -- Smuggler's Run, which drives around an open world -- while another on
# the same emulator, Mario Power Tennis, played for as long as anyone liked.
#
# So it is a game that pushes this card past what it will do while also
# encoding, not a resolution. Resolution changes how quickly that shows up,
# which is exactly what made it look like the culprit. Anything that hangs the
# card will announce itself in `dmesg | grep "VRAM is lost"`, which is the only
# reliable way to tell.
# Measured twice from a clean boot, running a GameCube game with a guest
# connected:
#
#   720p30   four minutes, 28.8-29.7 fps throughout, no errors at all
#   1080p30  ninety seconds, then:
#              amdgpu: GPU reset begin!. Source: 1
#              amdgpu: suspend of IP block <vce_v3_0> failed -22
#              amdgpu: VRAM is lost due to GPU reset!
#
# vce_v3_0 is the hardware encoder. Once it goes the game is black, the stream
# is dead and the card needs a reboot. A new power supply was fitted between
# those two measurements and made no difference, so it is the encoder's
# throughput rather than the power it draws.
#
# Which is a fact about a Polaris card, not about 1080p. A machine with a
# newer encoder may well be fine, so the option stays -- named so that nobody
# picks it without knowing what it can do.
QUALITY = [
    ("Big screen — 1080p30, 3 Mb/s (default)",
     dict(width=1920, height=1080, fps=30, bitrate_kbps=3000,
          queue_ms=60, jitter_ms=100, h264_profile="main", audio_frame_ms=20)),
    ("Over the internet — 720p30, 1.5 Mb/s",
     dict(width=1280, height=720, fps=30, bitrate_kbps=1500,
          queue_ms=60, jitter_ms=30, h264_profile="main", audio_frame_ms=20)),
    ("Same network — 720p60, 6 Mb/s",
     dict(width=1280, height=720, fps=60, bitrate_kbps=6000,
          queue_ms=80, jitter_ms=25, h264_profile="main", audio_frame_ms=10)),
    ("Poor connection — 540p30, 0.8 Mb/s",
     dict(width=960, height=540, fps=30, bitrate_kbps=800,
          queue_ms=40, jitter_ms=40, h264_profile="main", audio_frame_ms=20)),
    ("Lowest delay — 540p30, 1.2 Mb/s, no smoothing",
     dict(width=960, height=540, fps=30, bitrate_kbps=1200,
          queue_ms=25, jitter_ms=10, h264_profile="main", audio_frame_ms=10)),
    ("Big screen, smoother — 1080p60, 6 Mb/s (delivers fewer frames here)",
     dict(width=1920, height=1080, fps=60, bitrate_kbps=6000,
          queue_ms=60, jitter_ms=100, h264_profile="main", audio_frame_ms=20)),
]


def notify(message, error=False, seconds=4000):
    xbmcgui.Dialog().notification(
        ADDON_NAME, message,
        xbmcgui.NOTIFICATION_ERROR if error else xbmcgui.NOTIFICATION_INFO,
        seconds)


def duration_label(minutes):
    if minutes < 60:
        return "%d minutes" % minutes
    if minutes == 60:
        return "1 hour"
    return "%d hours" % (minutes // 60)


# -- the service ---------------------------------------------------------

def offer_to_start_service():
    dialog = xbmcgui.Dialog()
    if not C.service_installed():
        dialog.ok(ADDON_NAME,
                  "The Fourth Player service is not running, and systemd does "
                  "not know about it.\n\n"
                  "Run [B]install/install.sh[/B] from the fourth-player "
                  "repository once, then try again.")
        return False
    if not dialog.yesno(ADDON_NAME,
                        "The Fourth Player service is not running.\n\n"
                        "Start it now?"):
        return False
    code, output = C.start_service()
    if code != 0:
        dialog.ok(ADDON_NAME, "It would not start:\n\n" + (output or "no reason given"))
        return False
    # systemd returns as soon as it has forked; the socket appears shortly after.
    monitor = xbmc.Monitor()
    for _ in range(20):
        if monitor.waitForAbort(0.5):
            return False
        try:
            C.status()
            notify("Service started.")
            return True
        except C.NotRunning:
            continue
    xbmcgui.Dialog().ok(ADDON_NAME, "It started but is not answering yet. "
                                    "Try again in a moment.")
    return False


def stop_service():
    if not xbmcgui.Dialog().yesno(
            ADDON_NAME,
            "Stop the Fourth Player service?\n\n"
            "Any open session ends and nobody can join until it is started again."):
        return
    code, output = C.stop_service()
    notify("Service stopped." if code == 0 else (output or "Could not stop it."),
           error=code != 0)


# -- sessions ------------------------------------------------------------

def ask_minutes():
    """How long, including answers that are not on the list.

    Returns minutes, 0 for no limit, or None if they backed out.
    """
    dialog = xbmcgui.Dialog()
    options = [duration_label(m) for m in DURATIONS]
    options.append("Something else…")
    options.append("No time limit")
    choice = dialog.select("How long should the session stay open?", options)
    if choice < 0:
        return None
    if choice < len(DURATIONS):
        return DURATIONS[choice]
    if options[choice] == "No time limit":
        # Worth a second question. Every other answer here ends by itself; this
        # one stays open until somebody remembers to close it.
        if not dialog.yesno(
                ADDON_NAME,
                "The session will stay open until you close it — through "
                "reboots, and whether or not anyone is playing.\n\nOpen it "
                "with no time limit?",
                nolabel="Cancel", yeslabel="No limit"):
            return None
        return 0
    typed = dialog.numeric(0, "Minutes", str(DURATIONS[1]))
    if not typed:
        return None
    try:
        minutes = int(typed)
    except ValueError:
        return None
    return minutes if minutes > 0 else None


def start_session(previous="off"):
    # POLICIES is defined further down this file; module level, so it is there
    # by the time anything calls this.
    dialog = xbmcgui.Dialog()
    minutes = ask_minutes()
    if minutes is None:
        return
    # Asked here rather than left to be found in the menu. A session started
    # with this off looks, from the guest's phone, exactly like a feature that
    # does not exist: there is no button, and nothing to explain its absence.
    # Last time's answer is marked, so the usual case is two taps.
    labels = [("> " if value == previous else "  ") + label
              for value, label, _ in POLICIES]
    picked = dialog.select("Can guests start games?", labels)
    if picked < 0:
        return
    reply = C.start_session(minutes)
    if not reply.get("ok"):
        dialog.ok(ADDON_NAME, reply.get("error", "The session would not start."))
        return
    if POLICIES[picked][0] != "off":
        answer = C.set_policy(POLICIES[picked][0])
        if not answer.get("ok"):
            notify(answer.get("error", "Could not set that."), error=True)
    panels.show_invite(C.status, new_invite)


def new_invite():
    """A fresh link and PIN for the session that is already running.

    Not the same as closing and reopening. Everyone playing keeps their slot,
    because a guest is identified by their own token rather than by the invite
    they arrived on -- which is what makes this the right answer after a
    restart, when the clear pair is gone by design and the old advice was to
    throw the whole session away.
    """
    answer = C.reshare()
    if answer.get("error") or answer.get("ok") is False:
        notify(answer.get("error", "Could not make a new link."), error=True)
        return False
    return True


def reshare_warning(status):
    """What a new pair actually costs, which depends on how guests get in."""
    if (status or {}).get("require_link"):
        return ("Any link or QR code shared earlier stops working, including "
                "one saved to somebody's home screen.")
    # Guests are joining on the address and a PIN, so a saved shortcut is
    # still a saved shortcut -- only the number changes.
    return ("A shortcut somebody saved still works; they will need the new "
            "PIN.")


def new_invite_asked(status=None):
    status = status or C.status()
    if not xbmcgui.Dialog().yesno(
            ADDON_NAME,
            "Make a new link and PIN?[CR][CR]Everyone playing keeps their "
            "place. " + reshare_warning(status),
            nolabel="Cancel", yeslabel="New link"):
        return
    if new_invite():
        panels.show_invite(C.status, new_invite)


def extend_session(status=None):
    if (status or {}).get("unlimited"):
        xbmcgui.Dialog().ok(ADDON_NAME,
                            "This session has no time limit, so there is "
                            "nothing to add to.")
        return
    choice = xbmcgui.Dialog().select(
        "Add how much time?", ["%s more" % duration_label(m) for m in EXTENSIONS])
    if choice < 0:
        return
    reply = C.extend(EXTENSIONS[choice])
    if reply.get("ok"):
        notify("%d minutes left." % (reply.get("remaining", 0) // 60))
    else:
        notify(reply.get("error", "Could not add time."), error=True)


def remove_player(status):
    guests = status.get("guests") or []
    if not guests:
        notify("Nobody has joined yet.")
        return
    labels = ["%s — %s" % (g["label"], "playing" if g["connected"] else "away")
              for g in guests]
    choice = xbmcgui.Dialog().select("Remove which player?", labels)
    if choice < 0:
        return
    guest = guests[choice]
    if not xbmcgui.Dialog().yesno(
            ADDON_NAME,
            "Remove %s?\n\nTheir controller stops immediately and the link will "
            "not let them back in." % guest["label"]):
        return
    reply = C.kick(guest["slot"])
    notify("%s removed." % guest["label"] if reply.get("ok")
           else reply.get("error", "Could not remove them."),
           error=not reply.get("ok"))


def close_session():
    if not xbmcgui.Dialog().yesno(
            ADDON_NAME,
            "Close the session?\n\nEveryone is disconnected and the link stops "
            "working."):
        return
    reply = C.stop_session()
    notify("Session closed." if reply.get("ok")
           else reply.get("error", "Could not close it."),
           error=not reply.get("ok"))


# -- quality -------------------------------------------------------------

def set_quality(session_open):
    dialog = xbmcgui.Dialog()
    choice = dialog.select("Picture quality", [name for name, _ in QUALITY])
    if choice < 0:
        return
    _, settings = QUALITY[choice]

    config = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as handle:
                config = json.load(handle)
        except (OSError, ValueError):
            config = {}
    config.update(settings)
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
    except OSError as exc:
        dialog.ok(ADDON_NAME, "Could not save it:\n\n%s" % exc)
        return

    # The pipeline is built when a session opens, so this takes effect then --
    # and the service reads its config at startup, so it needs a restart too.
    if not C.service_installed():
        notify("Saved. Restart the server for it to take effect.")
        return
    prompt = ("Restart the service now so it takes effect?\n\n"
              "The open session will end." if session_open else
              "Restart the service now so it takes effect?")
    if not dialog.yesno(ADDON_NAME, prompt):
        notify("Saved. It applies next time the service starts.")
        return
    code, output = C.restart_service()
    notify("Restarted." if code == 0 else (output or "Could not restart it."),
           error=code != 0)


# -- the menu ------------------------------------------------------------

# -- letting guests start games -----------------------------------------

# The wording is the whole interface here: each of these hands somebody outside
# the house a different amount of control over this television, and the
# difference has to be readable at a glance from the sofa.
POLICIES = [
    ("off", "No — only I start games",
     "Guests can play what is already running, and nothing else."),
    ("approve", "Ask me first (30 seconds to answer)",
     "Hold both shoulder buttons to allow it. No answer means no."),
    ("idle", "Yes, when nothing is playing",
     "Guests can start a game once the screen is free."),
    ("open", "Yes, any time",
     "Guests can start a game over the top of whatever is playing."),
]


def choose_url(status):
    """The address guests' links are built on.

    Without this the link points at this machine's address on the local
    network, which works from the sofa and nowhere else -- and the failure is
    silent in the worst way: the link looks right, gets sent to a friend, and
    does nothing. So the current setting is shown, and what a link will look
    like afterwards.
    """
    dialog = xbmcgui.Dialog()
    current = status.get("public_url", "")
    typed = dialog.input("Address for guests' links",
                         defaultt=current or "https://",
                         type=xbmcgui.INPUT_ALPHANUM)
    if typed is None:
        return                                 # backed out; nothing changes
    typed = typed.strip()
    if typed in ("https://", "http://"):
        typed = ""                             # left as the prompt: clear it
    if typed == current:
        return
    if not typed and not dialog.yesno(
            ADDON_NAME,
            "Clear the address?\n\nLinks will point at this machine on the "
            "local network, which only works for people in the house.",
            nolabel="Keep it", yeslabel="Clear"):
        return
    reply = C.set_url(typed)
    if not reply.get("ok"):
        dialog.ok(ADDON_NAME, reply.get("error", "That address was not accepted."))
        return
    example = reply.get("example_url", "")
    if reply.get("public_url"):
        dialog.ok(ADDON_NAME, "Links will look like:\n\n%s" % example)
    else:
        notify("Links now point at this machine on the network.")


def choose_share(status):
    """Whether guests may drive one controller together.

    For the games that are played by passing a pad round a sofa -- everybody
    taking a turn is player one. Swapping seats does that one at a time; this
    is both of them holding the controls.
    """
    shared = bool(status.get("share_pads"))
    labels = [("  " if shared else "> ") + "One each  (normal)",
              ("> " if shared else "  ") + "Share, for pass-the-pad games"]
    choice = xbmcgui.Dialog().select("Can players share a controller?", labels)
    if choice < 0:
        return
    want = (choice == 1)
    if want == shared:
        return
    reply = C.set_share(want)
    if not reply.get("ok"):
        notify(reply.get("error", "Could not change that."), error=True)
    else:
        notify("Guests may share a controller" if want
               else "Each guest gets their own controller")


def choose_pin(status):
    """Set a PIN, or go back to a new random one each session.

    The point of setting one is that it stops changing -- so the owner stops
    walking to the television to read six new digits before anybody can join.
    What it costs is said out loud below, because a secret that never rotates
    is a different bargain from one that is thrown away with the session.
    """
    fixed = bool(status.get("pin_fixed"))
    labels = [("> " if fixed else "  ") + "Use a PIN I choose…",
              ("  " if fixed else "> ") + "A new random PIN each session  (safer)"]
    choice = xbmcgui.Dialog().select("What PIN do guests type?", labels)
    if choice < 0:
        return

    if choice == 1:
        if not fixed:
            return
        reply = C.set_pin("")
        if not reply.get("ok"):
            notify(reply.get("error", "Could not change that."), error=True)
        else:
            notify("Each session will get a new PIN")
        return

    if not fixed and not xbmcgui.Dialog().yesno(
            ADDON_NAME,
            "A PIN you set is used for every session from now on, so it is "
            "one secret that stops changing: everybody you have ever invited "
            "keeps knowing it.\n\nWrong tries still lock an address out, and "
            "it is never stored in the clear anywhere a guest can reach.\n\n"
            "Set your own PIN?",
            nolabel="Leave it random", yeslabel="Set one"):
        return

    # numeric input, so the remote's number pad is what answers it
    typed = xbmcgui.Dialog().numeric(0, "PIN for guests to type (4 to 12 digits)")
    if not typed:
        return
    reply = C.set_pin(typed)
    if not reply.get("ok"):
        # The service checks the length and refuses a single repeated digit,
        # and its reason is better than anything guessable from here.
        notify(reply.get("error", "Could not set that PIN."), error=True)
    else:
        notify("Guests will type the PIN you set")


def choose_link(status):
    """Whether a guest needs the whole link, or just the address and the PIN.

    Two secrets or one. The PIN alone is six digits against a lockout that
    reaches ten minutes after nine wrong tries, which is roughly a hundred days
    of guessing per address -- so this is a real option and not a reckless one.
    It is still one secret fewer, and worth saying so.
    """
    required = (status.get("require_link") is not False)
    # Named for what somebody has to hand over, because that is how the
    # question arrives: "can they just use the address?"
    labels = [("> " if required else "  ") + "The full link, with its key  (safer)",
              ("  " if required else "> ") + "Just the address and a PIN"]
    choice = xbmcgui.Dialog().select("How do guests get in?", labels)
    if choice < 0:
        return
    want = (choice == 0)
    if want == required:
        return
    if not want and not xbmcgui.Dialog().yesno(
            ADDON_NAME,
            "Anyone who knows the address can then try PINs at it.\n\n"
            "Six digits, and three wrong tries locks them out for a while, so "
            "guessing it is not realistic -- but it is one secret instead of "
            "two.\n\nAllow joining with the PIN alone?",
            nolabel="Keep the link", yeslabel="Allow"):
        return
    reply = C.set_link(want)
    if not reply.get("ok"):
        notify(reply.get("error", "Could not change that."), error=True)
    else:
        notify("Guests need the link." if want
               else "Guests need only the address and the PIN.")


def choose_slots(status):
    """How many can join at once.

    A setting rather than a question at the start, because it is decided once
    and then left. It applies to the next session: pads are made when a session
    opens and the player picker reads the devices at launch, so one that turned
    up later would be a controller the running game never sees.
    """
    current = status.get("slots", 3)
    most = status.get("max_slots", 8)
    labels = []
    for count in range(1, most + 1):
        label = "%d %s" % (count, "player" if count == 1 else "players")
        if count == 3:
            label += "  (default)"
        labels.append(("> " if count == current else "  ") + label)
    choice = xbmcgui.Dialog().select("How many can join at once?", labels)
    if choice < 0:
        return
    reply = C.set_slots(choice + 1)
    if not reply.get("ok"):
        notify(reply.get("error", "Could not change that."), error=True)
    elif status.get("open"):
        notify("The session open now keeps %d. The next one holds %d."
               % (current, choice + 1), seconds=6000)
    else:
        notify("The next session will hold %d." % (choice + 1))


def choose_policy(status):
    current = (status.get("launch") or {}).get("policy", "off")
    labels = []
    for value, label, detail in POLICIES:
        labels.append(("> " if value == current else "  ") + label)
    choice = xbmcgui.Dialog().select("Can guests start games?", labels)
    if choice < 0:
        return
    value, label, detail = POLICIES[choice]
    if value == "open" and not xbmcgui.Dialog().yesno(
            ADDON_NAME,
            "Any guest will be able to stop the game you are playing and "
            "start a different one, without asking.\n\nAllow that?",
            nolabel="No", yeslabel="Allow"):
        return
    reply = C.set_policy(value)
    notify(detail if reply.get("ok")
           else reply.get("error", "Could not change that."),
           error=not reply.get("ok"))


def answer_request(waiting):
    """Say yes or no to the guest waiting on an answer."""
    approve = xbmcgui.Dialog().yesno(
        "%s wants to play" % waiting.get("who", "A guest"),
        "%s\n\nStart it? This stops whatever is playing now."
        % waiting.get("label", "a game"),
        nolabel="No", yeslabel="Start it")
    reply = C.approve() if approve else C.deny()
    if not reply.get("ok"):
        notify(reply.get("error", "Could not answer that."), error=True)
    elif approve:
        notify("Starting %s" % waiting.get("label", "the game"))
    else:
        notify("Refused.")


def main():
    try:
        status = C.status()
    except C.NotRunning:
        if not offer_to_start_service():
            return
        try:
            status = C.status()
        except C.NotRunning:
            return

    if not status.get("ok"):
        xbmcgui.Dialog().ok(ADDON_NAME, status.get("error", "No answer from the service."))
        return

    if not status.get("open"):
        entries = [
            ("Open a session…",
             lambda: start_session((status.get("launch") or {}).get("policy", "off"))),
            ("Can guests start games?…", lambda: choose_policy(status)),
            ("How many can join?…", lambda: choose_slots(status)),
            ("How do guests get in?…", lambda: choose_link(status)),
            ("What PIN do guests type?…", lambda: choose_pin(status)),
            ("Can players share a controller?…", lambda: choose_share(status)),
            ("Address for links…", lambda: choose_url(status)),
            ("Picture quality…", lambda: set_quality(False)),
            ("Stop the service", stop_service),
        ]
        heading = "Fourth Player — nothing open"
    else:
        guests = len(status.get("guests") or [])
        remaining = status.get("remaining")
        heading = ("Fourth Player — %d playing, %s"
                   % (guests, "no time limit" if remaining is None
                      else "%d min left" % (remaining // 60)))
        entries = [
            ("Show the link, PIN and QR code",
             lambda: panels.show_invite(C.status, new_invite)),
            ("New link and PIN…", lambda: new_invite_asked(status)),
            ("Can guests start games?…", lambda: choose_policy(status)),
            ("Who is playing…", lambda: panels.show_monitor(C.status)),
            ("Add more time…", lambda: extend_session(status)),
            ("Remove a player…", lambda: remove_player(C.status())),
            ("How many can join?…", lambda: choose_slots(status)),
            ("How do guests get in?…", lambda: choose_link(status)),
            ("What PIN do guests type?…", lambda: choose_pin(status)),
            ("Can players share a controller?…", lambda: choose_share(status)),
            ("Address for links…", lambda: choose_url(status)),
            ("Close the session", close_session),
            ("Picture quality…", lambda: set_quality(True)),
        ]

    waiting = (status.get("launch") or {}).get("pending")
    if waiting:
        answer_request(waiting)
        return

    choice = xbmcgui.Dialog().select(heading, [label for label, _ in entries])
    if choice >= 0:
        entries[choice][1]()


if __name__ == "__main__":
    main()
