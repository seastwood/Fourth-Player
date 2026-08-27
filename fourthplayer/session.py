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
import logging
import os
import subprocess
import sys
import time

from . import gpu, invites, pads as padlib, protocol, retroarch
from .video import Stage

log = logging.getLogger("fourthplayer.session")

SWEEP_INTERVAL = 0.05

# How long to wait for the pipeline worker before giving up on a guest's
# request. Every add and remove of a peer goes through one thread so they
# cannot race; the cost is that one slow teardown delays everybody behind it.
# Tearing down a peer whose network vanished is exactly that slow case, and a
# guest reconnecting is exactly who is behind it -- so the wait is bounded and
# the guest is told, rather than left watching "rejoining" forever.
PIPELINE_TIMEOUT = 12.0

# How long a guest may hold a slot with no media connection. Long enough to
# ride out a reconnect or a network switch, short enough that a session with
# three slots is not permanently full of people who left. A guest reaped this
# way can walk straight back in with the PIN.
GHOST_SECONDS = 75.0

# Guests are told the session is running out at these many seconds remaining,
# so the end is something you can see coming rather than something that happens
# to you mid-game.
WARN_AT = (300, 120, 30)


class GuestConnection:
    """One browser: a slot, a pad, a WebRTC peer and a socket."""

    def __init__(self, session, slot, socket):
        self.session = session
        self.slot = slot
        self.socket = socket
        # When they last had a working media connection. A guest is only ever
        # reaped for having none, so this starts now rather than at zero.
        self.media_since = time.monotonic()
        self.outbox = None      # set by the server; None while signalling is down
        self.on_signal = None   # how to reach them, so a rebuild needs no help
        self.peer = None
        self.label = f"Player {slot + 2}"   # the local player is player 1
        self.joined_at = time.monotonic()
        self.frames = 0
        self.bad_frames = 0

    @property
    def pad(self):
        return self.session.pads[self.slot]

    def has_media(self):
        """Whether this guest currently has a connection that carries anything.

        Deliberately not "do they have a peer". A peer whose ICE has gone quiet
        is an object, not a route, and treating the two as the same is what let
        one guest who changed network hold a slot until the session ended.
        """
        return self.peer is not None and getattr(self.peer, "ice_ok", False)

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
        self.pad.apply(state)


class LiveSession:
    """Everything that exists only while the session is open."""

    def __init__(self, cfg, loop, now=time.monotonic):
        self.cfg = cfg
        self.loop = loop
        self._now = now
        self.invite = None
        self.stage = None
        self.pads = None
        self.guests = {}          # slot -> GuestConnection
        self._overlay = None
        self._previous_dpm = None
        self._warned = set()
        self.on_notice = None      # set by the server: broadcast to guests
        self._sweeper = None
        self.opened_at = None
        self._previous_dpm = None

    # -- lifecycle ----------------------------------------------------------

    @property
    def open(self):
        return self.invite is not None and self.invite.alive(self._now())

    def start(self, duration_seconds):
        if self.invite is not None:
            raise RuntimeError("a session is already open")
        now = self._now()
        self.invite = invites.Session(slots=self.cfg.slots,
                                      duration=duration_seconds, now=now)
        # Pads first, and before any guest can arrive. kodi-retrobox's player
        # picker enumerates evdev devices at launch, so a pad that appears
        # after RetroArch starts is a pad that game will never see.
        self.pads = padlib.PadSet(self.cfg.slots)
        # Tell RetroArch what these pads are before anything can read them,
        # or it guesses and the guest's A button ends up somewhere else.
        retroarch.write_profiles([pad.name for pad in self.pads])
        self.stage = Stage(self.cfg, self.loop)
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
        log.info("session open for %.0f minutes, %d slots, pads at %s",
                 duration_seconds / 60, self.cfg.slots,
                 ", ".join(p.path for p in self.pads))
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
                env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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

    def admit(self, token, pin, socket, address):
        """Spend the invite for a slot. Raises invites.JoinError on refusal."""
        slot, guest_token = self.invite.join(token, pin, now=self._now(),
                                             address=address)
        guest = GuestConnection(self, slot, socket)
        self.guests[slot] = guest
        log.info("%s joined from %s", guest.label, address or "unknown")
        return guest, guest_token

    async def renew(self, guest, on_signal):
        """Give a guest a fresh media connection without a fresh invite.

        What a phone needs when it moves between mobile data and wifi: every
        address it had is gone, so the old connection can only ever be
        declared dead. Re-offering is the whole recovery, and it costs the
        guest nothing -- they keep their slot, their pad and their session.
        """
        self.detach_peer(guest)
        return await self.attach_peer(guest, on_signal)

    def resume(self, guest_token, socket):
        """A guest whose socket dropped, coming back without the PIN.

        Their slot is held rather than reassigned: it is the same person, and
        making them re-enter a PIN off a television they may no longer be
        looking at is the kind of friction this project exists to remove.
        """
        record = self.invite.guest_for(guest_token, now=self._now())
        existing = self.guests.get(record.slot)
        if existing is not None:
            self.detach_peer(existing)
            existing.socket = socket
            return existing
        guest = GuestConnection(self, record.slot, socket)
        self.guests[record.slot] = guest
        return guest

    async def attach_peer(self, guest, on_signal):
        """Give a guest a peer, without blocking everybody else's video."""
        # A new peer means a new sender, whose sequence numbers start again at
        # zero. Without this the pad rejects everything they send as stale.
        guest.pad.adopt_new_sender()

        def configure(peer):
            peer.on_input = guest.feed
            peer.on_broken = lambda why: self._peer_broke(guest, peer, why)
            # The media connection dying is what ends a guest -- not their
            # signalling socket, which they only need to arrive and
            # renegotiate.
            peer.on_dead = lambda why: self._peer_died(guest, peer, why)

        peer = await asyncio.wait_for(
            self.loop.run_in_executor(
                self.stage.mutations,
                functools.partial(self.stage.add_peer,
                                  f"slot{guest.slot}", on_signal, configure)),
            timeout=PIPELINE_TIMEOUT)
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
        if guest.on_signal is None:
            self.drop(guest.slot, reason="its connection broke")
            return
        self.loop.create_task(self._rebuild(guest, why))

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
        guest.pad.release_all()
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
        """The socket went away. The slot stays theirs until kicked or expiry."""
        guest = self.guests.pop(slot, None)
        if guest is None:
            return False
        self.detach_peer(guest)
        log.info("%s %s", guest.label, reason)
        return True

    def kick(self, slot):
        """Remove a guest and make sure the link will not let them back."""
        self.drop(slot, reason="was removed")
        return self.invite.kick(slot)

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
            if guest.has_media():
                guest.media_since = now
                continue
            if now - guest.media_since > seconds:
                log.info("%s held a slot with no video for %.0fs; freeing it",
                         guest.label, now - guest.media_since)
                self.drop(slot, reason="gave up")

    def reap_now(self, seconds=10.0):
        """Free slots whose connection has been dead for a few seconds.

        The impatient version of the sweep, for the moment a guest is being
        refused. The ordinary grace period exists so a reconnect keeps its
        slot; somebody actively trying to get in is better served by the slot.
        """
        before = len(self.guests)
        self._reap_ghosts(seconds=seconds)
        return before - len(self.guests)

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
                if not self.invite:
                    return
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
