"""One live session: an invite, some pads, one pipeline, and the guests on it.

This is the only module that knows about all four of the others, and it is
deliberately the only one. `invites` does not know what a pad is; `pads` has
never heard of a network; `video` cannot tell a guest from a slot number.

The dead-man sweep lives here because it is the one rule that spans two of them:
it is `video` that stops hearing from a guest and `pads` that has to open their
buttons, and neither can reasonably own the other.
"""

import asyncio
import functools
import json
import logging
import math
import os
import subprocess
import sys
import time

from . import (catalogue as cataloguelib, gpu, invites, launcher,
               pads as padlib, protocol, retroarch, screen)
from .video import Stage, best_shared_codec, CODEC_PREFERENCE

# Better first, so "is this a step down" is a comparison rather than a guess.
CODEC_RANK = {name: len(CODEC_PREFERENCE) - i
              for i, name in enumerate(CODEC_PREFERENCE)}


def _common(guests):
    """The codecs every one of these guests can decode.

    A guest that told us nothing constrains nobody: they are sent H.264, which
    they would have been anyway.
    """
    stated = [set(c.lower().replace("video/", "") for c in g.codecs)
              for g in guests if g.codecs]
    if not stated:
        return []
    shared = set.intersection(*stated)
    return sorted(shared)

log = logging.getLogger("fourthplayer.session")

SWEEP_INTERVAL = 0.05

# ---- chat ------------------------------------------------------------------
#
# Guests can see each other's controllers moving and cannot say a word to each
# other, which is a strange way to play together. So: a line of text, from
# whoever is holding a pad, to everybody -- including the television, because
# the person in the room is playing too and a conversation they cannot see is
# one happening about them.
#
# Deliberately small. No history beyond the session, no names beyond the ones
# guests already chose, nothing kept on disk. A chat that outlives the evening
# is a different thing to own.
CHAT_KEEP = 60              # messages remembered, for somebody joining late
CHAT_LIMIT = 240            # characters, after which it is cut
CHAT_GAP = 0.4              # seconds one guest must leave between messages

# Where a live session is written down so a restart does not end it. The
# process has segfaulted inside the GPU's video driver more than once, and
# systemd puts it straight back -- but everything about the session lived in
# memory, so every guest was locked out of something that no longer existed.
# What a guest may do about starting a game. Off is the default everywhere,
# because the failure mode of the other three is somebody else's television.
#
#   off      nothing; the page does not even offer a game list
#   open     start anything, including over whatever is playing now
#   idle     start anything, but only when the screen is free
#   approve  ask, and the owner has APPROVAL_SECONDS to answer
# How long to wait before rebuilding a guest's connection ourselves. Long
# enough for a browser that noticed the same failure to reconnect and ask for
# one, short enough that a guest whose browser did not notice is not left
# staring at a frozen picture.
REBUILD_GRACE = 2.0

# How long a name may be, and what it may contain. It is drawn on somebody
# else's television and printed in their logs, so it is trimmed to something
# that fits a card and stripped of anything that is not a printing character --
# not as a security measure, since it cannot reach a shell or a page unescaped,
# but because a name full of newlines is a card that no longer reads.
# Eight. Long enough for a name and short enough that four of them fit on a
# seat list beside a player number, which is where they now have to read.
NAME_MAX = 8


def clean_name(name):
    """A guest's chosen name, or "" to be called by their slot."""
    text = "".join(c for c in str(name or "") if c.isprintable())
    text = " ".join(text.split())
    return text[:NAME_MAX]


LAUNCH_POLICIES = ("off", "open", "idle", "approve")
APPROVAL_SECONDS = 30.0

# What each guest calls themselves, keyed by the pad they are driving. Written
# for kodi-retrobox's player picker, which is a different program reading
# evdev: evdev knows a device called "Fourth Player 1" and nothing else, so
# without this the picker shows the socket rather than the person. A file
# rather than a question over the control socket, so that project needs no
# knowledge of this one -- the same coupling through data the catalogue uses.
PAD_NAMES_PATH = os.path.expanduser(
    "~/.local/state/fourth-player/pad-names.json")

# Touched to ask the television's player picker to come back over a game that
# is already running. Coupled the same way and for the same reason: the picker
# watches for this file, and neither program knows the other exists.
REPICK_PATH = os.path.join(os.path.dirname(PAD_NAMES_PATH), "repick")

STATE_PATH = os.path.expanduser("~/.local/state/fourth-player/session.json")

# How long to wait for the pipeline worker before giving up on a guest's
# request. Every add and remove of a peer goes through one thread so they
# cannot race; the cost is that one slow teardown delays everybody behind it.
# Tearing down a peer whose network vanished is exactly that slow case, and a
# guest reconnecting is exactly who is behind it -- so the wait is bounded and
# the guest is told, rather than left watching "rejoining" forever.
PIPELINE_TIMEOUT = 12.0

# How long a guest may hold a slot with no working media. Short, because losing
# it costs them nothing: the invite still remembers them, so a guest whose slot
# is freed and who then reconnects is put back in the *same* slot by their own
# token. The grace period is only there so a brief reconnect does not disturb
# anybody, not to protect somebody who has gone.
GHOST_SECONDS = 25.0

# Once their signalling has gone too, there is nothing left to wait for.
LEFT_SECONDS = 8.0

# How long a guest may be silent before their connection is presumed dead. The
# browser heartbeats its pad state every 50 ms whether or not anything is
# moving, so silence is unambiguous -- and it is the only honest liveness
# signal available. ICE is not one: when a guest simply vanishes, webrtcbin
# leaves the connection sitting at "completed" indefinitely, because nothing
# arrives to contradict it.
SILENCE_SECONDS = 5.0

# Guests are told the session is running out at these many seconds remaining,
# so the end is something you can see coming rather than something that happens
# to you mid-game.
WARN_AT = (300, 120, 30)


class GuestConnection:
    """One browser: a slot, a pad, a WebRTC peer and a socket."""

    # Class-level defaults for the plain values, so that a guest built without
    # running __init__ -- which is how several tests make a minimal one --
    # still answers for them. `health` is None rather than {} on purpose: a
    # mutable default here would be one dict shared by every guest that never
    # set its own.
    health = None
    peer = None
    frames = 0
    held_frames = 0
    # A guest who brought a controller and no screen: somebody sitting beside
    # a player who already has the picture. Their peer carries the input
    # channel and nothing else. In every other way they are an ordinary guest
    # -- their own seat, their own pad, their own name, their own row in the
    # list -- because that is what the rest of this already understands.
    input_only = False

    def __init__(self, session, slot, socket, name=""):
        self.session = session
        self.slot = slot
        self.socket = socket
        self.name = name
        # Which virtual pad this guest drives, which is not the same as which
        # invite slot they hold. RetroArch bound its player ports to specific
        # devices when the game started and will not revisit that until it
        # restarts -- so the way to become player 2 mid-game is to write to the
        # pad that is already player 2, not to renumber anything.
        self.pad_index = slot
        # When they last had a working media connection. A guest is only ever
        # reaped for having none, so this starts now rather than at zero.
        #
        # From the session's clock rather than straight from time.monotonic():
        # the reaper compares this against that clock, and reading the two from
        # different sources made the comparison depend on how long the machine
        # had been switched on. It held for three days and broke within an hour
        # of a reboot, because monotonic() had gone back to counting from a few
        # hundred seconds while the test drove its own clock from a thousand.
        self.media_since = session._now() if session is not None else time.monotonic()
        self.outbox = None      # set by the server; None while signalling is down
        self.on_signal = None   # how to reach them, so a rebuild needs no help
        self.codecs = []        # what their browser said it can decode
        self.peer = None
        # Only one attach for this slot at a time. Two can be asked for at
        # once -- the host rebuilding a peer whose branch errored, and the
        # browser reconnecting after noticing the same failure. The pipeline
        # half of that is already serialised by the single-threaded worker that
        # owns it; what is not is everything around it, including which peer
        # this ends up pointing at. Belt and braces on a slot that has already
        # produced one connection nobody was feeding.
        self.attaching = asyncio.Lock()
        # What they called themselves, or which guest they are. Deliberately
        # not a player number: which player a guest is depends on the ports the
        # running game bound, and numbering them from the slot got it wrong in
        # both directions -- the first guest was called "player 2" while the
        # game called them player 1.
        self.label = name or f"Guest {slot + 1}"
        self.joined_at = time.monotonic()
        self.last_input = 0.0
        self.frames = 0
        self.bad_frames = 0
        # How this guest's connection is actually running, measured at their
        # end and sent here. It has to come from there: round trip time and
        # lost packets are properties of the path to *them*, and the host can
        # only see its own side of it.
        self.health = {}
        # Frames that arrived while the television was in a menu, and so went
        # nowhere. Counted rather than dropped silently: "my controller did
        # nothing" is a question somebody will ask, and this is the answer.
        self.held_frames = 0

    @property
    def pad(self):
        return self.session.pads[self.pad_index]

    def has_media(self, now=None):
        """Whether this guest is actually there.

        Three things have been tried for this and only the last one holds.
        "Do they have a peer" is an object, not a route. "Is their ICE up" is
        better but still wrong: a guest who vanishes leaves webrtcbin sitting
        at `completed` for ever, because nothing arrives to say otherwise --
        so the slot was held by somebody long gone.

        What is left is hearing from them. Their browser sends its pad state
        every 50 ms regardless of whether anything moved, so silence means the
        channel is gone whatever else claims. An open signalling socket counts
        too, for the moment between joining and the first frame.
        """
        if self.peer is None or not getattr(self.peer, "ice_ok", False):
            return False
        stamp = now if now is not None else time.monotonic()
        if self.last_input and stamp - self.last_input < SILENCE_SECONDS:
            return True
        # Nothing heard yet, or nothing recently: fall back to whether they are
        # still holding a socket open, which covers a guest who has only just
        # arrived.
        return self.socket is not None and not self.last_input

    def feed(self, data):
        """A frame off the data channel. Never raises: a guest cannot crash us."""
        try:
            state = protocol.decode(data)
        except protocol.ProtocolError:
            self.bad_frames += 1
            if self.bad_frames in (1, 100, 1000):
                log.warning("%s: undecodable input frame (%d so far)",
                            self.label, self.bad_frames)
            return
        self.frames += 1
        # Counted as presence before anything else, and whatever happens next.
        # A guest whose frames are being withheld is still plainly there, and
        # the dead-man switch releases pads for *silence* -- reaping somebody
        # for being held is the wrong answer to the right observation.
        self.last_input = time.monotonic()
        if (self.session is not None and self.session.input_held
                and self.session.driver != self.slot):
            self.held_frames += 1
            return
        # Named, so a shared pad can tell its senders' frames apart
        # and merge them rather than treating each as the other's stale one.
        self.pad.apply(state, sender=self.slot)


class LiveSession:
    """Everything that exists only while the session is open."""

    # Who, if anybody, may use the screen directly. A class-level default for
    # the same reason the guest has them: a session built without __init__ --
    # which is how some tests make one -- is still asked who is driving.
    driver = None
    # The row this session last put on the television, for "start it again".
    last_started = None

    def __init__(self, cfg, loop, now=time.monotonic):
        self.cfg = cfg
        self.loop = loop
        self._now = now
        self.invite = None
        self.stage = None
        self.pads = None
        # Whether guest frames are reaching the machine, and what is in front
        # if they are not. See screen.py: a guest's pad is wired to the
        # machine rather than to the game, so what it can do depends entirely
        # on what has the foreground.
        self.input_held = False
        self.hold_reason = ""
        # One guest the host has named who may drive whatever is in front,
        # while everybody else's frames stop at the television. A slot number,
        # or None, which is what it is until somebody says otherwise.
        #
        # One at a time on purpose: "who is driving" is a single answer, not a
        # set of ticks. Two people on one remote desktop is a mess, and a
        # single value makes taking it back unambiguous.
        #
        # `on_notice_one` is set by the server beside `on_notice`: some things
        # are the same for everybody and some are not, and this is the first
        # that is not.
        self.on_notice_one = None
        # What has been said this session, and when each guest last spoke.
        self.chat = []
        self._chat_id = 0
        self._chat_at = {}
        self.driver = None
        # And what it was granted against. A permission to drive Moonlight is
        # not a permission to drive Steam's store, and the way somebody gets
        # from one to the other is closing one and opening the other -- which
        # nobody would think of as revoking anything. So it is scoped to the
        # thing in front when it was given, and dropped when that changes.
        self.driver_shell = ""
        self.guests = {}          # slot -> GuestConnection
        self._overlay = None
        self._previous_dpm = None
        self._warned = set()
        self._ticks = 0
        self.on_notice = None      # set by the server: broadcast to guests
        self._sweeper = None
        self.opened_at = None
        self._previous_dpm = None
        self.launch_policy = "off"
        # Fixed for the life of the session. Pads are created when it opens and
        # RetroArch's picker reads the devices at launch, so a pad that appears
        # later is a pad the running game will never see -- which makes
        # changing this mid-session a setting that silently does nothing.
        self.slots = cfg.slots
        self.catalogue = cataloguelib.Catalogue()
        # The one launch request waiting on the owner, if any. Only ever one:
        # a queue of these is a queue of interruptions.
        self.pending = None

    # -- lifecycle ----------------------------------------------------------

    @property
    def open(self):
        return self.invite is not None and self.invite.alive(self._now())

    def start(self, duration_seconds, invite=None, slots=None):
        if self.invite is not None:
            raise RuntimeError("a session is already open")
        now = self._now()
        self.slots = int(slots or self.cfg.slots)
        self.invite = invite or invites.Session(
            slots=self.slots, duration=duration_seconds, now=now,
            pin=getattr(self.cfg, "fixed_pin", "") or None)
        # A restored invite brings its own count: the pads have to match the
        # slots the people already holding the link were given.
        self.slots = self.invite.slots
        # Pads first, and before any guest can arrive. kodi-retrobox's player
        # picker enumerates evdev devices at launch, so a pad that appears
        # after RetroArch starts is a pad that game will never see.
        self.codec = (self.cfg.codec or "auto").lower()
        policy = (getattr(self.cfg, "guest_launch", "off") or "off").lower()
        self.launch_policy = policy if policy in LAUNCH_POLICIES else "off"
        self.pads = padlib.PadSet(self.slots,
                                  guide=self.cfg.guest_guide_button)
        # Tell RetroArch what these pads are before anything can read them,
        # or it guesses and the guest's A button ends up somewhere else.
        # The same answer the pads were built with, or RetroArch binds by
        # numbers the device does not use.
        retroarch.write_profiles(list(self.pads.names),
                                 guide=self.cfg.guest_guide_button)
        self.stage = Stage(self.cfg, self.loop,
                           codec=None if self.codec == "auto" else self.codec)
        self.stage.start()
        self.opened_at = now
        # Thresholds already behind us at the start are not warnings, they are
        # noise: a two-minute session would otherwise announce "ending in five
        # minutes" and "ending in two minutes" the instant it opened.
        self._warned = {w for w in WARN_AT if w >= duration_seconds}
        if self.cfg.manage_gpu_clocks:
            self._previous_dpm = gpu.current()
            gpu.set_level("high")
        self._sweeper = self.loop.create_task(self._sweep_forever())
        self._start_overlay()
        self.save()
        log.info("session open for %s, %d slots, pads at %s",
                 "no fixed time" if math.isinf(duration_seconds)
                 else "%.0f minutes" % (duration_seconds / 60), self.slots,
                 ", ".join(self.pads.names))
        return self.invite

    def _start_overlay(self):
        """The on-screen card, in its own process so it cannot take us with it."""
        if not self.cfg.overlay:
            return
        environment = dict(os.environ)
        environment.setdefault("DISPLAY", self.cfg.display)
        try:
            self._overlay = subprocess.Popen(
                [sys.executable, "-m", "fourthplayer.overlay"],
                # stderr is inherited on purpose, so it lands in this service's
                # journal. Discarding it meant an overlay that failed left no
                # record anywhere -- and one that fails is indistinguishable
                # from a feature nobody wired up.
                env=environment, stdout=subprocess.DEVNULL)
        except OSError as exc:
            log.warning("could not draw the on-screen card: %s", exc)
            self._overlay = None

    def _stop_overlay(self):
        if getattr(self, "_overlay", None) is None:
            return
        self._overlay.terminate()
        try:
            self._overlay.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._overlay.kill()
        self._overlay = None

    def stop(self, reason="closed"):
        """Close the session. Blocks; prefer `astop` from inside the loop."""
        parts = self._begin_stop(reason)
        if parts:
            self._finish_stop(*parts)

    async def astop(self, reason="closed"):
        """Close without blocking the event loop.

        Tearing a VAAPI pipeline down and destroying uinput devices both take
        real time, and doing them inline froze everything the server was
        supposed to still be doing -- which is how a session simply running out
        of time managed to drag the whole machine down with it.
        """
        parts = self._begin_stop(reason)
        if parts:
            await self.loop.run_in_executor(None, self._finish_stop, *parts)

    def _begin_stop(self, reason):
        """Everything that is fast, in the order that is kindest to a game."""
        if self.invite is None:
            return None
        log.info("session closing (%s)", reason)
        self.notify({"t": "closed", "reason": reason})

        # Guests go first, and dropping them releases their pads. A game must
        # see the buttons come up *before* the device disappears from under it,
        # or it is left holding a direction from a controller that no longer
        # exists.
        for slot in list(self.guests):
            self.drop(slot, reason=reason)

        self._stop_overlay()

        # Never cancel the sweeper from inside the sweeper.
        if self._sweeper is not None:
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if current is not self._sweeper:
                self._sweeper.cancel()
            self._sweeper = None

        if self.cfg.manage_gpu_clocks and self._previous_dpm:
            gpu.set_level(self._previous_dpm)
            self._previous_dpm = None

        # The guests were dropped above; this writes the empty result out
        # before the pads themselves go, so no name outlives the session.
        self.publish_pad_names()
        stage, pads = self.stage, self.pads
        self.stage = self.pads = None
        self.invite.destroy()
        self.invite = None
        self.opened_at = None
        self._warned = set()
        return stage, pads

    def _finish_stop(self, stage, pads):
        """The slow half, run off the event loop."""
        if stage is not None:
            stage.stop()
        if pads is not None:
            # A beat between releasing and unplugging, so anything reading
            # these devices processes the release first.
            time.sleep(0.15)
            pads.close()

    def remaining(self):
        return self.invite.remaining(self._now()) if self.invite else 0.0

    @property
    def unlimited(self):
        return self.invite is not None and self.invite.unlimited

    def extend(self, seconds):
        """Push the deadline back. The invite and every guest survive it."""
        if self.invite is None:
            raise RuntimeError("no session is open")
        limit = self.cfg.max_duration_minutes * 60
        total = (self.invite.expires_at - self.invite.started_at) + seconds
        if total > limit:
            seconds = max(0.0, limit - (self.invite.expires_at - self.invite.started_at))
        self.invite.expires_at += seconds
        # Warnings already given are re-armed, so a session extended past a
        # threshold warns again when it comes back round.
        self._warned = {w for w in self._warned if w < self.remaining()}
        log.info("session extended by %.0f minutes, %.0f left",
                 seconds / 60, self.remaining() / 60)
        self.notify({"t": "extended", "remaining": round(self.remaining())})
        return seconds

    # -- guests -------------------------------------------------------------

    def admit(self, token, pin, socket, address, name=""):
        """Spend the invite for a slot. Raises invites.JoinError on refusal."""
        name = clean_name(name)
        slot, guest_token = self.invite.join(
            token, pin, now=self._now(), address=address, label=name,
            require_token=getattr(self.cfg, "require_link", True))
        guest = GuestConnection(self, slot, socket, name)
        self.guests[slot] = guest
        guest.pad                        # plug their controller in
        # Write it down now. The snapshot was only taken when a session opened
        # or somebody left, so a guest who joined and was still playing when
        # the process died was absent from it -- leaving the people actually
        # in the game as the only ones unable to get back in.
        self.save()
        log.info("%s joined from %s", guest.label, address or "unknown")
        # Everybody already in the session hears about it. The one arriving is
        # sent this too, but has no label of their own yet and ignores it.
        self.publish_pad_names()
        self.notify({"t": "arrived", "label": guest.label,
                     "guests": len(self.guests), "slots": self.slots})
        self.publish_people()
        return guest, guest_token

    async def agree_codec(self, guest, guest_codecs):
        """Settle on an encoding everybody watching can decode.

        The picture is encoded once for everybody, so this belongs to the
        session rather than to each guest. Two directions, and they are not
        symmetric:

        Upwards -- to something better -- only while nobody else is connected,
        because there is nobody to disturb.

        Downwards, whenever somebody arrives who cannot decode what is running.
        Everybody already watching renegotiates and loses a second of picture,
        which is a far smaller thing than a guest who cannot join at all. The
        alternative was telling them their browser refused the video and
        leaving them out of the game.
        """
        guest.codecs = list(guest_codecs or [])
        if self.cfg.codec.lower() != "auto" or self.stage is None:
            return self.stage.codec if self.stage else None

        # What every guest in the session -- this one included -- can take.
        everyone = [g for g in self.guests.values()]
        shared = best_shared_codec(_common(everyone), self.cfg.hardware_encode)
        if shared == self.stage.codec:
            return shared

        going_down = CODEC_RANK.get(shared, 0) < CODEC_RANK.get(self.stage.codec, 0)
        if len(everyone) > 1 and not going_down:
            log.info("keeping %s: somebody else is already watching",
                     self.stage.codec)
            return self.stage.codec

        log.info("%s %s (%d guest(s) already watching)",
                 "dropping to" if going_down else "agreeing on", shared,
                 max(0, len(everyone) - 1))
        await self._recapture(shared)
        return shared

    async def _recapture(self, codec):
        """Restart the capture in a different codec and re-offer to everybody."""
        old, self.stage = self.stage, None
        others = [g for g in self.guests.values() if g.peer is not None]
        log.info("recapture: re-offering to %d guest(s)", len(others))
        for other in others:
            other.peer = None
        try:
            await asyncio.wait_for(
                self.loop.run_in_executor(None, old.stop), timeout=10)
        except Exception as exc:
            log.warning("the previous capture would not stop (%s)", exc)
        self.stage = Stage(self.cfg, self.loop, codec=codec)
        self.stage.start()
        # Anybody who was watching needs a fresh offer describing the new
        # encoding; their browsers answer it without being asked twice.
        for other in others:
            if other.on_signal is not None:
                try:
                    await self.attach_peer(other, other.on_signal)
                except Exception as exc:
                    log.warning("%s could not be re-offered (%s)",
                                other.label, exc)

    async def renew(self, guest, on_signal):
        """Give a guest a fresh media connection without a fresh invite.

        What a phone needs when it moves between mobile data and wifi: every
        address it had is gone, so the old connection can only ever be
        declared dead. Re-offering is the whole recovery, and it costs the
        guest nothing -- they keep their slot, their pad and their session.
        """
        self.detach_peer(guest)
        return await self.attach_peer(guest, on_signal)

    def resume(self, guest_token, socket, name=""):
        """A guest whose socket dropped, coming back without the PIN.

        Their slot is held rather than reassigned: it is the same person, and
        making them re-enter a PIN off a television they may no longer be
        looking at is the kind of friction this project exists to remove.
        """
        now = self._now()
        try:
            record = self.invite.guest_for(guest_token, now=now)
        except invites.UnknownGuest:
            # Their slot was given away while they were gone -- which is the
            # normal outcome of a network switch, since the slot goes back the
            # moment they stop being heard from. The token still identifies
            # them, so they take the next free slot rather than being sent to
            # hunt for a PIN that is on somebody else's television.
            slot = self.invite.reclaim(guest_token, now=now)
            record = self.invite.guests[slot]
            log.info("a guest reclaimed slot %d on their own token", slot)
        existing = self.guests.get(record.slot)
        if existing is not None:
            self.detach_peer(existing)
            existing.socket = socket
            return existing
        # The name they gave when they first joined is on the invite's record,
        # so coming back does not turn them into "Player 3" again. A name sent
        # with the resume wins, since they may have just changed it.
        name = clean_name(name) or getattr(record, "label", "") or ""
        if name.startswith("Player "):
            name = ""                       # a slot number is not a name
        guest = GuestConnection(self, record.slot, socket, name)
        self.guests[record.slot] = guest
        guest.pad                        # plug their controller back in
        self.save()
        self.publish_pad_names()
        return guest

    async def attach_peer(self, guest, on_signal):
        """Give a guest a peer, without blocking everybody else's video."""
        async with guest.attaching:
            return await self._attach_peer(guest, on_signal)

    async def _attach_peer(self, guest, on_signal):
        # Remembered so the host can re-offer to them later without being
        # asked -- a codec change, or a connection rebuilt after a failure.
        # Losing this line meant those guests were silently skipped: the
        # capture restarted around them and they were left with a peer that no
        # longer had anything behind it.
        guest.on_signal = on_signal
        # A new peer means a new sender, whose sequence numbers start again at
        # zero. Without this the pad rejects everything they send as stale.
        guest.pad.adopt_new_sender(guest.slot)

        def configure(peer):
            peer.on_input = guest.feed
            peer.on_broken = lambda why: self._peer_broke(guest, peer, why)
            # The media connection dying is what ends a guest -- not their
            # signalling socket, which they only need to arrive and
            # renegotiate.
            peer.on_dead = lambda why: self._peer_died(guest, peer, why)

        job = functools.partial(self.stage.add_peer,
                                f"slot{guest.slot}", on_signal, configure,
                                not guest.input_only)
        try:
            peer = await asyncio.wait_for(
                self.loop.run_in_executor(self.stage.mutations, job),
                timeout=PIPELINE_TIMEOUT)
        except asyncio.TimeoutError:
            # The worker is not serving its queue. Give it a fresh one and try
            # once more, because otherwise this guest -- and every guest after
            # them -- is refused until somebody restarts the service.
            self.stage.reset_worker("an attach did not finish in %.0fs"
                                    % PIPELINE_TIMEOUT)
            peer = await asyncio.wait_for(
                self.loop.run_in_executor(self.stage.mutations, job),
                timeout=PIPELINE_TIMEOUT)
        except RuntimeError as exc:
            # The capture is not running and would not restart. Replacing it
            # was tried and is worse than the disease: taking a pipeline full
            # of live WebRTC transports to NULL can block for longer than any
            # sane timeout, so the old one carries on capturing while its
            # replacement starts, and a fault that survives the rebuild leaves
            # a process with several screen captures competing for one GPU.
            # That was observed twice, at four and five pipelines.
            #
            # Ending the session is a smaller thing to go wrong. The guests are
            # told, the host opens another, and nothing is left running that
            # nobody is watching.
            log.error("%s; ending the session", exc)
            self.notify({"t": "closed", "reason": "the video stopped working"})
            await self.astop(reason="the capture stopped and would not restart")
            raise
        guest.peer = peer
        return peer

    def _peer_broke(self, guest, peer, why):
        """Rebuild a guest's connection after its branch errored.

        Distinct from dying: nothing has told the guest anything, and their
        browser still believes the connection is up. Re-offering is the only
        thing that gets them a picture back, and it costs them their slot only
        if it fails.
        """
        if guest.peer is not peer or self.guests.get(guest.slot) is not guest:
            return
        # Get the failed elements out of the pipeline first and without delay:
        # while they are still in it, the error they raised is the pipeline's
        # problem too, and the next guest to arrive finds a capture that will
        # not take them.
        self.detach_peer(guest)
        if guest.on_signal is None:
            self.drop(guest.slot, reason="its connection broke")
            return
        # Not immediately. The media and the signalling usually fail together
        # -- two ends of one network event -- and the browser reacts to the
        # same failure by reconnecting and asking for a fresh connection. Both
        # sides then build a peer for this slot, a second apart, with nothing
        # in the log to say so: detach_peer frees the name before add_peer
        # takes it, so neither is ever seen as stale. The guest answers one of
        # them and the capture feeds the other, which is a connection where ICE
        # completes, the data channel opens, the buttons work and no video ever
        # arrives -- and only reloading the page gets out of it.
        #
        # So wait a moment and see whether they sort it out themselves, which
        # is the outcome that needs no offer from here at all.
        self.loop.create_task(self._rebuild_later(guest, why))

    async def _rebuild_later(self, guest, why, grace=REBUILD_GRACE):
        await asyncio.sleep(grace)
        if self.guests.get(guest.slot) is not guest:
            return                             # they are gone; nothing to fix
        if guest.peer is not None:
            log.info("%s: %s, and they reconnected on their own; leaving their "
                     "new connection alone", guest.label, why)
            return
        if guest.outbox is None:
            # No way to reach them. An offer would go into a queue nobody
            # drains, and they will resume when their network comes back.
            log.info("%s: %s, and their signalling is still down; waiting for "
                     "them rather than offering into the dark", guest.label, why)
            return
        await self._rebuild(guest, why)

    async def _rebuild(self, guest, why):
        try:
            await self.renew(guest, guest.on_signal)
            log.info("%s: connection rebuilt after %s", guest.label, why)
        except Exception as exc:
            log.warning("%s: could not be rebuilt (%s); freeing the slot",
                        guest.label, exc)
            self.drop(guest.slot, reason="its connection could not be rebuilt")

    def _peer_died(self, guest, peer, why):
        """A peer's media ended. Only act if it is still the current one.

        A guest who reloads their page leaves the old peer dying while the new
        one is already carrying them. That death arrives seconds later and used
        to drop *the slot*, which by then belonged to the replacement -- so
        refreshing the page reconnected you and then threw you out again, with
        nothing to show for it but a page stuck on "rejoining".
        """
        if guest.peer is not peer:
            log.debug("%s: an old peer died (%s); the current one is fine",
                      guest.label, why)
            return
        if self.guests.get(guest.slot) is not guest:
            log.debug("slot %d has moved on; ignoring a dead peer", guest.slot)
            return
        self.drop(guest.slot, reason=why)

    def detach_peer(self, guest, background=True):
        """Let a peer go. The slow half happens off the event loop.

        Tearing down a *live* webrtcbin -- DTLS, SRTP and ICE all up -- takes
        seconds, and doing it inline froze the whole server while a guest was
        waiting to be let back in. Measured at 2.3 s on the target machine,
        which is 2.3 s of every other guest's video not being served either.
        """
        peer, guest.peer = guest.peer, None
        # Only this guest's contribution. On a shared pad, one
        # of them dropping out must not take the controls away from the other.
        guest.pad.forget(guest.slot)
        if peer is None:
            return
        # Its death is expected from here on, and must not be read as the
        # guest's death -- particularly not the guest replacing them.
        peer.on_dead = None
        if self.stage is not None:
            self.stage.take_peer(peer.id)
        if background and self.stage is not None:
            # The same single worker that adds peers, so a teardown and the
            # attach that replaces it cannot touch the pipeline at once.
            self.stage.mutations.submit(peer.detach)
        else:
            peer.detach()

    def drop(self, slot, reason="left"):
        """Let a guest go, and give the slot back to the invite.

        Both halves matter. Slots are allocated by the invite, not here, so a
        guest removed from this list while the invite still held their slot
        left a session that reported empty slots and refused everybody who
        asked for one -- which is exactly as confusing as it sounds.
        """
        guest = self.guests.pop(slot, None)
        if guest is None:
            return False
        # Whoever was driving stops driving by leaving. Nobody inherits it:
        # the next guest into that seat is a different person, and a
        # permission that arrives with a chair is not one anybody granted.
        self.forget_driver_if(slot)
        self.detach_peer(guest)
        # Unplug their controller. A seat nobody is sitting in takes a player
        # port away from whoever is actually holding something.
        if self.pads is not None and 0 <= guest.pad_index < len(self.pads):
            self.pads.release(guest.pad_index)
        if self.invite is not None:
            self.invite.release(slot, now=self._now())
            self.save()
        self.publish_pad_names()
        self.publish_people()
        log.info("%s %s", guest.label, reason)
        return True

    def kick(self, slot):
        """Remove a guest and make sure the link will not let them back."""
        self.drop(slot, reason="was removed")
        return self.invite.kick(slot)

    def _who_by_pad(self):
        """{pad index as a string: [names]}, in slot order."""
        out = {}
        for guest in sorted(self.guests.values(),
                            key=lambda g: getattr(g, "slot", 0)):
            out.setdefault(str(guest.pad_index), []).append(guest.label)
        return out

    def set_pad(self, guest, index):
        """Move a guest onto a different virtual pad, swapping if it is taken.

        Instant and needs no restart, which is the point: a game that is
        already running has its ports bound to devices, and somebody arriving
        halfway through could otherwise only be given controls by stopping the
        game and starting again.
        """
        if self.pads is None:
            raise ValueError("no session is open")
        index = int(index)
        if not 0 <= index < len(self.pads):
            raise ValueError("there is no controller %d" % (index + 1))
        if index == guest.pad_index:
            return guest.pad_index

        # Sharing means nobody is displaced: both of you drive that pad, and
        # what the game sees is the two of you merged. It is how a hot-seat
        # game is meant to be played -- the pad goes round the sofa and
        # everybody taking a turn is player one -- and it is off by default,
        # because when everybody is their own player being silently joined to
        # somebody else's controller would be baffling.
        sharing = bool(getattr(getattr(self, "cfg", None),
                               "share_pads", False))
        other = None if sharing else next(
            (g for g in self.guests.values()
             if g is not guest and g.pad_index == index), None)
        # Let go of everything on both pads first. Moving a guest whose thumb
        # is on a direction would otherwise leave that direction held down on a
        # pad nobody is driving any more, and the character walks into a wall.
        guest.pad.forget(guest.slot)
        if other is not None:
            other.pad.forget(other.slot)
            other.pad_index = guest.pad_index
        was, guest.pad_index = guest.pad_index, index
        guest.pad.adopt_new_sender(guest.slot)
        if other is not None:
            other.pad.adopt_new_sender(other.slot)
            log.info("%s and %s swapped pads (%d <-> %d)",
                     guest.label, other.label, was, index)
        else:
            # Nobody swapped into the seat they left, so nobody is in it --
            # and an empty seat must not keep a player port to itself.
            self.pads.release(was)
            log.info("%s moved from pad %d to pad %d", guest.label, was, index)
        self.publish_pad_names()
        self.notify({"t": "pads", **self.pad_state()})
        self.publish_people()
        return index

    def request_repick(self, guest):
        """Ask the television to put the player picker back up.

        Worth having because a seat the running game never bound cannot be
        taken from here at all: which ports exist is settled when a game
        starts, so somebody arriving in the middle of one had no way in short
        of the host stopping it by hand. The picker knows how to close the
        game, hold on to it, and put it back where it was.
        """
        if not launcher.running():
            raise ValueError("no game is running")
        os.makedirs(os.path.dirname(REPICK_PATH), exist_ok=True)
        with open(REPICK_PATH, "w") as handle:
            handle.write(guest.label + "\n")
        log.info("%s asked for the player picker", guest.label)
        return True

    def pad_state(self):
        """Who is on which pad, and which player each pad actually is.

        The player number comes from the game that is running, not from the
        pad's position: the picker decides which pad is which port and writes
        it into the config it hands RetroArch. A pad bound to nothing is not a
        seat anybody can take, and saying so beats offering it.
        """
        ports = {}
        try:
            ports = launcher.player_ports()
        except Exception:
            pass
        names = list(self.pads.names) if self.pads is not None else []
        try:
            playing = bool(launcher.running())
        except Exception:
            playing = False
        # The row for whatever is on now, or was on last. Both come from the
        # same place, which is the point: "what am I ending" and "what would
        # I continue" are the same question asked at different moments.
        try:
            row = self.playing_now()
        except Exception:
            row = None
        return {
            "count": len(names),
            # Whether a game is running at all, which is not the same as
            # knowing which pad is which player. Told apart because saying
            # "no game is running" while one plainly is sends somebody looking
            # in the wrong place entirely.
            "playing": playing,
            # Everybody on each pad, not just whoever was looked at last: with
            # sharing on there can be several, and a list that silently kept
            # one name would make a shared controller look unoccupied.
            "who": {index: ", ".join(names)
                    for index, names in self._who_by_pad().items()},
            # index -> player number in the game, or absent when that pad is
            # not bound to a port at all.
            "ports": {str(i): ports[name]
                      for i, name in enumerate(names) if name in ports},
            # What is on the television, by name. Worth saying next to the
            # buttons that end it: "End game" over an unnamed screen asks
            # somebody to remember what they are about to stop.
            "game": (row or {}).get("label", "") if playing else "",
            # And, when nothing is on, what was on last -- so the tab that is
            # otherwise an apology can offer to put it back.
            "last": ({"id": row["id"], "label": row["label"]}
                     if row and not playing else None),
        }

    def people(self):
        """Who is in the room, for the guests' own list of each other.

        Deliberately narrower than roster(). That one is for the person who
        owns the television and may say anything; this goes to everybody, so
        it carries names, seats and how well each connection is running, and
        nothing that says which machine or which network anybody is on.

        The pad name is worth having rather than just the number: "Fourth
        Player 2" is what the guest sees written on the seat picker, and a
        list that called it something else would be a list nobody could match
        up with the thing in front of them.
        """
        ports = {}
        try:
            ports = launcher.player_ports()
        except Exception:
            pass
        names = list(self.pads.names) if self.pads is not None else []
        rows = []
        for g in sorted(self.guests.values(), key=lambda g: g.slot):
            pad_name = (names[g.pad_index]
                        if 0 <= g.pad_index < len(names) else "")
            health = g.health or {}
            rows.append({
                "slot": g.slot,
                "name": g.label,
                "pad": g.pad_index,
                "pad_name": pad_name,
                # Which player the running game thinks they are, which is not
                # their seat number and is absent when no game has bound it.
                "player": ports.get(pad_name),
                "here": g.has_media(),
                "input_only": bool(g.input_only),
                "seconds": round(time.monotonic() - g.joined_at),
                "driving": g.slot == self.driver,
                "rtt": health.get("rtt"),
                "loss": health.get("loss"),
                "fps": health.get("fps"),
                "frames": g.frames,
                # Frames that arrived while the television was in a menu and
                # so went nowhere. "My controller did nothing" is a question
                # somebody asks, and this is the answer to it.
                "held": g.held_frames,
            })
        return rows

    def set_health(self, guest, message):
        """Take a guest's word for how their own connection is doing.

        Clamped rather than trusted: these numbers are drawn on other people's
        screens, and a guest is free to send anything at all.
        """
        def number(key, low, high):
            try:
                value = float(message.get(key))
            except (TypeError, ValueError):
                return None
            if value != value:                       # NaN
                return None
            return round(max(low, min(high, value)), 1)

        guest.health = {"rtt": number("rtt", 0, 9999),
                        "loss": number("loss", 0, 100),
                        "fps": number("fps", 0, 240)}

    def publish_people(self):
        self.notify({"t": "people", "people": self.people()})

    def roster(self):
        rows = []
        for g in sorted(self.guests.values(), key=lambda g: g.slot):
            sent = g.peer.sent if g.peer is not None else {}
            rows.append({
                "slot": g.slot,
                "label": g.label,
                "connected": g.has_media(),
                "seconds": round(time.monotonic() - g.joined_at),
                "frames": g.frames,
                "pad": g.pad.path,
                "pad_index": g.pad_index,
                "video_kb": round(sent.get("video_bytes", 0) / 1024),
                "audio_kb": round(sent.get("audio_bytes", 0) / 1024),
                "video_packets": sent.get("video_packets", 0),
            })
        return rows

    # -- the sweep ----------------------------------------------------------

    def _reap_ghosts(self, seconds=GHOST_SECONDS):
        """Free slots held by guests who have no media and are not coming back.

        Three slots fill up fast when nothing ever releases them. A guest whose
        connection failed -- or whose attach timed out, or who closed the tab
        while their peer was already gone -- was keeping their slot for the
        whole session, so a couple of failed attempts left the session
        permanently full and everybody afterwards locked out of a session that
        looked open.
        """
        now = self._now()
        for slot, guest in list(self.guests.items()):
            if guest.has_media(now):
                # The only place this is refreshed. Doing it when a connection
                # is *attempted* made a guest who could never connect immortal:
                # every retry pushed their deadline back, so the slot was held
                # by somebody who had no picture and never would.
                guest.media_since = now
                continue
            # Their socket has gone as well, so they are not mid-reconnect --
            # they closed the tab or walked out of range.
            limit = seconds if guest.socket is not None else min(seconds, LEFT_SECONDS)
            if now - guest.media_since > limit:
                log.info("%s had no video for %.0fs; freeing the slot",
                         guest.label, now - guest.media_since)
                self.drop(slot, reason="left")

    def reap_now(self, seconds=5.0):
        """Free slots whose connection has been dead for a few seconds.

        The impatient version of the sweep, for the moment a guest is being
        refused. The ordinary grace period exists so a reconnect keeps its
        slot; somebody actively trying to get in is better served by the slot.
        """
        before = len(self.guests)
        self._reap_ghosts(seconds=seconds)
        return before - len(self.guests)

    # -- starting a game ----------------------------------------------------

    def launch_state(self):
        """What the guest's page needs to know to offer the game list."""
        pending = None
        if self.pending:
            pending = {"label": self.pending["label"],
                       "who": self.pending["who"],
                       "seconds": max(0, round(self.pending["deadline"] - self._now()))}
        return {"policy": self.launch_policy, "pending": pending}

    def set_policy(self, policy):
        policy = (policy or "off").lower()
        if policy not in LAUNCH_POLICIES:
            raise ValueError("unknown launch policy %r" % policy)
        self.launch_policy = policy
        if policy == "off":
            self.deny_launch("the owner turned off starting games")
        log.info("guests may start games: %s", policy)
        self.notify({"t": "launchpolicy", **self.launch_state()})
        return policy

    async def request_launch(self, guest, game_id, resume=False):
        """A guest has asked for a game. Returns what to tell them."""
        if self.launch_policy == "off":
            return {"ok": False, "error": "The owner has not turned on starting "
                                          "games from here."}
        row = self.catalogue.find(game_id)
        if row is None:
            # Either a stale page or somebody inventing ids. Same answer.
            return {"ok": False, "error": "That game is not on this box."}
        problem = await self.loop.run_in_executor(None, launcher.preflight, row)
        if problem:
            return {"ok": False, "error": problem}

        busy = await self.loop.run_in_executor(None, launcher.running)
        if self.launch_policy == "idle" and busy:
            return {"ok": False,
                    "error": "Something is already playing. This can start "
                             "once the screen is free."}

        if self.launch_policy == "approve":
            if self.pending:
                return {"ok": False,
                        "error": "Someone else has just asked. Wait for that "
                                 "to be answered."}
            self.pending = {
                "id": row["id"],
                "resume": bool(resume),
                "label": row["label"],
                "short": row["short"],
                "who": guest.label if guest is not None else "someone",
            "how": "continuing a save" if resume else "from the start",
                "slot": guest.slot if guest is not None else None,
                "deadline": self._now() + APPROVAL_SECONDS,
            }
            log.info("%s asked to start %s; waiting for the owner",
                     self.pending["who"], row["label"])
            # Everyone sees the ask, so a second person does not sit wondering
            # why the list stopped responding.
            self.notify({"t": "launchpolicy", **self.launch_state()})
            return {"ok": True, "state": "pending",
                    "seconds": round(APPROVAL_SECONDS), "label": row["label"]}

        return await self._start_game(row, busy, resume)

    # Where kodi-retrobox records what it last put on the television. Read
    # rather than asked for, the same coupling through data the catalogue and
    # the pad names already use: that project needs no knowledge of this one.
    LAST_GAME = os.path.expanduser("~/.local/state/retroarch/last-game.json")

    def playing_now(self):
        """The catalogue row for whatever is on the television, or None.

        Two ways of knowing, because there are two ways a game gets started.
        One this session started is remembered outright. One somebody put on
        from the television is found by the path kodi-retrobox writes down --
        without which "restart" would only ever work for games started from a
        phone, which is not most of them.
        """
        # A Steam game first, and whoever started it: it is the one kind that
        # can be identified outright, by the appid Steam marks it with, rather
        # than inferred from what this server last did.
        try:
            appid = launcher.steam_game_now()
        except Exception:
            appid = None
        if appid:
            for row in self.catalogue.rows():
                if row.get("appid") == appid:
                    return row
            # Playing, and not on the guest list. Named honestly rather than
            # reported as nothing: somebody looking at the tab can still end
            # it, and "Steam game" is truer than silence.
            return {"id": "", "label": "a Steam game", "appid": appid,
                    "kind": "steam"}
        if self.last_started is not None:
            return self.last_started
        try:
            with open(self.LAST_GAME) as handle:
                rom = (json.load(handle) or {}).get("rom")
        except (OSError, ValueError):
            return None
        if not rom:
            return None
        for row in self.catalogue.rows():
            if row.get("path") == rom:
                return row
        return None

    async def request_restart(self, guest):
        """Start the game that is playing again, from the beginning.

        Gated as starting one is, and it is a start: what it replaces is the
        game somebody is in the middle of.
        """
        if self.launch_policy == "off":
            return {"ok": False, "error": "The owner has not turned on "
                                          "starting and stopping games from "
                                          "here."}
        playing = await self.loop.run_in_executor(None, launcher.running)
        if not playing:
            return {"ok": False, "error": "Nothing is playing."}
        row = self.playing_now()
        if row is None:
            return {"ok": False,
                    "error": "This cannot tell which game is on the "
                             "television, so it cannot start it again. Pick "
                             "it from the list instead."}
        # resume=False on purpose: "restart" is from the beginning. Ending the
        # game is the way to stop and keep your place.
        return await self.request_launch(guest, row["id"], resume=False)

    async def request_stop(self, guest):
        """A guest has asked to end the game. Returns what to tell them.

        Gated exactly as starting one is. Ending the game is not a smaller act
        than starting one -- somebody in the room is playing it -- so an owner
        who wants to be asked before a game starts is asked before one ends.

        The saving is the ordinary stop: RetroArch is configured to write its
        state on the way out and to load it again next time, and stop_running
        sends TERM and waits for exactly that reason. There is no separate
        save step to get wrong, and a game that ignores TERM long enough is
        killed by the same path that has always killed it.
        """
        if self.launch_policy == "off":
            return {"ok": False, "error": "The owner has not turned on "
                                          "starting and stopping games from "
                                          "here."}
        playing = await self.loop.run_in_executor(None, launcher.running)
        if not playing:
            return {"ok": False, "error": "Nothing is playing."}

        if self.launch_policy == "approve":
            if self.pending:
                return {"ok": False,
                        "error": "Someone else has just asked. Wait for that "
                                 "to be answered."}
            self.pending = {
                "kind": "stop",
                "id": "", "resume": False,
                "label": "ending the game",
                "short": "",
                "who": guest.label if guest is not None else "someone",
                "how": "saving first",
                "slot": guest.slot if guest is not None else None,
                "deadline": self._now() + APPROVAL_SECONDS,
            }
            log.info("%s asked to end the game; waiting for the owner",
                     self.pending["who"])
            self.notify({"t": "launchpolicy", **self.launch_state()})
            return {"ok": True, "state": "pending",
                    "seconds": round(APPROVAL_SECONDS),
                    "label": "ending the game"}

        return await self._stop_game()

    async def _stop_game(self):
        """Close what is playing, giving it time to write its save."""
        went = await self.loop.run_in_executor(None, launcher.stop_running)
        if not went:
            # It was killed rather than asked, which is the case where a save
            # can be lost. Said plainly rather than reported as a clean stop.
            self.notify({"t": "note",
                         "message": "The game would not close on its own and "
                                    "had to be stopped. Anything since its "
                                    "last save may not have been kept."})
        else:
            self.notify({"t": "note",
                         "message": "The game has been closed and the "
                                    "television is back at the menu."})
        return {"ok": True, "stopped": True, "clean": bool(went)}

    async def _start_game(self, row, busy=False, resume=False):
        # Steam and Moonlight first, and whether or not a game is playing.
        # Neither is a game -- `busy` is about games -- but both are in the
        # way of one: they hold the screen, a GPU context and the pad they
        # were given, and neither needs to be there. Kodi starts them when
        # somebody asks.
        #
        # Before the stop below rather than after, so the waits do not run end
        # to end while a guest watches a list that is not answering.
        stubborn = await self.loop.run_in_executor(None, launcher.clear_the_screen)
        if stubborn:
            names = " and ".join(stubborn)
            log.warning("%s would not close; starting %s anyway",
                        names, row["label"])
            # Said, not refused. A game over the top of something that will
            # not die is untidy; a guest told "no" by a machine that looks
            # idle to them is worse.
            self.notify({"t": "note",
                         "message": names + " would not close, so the game may "
                                    "start behind it."})
        if busy:
            # Only the open policy gets here with something already playing,
            # and taking over is what that policy is.
            log.info("stopping what is playing to start %s", row["label"])
            stopped = await self.loop.run_in_executor(None, launcher.stop_running)
            # Launching anyway left the guest with the worst of both: the game
            # they were playing had been told to quit, and the one they asked
            # for was refused. Say so instead, while the old one is still up.
            if not stopped:
                log.warning("%s is still running; not starting %s over it",
                            "what was playing", row["label"])
                return {"ok": False,
                        "error": "The game that is running would not close. "
                                 "Try again in a moment."}
        problem = await self.loop.run_in_executor(
            None, functools.partial(launcher.launch, row, resume=resume))
        # Remembered so "start it again" knows what "it" is, without having to
        # go and read what the television wrote down.
        self.last_started = row
        if problem:
            return {"ok": False, "error": problem}
        self.notify({"t": "starting", "label": row["label"],
                     "short": row["short"], "resume": bool(resume)})
        return {"ok": True, "state": "starting", "label": row["label"],
                "resume": bool(resume)}

    async def approve_launch(self):
        """The owner said yes. Returns what happened, for the control socket."""
        if not self.pending:
            return {"ok": False, "error": "nothing is waiting"}
        kind = self.pending.get("kind", "start")
        who, label = self.pending["who"], self.pending["label"]
        if kind == "stop":
            self.pending = None
            log.info("owner approved ending the game for %s", who)
            result = await self._stop_game()
            self.notify({"t": "launchpolicy", **self.launch_state()})
            return result
        row = self.catalogue.find(self.pending["id"])
        resume = self.pending.get("resume", False)
        self.pending = None
        if row is None:
            self.notify({"t": "launchdenied", "reason": "that game has gone"})
            return {"ok": False, "error": "that game is no longer in the list"}
        log.info("owner approved %s for %s", label, who)
        busy = await self.loop.run_in_executor(None, launcher.running)
        result = await self._start_game(row, busy, resume)
        self.notify({"t": "launchpolicy", **self.launch_state()})
        return result

    def deny_launch(self, reason="the owner said no"):
        if not self.pending:
            return {"ok": False, "error": "nothing is waiting"}
        label = self.pending["label"]
        self.pending = None
        log.info("launch of %s refused: %s", label, reason)
        self.notify({"t": "launchdenied", "reason": reason, "label": label})
        self.notify({"t": "launchpolicy", **self.launch_state()})
        return {"ok": True}

    # -- surviving a restart -------------------------------------------------

    def publish_pad_names(self):
        """Say which pad belongs to whom, or clear it when nobody is here."""
        names = {}
        if self.pads is not None:
            for guest in self.guests.values():
                if 0 <= guest.pad_index < len(self.pads):
                    names[self.pads.name_for(guest.pad_index)] = guest.label
        try:
            os.makedirs(os.path.dirname(PAD_NAMES_PATH), exist_ok=True)
            tmp = PAD_NAMES_PATH + ".new"
            with open(tmp, "w") as handle:
                json.dump(names, handle)
            os.replace(tmp, PAD_NAMES_PATH)   # never a half-written file
        except OSError as exc:
            log.debug("could not write the pad names: %s", exc)

    def save(self):
        """Write the invite down, so a crash costs a reconnect and not a session."""
        if self.invite is None:
            return
        # A snapshot that says no time is left is worse than no snapshot: it
        # replaces a good one, and the session that was open at the restart is
        # then declined on the way back in. This has been seen once -- a state
        # file written with expires_in 0.0 while the session still had three and
        # a half hours on it -- and never reproduced, over restarts with and
        # without a guest attached. Whatever produces it, refusing to write it
        # costs nothing: a session really out of time is closing anyway, and
        # `forget` is how a finished session clears its state on purpose.
        left = self.invite.remaining(self._now())
        if left <= 0:
            log.warning("declining to save a session with no time left "
                        "(the file on disk is left alone)")
            return
        try:
            os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
            tmp = STATE_PATH + ".new"
            with open(tmp, "w") as handle:
                json.dump(self.invite.snapshot(self._now()), handle)
            os.chmod(tmp, 0o600)
            os.replace(tmp, STATE_PATH)          # never a half-written file
        except OSError as exc:
            log.warning("could not save the session: %s", exc)

    @staticmethod
    def forget():
        try:
            os.unlink(STATE_PATH)
        except OSError:
            pass

    @staticmethod
    def saved_invite(now, fixed_pin=""):
        """The invite from a previous run, if it is still in date."""
        try:
            with open(STATE_PATH) as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        try:
            invite = invites.Session.restore(data, now)
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("the saved session could not be read: %s", exc)
            return None
        if invite is not None:
            # The config is where a set PIN lives; the snapshot deliberately
            # holds only digests. Without this a re-share after a restart would
            # hand out a random PIN and quietly undo the owner's choice.
            invite.adopt_fixed_pin(fixed_pin)
        if invite is None:
            # Said out loud, because the alternative -- what happened here for
            # weeks -- is a session that silently does not come back and no
            # record anywhere of what the file said.
            log.info("the saved session had run out: %.0fs left when it was "
                     "written, %.0fs ago",
                     float(data.get("expires_in", 0)),
                     max(0.0, time.time() - float(data.get("saved_at", 0))))
        return invite

    def _watch_the_screen(self):
        """Whether a guest's frames should be reaching the machine right now.

        Read on a timer rather than per frame: frames arrive 125 times a
        second per guest and this asks X two questions, which is not a thing
        to do eight thousand times a minute for an answer that changes when
        somebody walks across a room.
        """
        if not self.cfg.guest_input_needs_a_game:
            return False, ""
        shells = tuple(self.cfg.shell_windows) or screen.SHELLS
        front = screen.foreground()
        if not screen.is_shell(front, shells):
            return False, ""
        # Named in the message, because "controls paused" without a reason is
        # indistinguishable from a fault, and the guest can see the screen.
        for shell in shells:
            if shell in front:
                return True, shell
        return True, "the desktop"

    def say(self, who, text, slot=None):
        """Put one line in front of everybody. Returns the message, or None.

        `who` is a label rather than a guest, because the television speaks
        here too and it is not sitting in a slot.

        Everything a browser sends is suspect: this is the one place in the
        program where a guest's own words reach other people's screens, so the
        text is cut to length, stripped of the control characters that make a
        line lie about how long it is, and rate limited per sender. It is not
        escaped here -- the page and the overlay each escape for their own
        medium, which is where escaping belongs.
        """
        text = " ".join(str(text or "").split())[:CHAT_LIMIT]
        if not text:
            return None
        now = self._now()
        if slot is not None:
            last = self._chat_at.get(slot, 0.0)
            if now - last < CHAT_GAP:
                return None
            self._chat_at[slot] = now
        self._chat_id += 1
        message = {"id": self._chat_id, "from": who, "text": text,
                   "at": time.time(), "slot": slot}
        self.chat.append(message)
        del self.chat[:-CHAT_KEEP]
        self.notify({"t": "chat", **message})
        log.info("chat: %s: %s", who, text)
        return message

    def recent_chat(self, since=0):
        """Messages after `since`, for a page that has just joined or a
        television that polls."""
        return [m for m in self.chat if m["id"] > since]

    def name_a_driver(self, slot):
        """Let one guest drive what is in front, or nobody. Returns the label.

        Only from the television: this arrives on the control socket, which is
        the host's own machine, and never from the web UI. A guest cannot give
        it to themselves, which is the whole point of it existing.
        """
        if slot is None:
            was, self.driver, self.driver_shell = self.driver, None, ""
            if was is not None:
                log.info("nobody is driving %s now", self.hold_reason or "the screen")
            self._tell_about_the_hold()
            return None
        guest = next((g for g in self.guests.values() if g.slot == slot), None)
        if guest is None:
            raise ValueError("nobody is in that seat")
        self.driver = slot
        # What it is granted against, so it cannot outlive it. Held means
        # something is in front; not held means the permission is moot for as
        # long as that lasts, and it is scoped to whatever comes next.
        self.driver_shell = self.hold_reason
        log.info("%s may drive %s", guest.label, self.driver_shell or "the screen")
        self._tell_about_the_hold()
        return guest.label

    def forget_driver_if(self, gone_slot):
        """Take it back when that guest leaves. Nobody inherits it."""
        if self.driver is not None and self.driver == gone_slot:
            log.info("the guest who was driving has gone; nobody is now")
            self.driver = None
            self.driver_shell = ""

    def hold_state(self, guest):
        """Where one guest stands on the hold, right now.

        The same answer _tell_about_the_hold sends, for a guest who was not
        here when it last changed. It changes when it changes and not
        otherwise, which is right for a broadcast and leaves anybody who
        rejoins afterwards holding whatever they last heard -- a page that
        came back to a game in progress could still be showing "Controls
        paused" from before it went away.
        """
        driving = next((g.label for g in self.guests.values()
                        if g.slot == self.driver), "")
        return {"held": self.input_held, "why": self.hold_reason,
                "driving": guest.slot == self.driver,
                "driver_label": "" if guest.slot == self.driver else driving}

    def _tell_about_the_hold(self):
        """Tell every page where it stands, one page at a time.

        Per guest rather than broadcast, because "are you the one driving" is
        a different answer for each of them and it is not the page's job to
        work that out from a slot number: which slot a browser holds is this
        program's business, it changes, and a page that guessed wrong would
        either pause the driver or let everybody through.
        """
        for guest in list(self.guests.values()):
            self.notify_one(guest, {"t": "hold", **self.hold_state(guest)})

    def _hold_input(self, held, why=""):
        """Start or stop withholding guest frames, and say so once."""
        if held == self.input_held and why == self.hold_reason:
            return
        # A permission given while Moonlight was in front is not a permission
        # for whatever replaced it. Closing one program and opening another is
        # not something anybody would think of as revoking a permission, which
        # is exactly why this has to do it for them.
        if self.driver is not None and why != self.driver_shell:
            log.info("what is in front changed from %s to %s; nobody is "
                     "driving now", self.driver_shell or "nothing",
                     why or "nothing")
            self.driver = None
            self.driver_shell = ""
        if held == self.input_held:
            self.hold_reason = why
            return
        self.input_held = held
        self.hold_reason = why
        if held:
            # Let go of everything on the way in. A button held at the moment
            # the menu came up would otherwise stay held on the television for
            # as long as the menu is there, which is the one state that does
            # not correct itself.
            # Let go of the buttons, not of the device. Unplugging a pad
            # would take its port binding with it -- RetroArch settles which
            # device is which player when a game starts and does not revisit
            # it -- so a guest would come back from the menu as nobody.
            if self.pads is not None:
                for _index, pad in self.pads.live():
                    pad.release_all()
            log.info("guest controllers held: %s is in front", why or "a menu")
        else:
            log.info("guest controllers live again")
        self._tell_about_the_hold()

    def notify_one(self, guest, message):
        """One guest, for the things whose answer differs per page.

        "May you drive" is the first of those. Falls back to telling everybody
        if the server has not given us a way to reach one guest -- a message
        that reaches too many people is a poor answer, and a message that
        reaches nobody because an attribute was missing is a worse one.
        """
        if self.on_notice_one is None:
            return self.notify(message)
        try:
            self.on_notice_one(guest, message)
        except Exception:
            log.exception("could not deliver a notice to %s", guest.label)

    def notify(self, message):
        if self.on_notice is not None:
            try:
                self.on_notice(message)
            except Exception:
                log.exception("could not deliver a notice to guests")

    async def _sweep_forever(self):
        """Release quiet pads, warn about the deadline, and close on it."""
        try:
            while True:
                await asyncio.sleep(SWEEP_INTERVAL)
                if self.pads:
                    for pad in self.pads.sweep():
                        log.debug("dead-man: released %s", pad.name)
                self._ticks += 1
                # Every shell_poll_ms, ask what is in front. The sweeper runs
                # far more often than that; this rides on it rather than
                # bringing a second timer of its own.
                every = max(1, int(self.cfg.shell_poll_ms / (SWEEP_INTERVAL * 1000)))
                if self._ticks % every == 0:
                    # Guarded, because this loop is also the dead-man switch,
                    # the launch-request deadline and the pad sweep. A fault
                    # in the newest thing on it took all of those with it: a
                    # missing method here ended the task, and what somebody
                    # actually noticed was that a controller stopped being
                    # released when its guest went quiet.
                    try:
                        held, why = await self.loop.run_in_executor(
                            None, self._watch_the_screen)
                        self._hold_input(held, why)
                    except Exception:
                        log.exception("could not read what is on the screen")
                # Roughly every ten seconds, make sure the thread that owns the
                # pipeline is still answering. A wedge is otherwise invisible
                # until somebody tries to join and is refused.
                if self._ticks % 200 == 0 and self.stage is not None:
                    if not await self.loop.run_in_executor(
                            None, self.stage.worker_alive):
                        self.stage.reset_worker("it stopped answering a health check")
                if not self.invite:
                    return
                # An unanswered ask is a refusal. Silence must not leave a
                # guest staring at a countdown that already ran out.
                if self.pending and self._now() >= self.pending["deadline"]:
                    self.deny_launch("nobody answered")
                self._reap_ghosts()
                left = self.remaining()
                for threshold in WARN_AT:
                    if left <= threshold and threshold not in self._warned:
                        self._warned.add(threshold)
                        log.info("session ends in %.0f seconds", left)
                        self.notify({"t": "ending", "remaining": round(left)})
                if not self.invite.alive(self._now()):
                    log.info("session reached its deadline")
                    await self.astop(reason="expired")
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("the sweeper died -- pads may stick")
            raise
