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
import os
import subprocess
import sys
import time

from . import gpu, invites, pads as padlib, protocol, retroarch
from .video import Stage

log = logging.getLogger("fourthplayer.session")

SWEEP_INTERVAL = 0.05

# Where a live session is written down so a restart does not end it. The
# process has segfaulted inside the GPU's video driver more than once, and
# systemd puts it straight back -- but everything about the session lived in
# memory, so every guest was locked out of something that no longer existed.
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
        self.last_input = 0.0
        self.frames = 0
        self.bad_frames = 0

    @property
    def pad(self):
        return self.session.pads[self.slot]

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
        self.last_input = time.monotonic()
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
        self._ticks = 0
        self.on_notice = None      # set by the server: broadcast to guests
        self._sweeper = None
        self.opened_at = None
        self._previous_dpm = None

    # -- lifecycle ----------------------------------------------------------

    @property
    def open(self):
        return self.invite is not None and self.invite.alive(self._now())

    def start(self, duration_seconds, invite=None):
        if self.invite is not None:
            raise RuntimeError("a session is already open")
        now = self._now()
        self.invite = invite or invites.Session(
            slots=self.cfg.slots, duration=duration_seconds, now=now)
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
        self.save()
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
        # Write it down now. The snapshot was only taken when a session opened
        # or somebody left, so a guest who joined and was still playing when
        # the process died was absent from it -- leaving the people actually
        # in the game as the only ones unable to get back in.
        self.save()
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
        guest = GuestConnection(self, record.slot, socket)
        self.guests[record.slot] = guest
        self.save()
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

        job = functools.partial(self.stage.add_peer,
                                f"slot{guest.slot}", on_signal, configure)
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
        """Let a guest go, and give the slot back to the invite.

        Both halves matter. Slots are allocated by the invite, not here, so a
        guest removed from this list while the invite still held their slot
        left a session that reported empty slots and refused everybody who
        asked for one -- which is exactly as confusing as it sounds.
        """
        guest = self.guests.pop(slot, None)
        if guest is None:
            return False
        self.detach_peer(guest)
        if self.invite is not None:
            self.invite.release(slot, now=self._now())
            self.save()
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

    # -- surviving a restart -------------------------------------------------

    def save(self):
        """Write the invite down, so a crash costs a reconnect and not a session."""
        if self.invite is None:
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
    def saved_invite(now):
        """The invite from a previous run, if it is still in date."""
        try:
            with open(STATE_PATH) as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        try:
            return invites.Session.restore(data, now)
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("the saved session could not be read: %s", exc)
            return None

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
                # Roughly every ten seconds, make sure the thread that owns the
                # pipeline is still answering. A wedge is otherwise invisible
                # until somebody tries to join and is refused.
                if self._ticks % 200 == 0 and self.stage is not None:
                    if not await self.loop.run_in_executor(
                            None, self.stage.worker_alive):
                        self.stage.reset_worker("it stopped answering a health check")
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
