"""Who is allowed in, for how long, and how that is taken back.

An invite is designed to leave the machine. It gets forwarded, screenshotted,
pasted into a group chat and photographed off a television, so it is never
treated as a credential on its own. Two factors travel by different routes: a
256-bit token in the link, and a six-digit PIN shown beside it.

Splitting them is not about defeating someone holding a photograph you chose to
send. It is about the half that outlives the session -- the link, which sits in
chat history and browser autocomplete long after the game ends. The PIN never
goes anywhere but the screen.

Only digests are stored. The clear token and PIN live in memory so the owner
can reopen the panel and read what they already sent; a restart forgets them
while the invite itself stays valid, and the panel then says to re-share.

Everything here takes `now` as an argument. There is no call to the clock in
this module, which is what lets the whole expiry and lockout story be tested in
milliseconds instead of hours.
"""

import base64
import hashlib
import hmac
import math
import secrets
import time
from dataclasses import dataclass, field

TOKEN_BYTES = 32                 # 256 bits
PIN_DIGITS = 6

# Ten wrong PINs destroy the invite outright. A leaked link then costs a
# re-share and nothing more, which is a far better failure than an attacker
# with unlimited guesses at a six-digit number.
MAX_PIN_ATTEMPTS = 10

# How long a guest who has left may come back on their own token alone. Their
# slot is given away immediately -- somebody present beats somebody who might
# return -- but the claim outlives it, so a guest whose connection dropped gets
# straight back in without hunting for the PIN, which by then is on a
# television in another house.
CLAIM_SECONDS = 900.0

# Per-address lockout, escalating. Three strikes because typing a six-digit
# number off a photograph goes wrong honestly about that often.
LOCKOUT_AFTER = 3
LOCKOUT_STEPS = (30.0, 120.0, 600.0)


class JoinError(Exception):
    """Base for every refusal. The client is told which, the log is told why."""


class InviteExpired(JoinError): pass
class InviteDestroyed(JoinError): pass
class BadPin(JoinError): pass
class LockedOut(JoinError):
    def __init__(self, seconds):
        super().__init__(f"locked out for {seconds:.0f}s")
        self.seconds = seconds
class SessionFull(JoinError): pass
class UnknownGuest(JoinError): pass


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def _matches(clear: str, stored: bytes) -> bool:
    return hmac.compare_digest(_digest(clear), stored)


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


# A PIN the owner sets is reused for every session rather than thrown away with
# the old one, so it is exposed for longer and to more people. Four digits is
# 10,000 guesses, which the lockout makes slow rather than impossible, so a
# longer one can be chosen.
MIN_FIXED_PIN = 4
MAX_FIXED_PIN = 12


def new_pin() -> str:
    return f"{secrets.randbelow(10 ** PIN_DIGITS):0{PIN_DIGITS}d}"


def check_fixed_pin(pin):
    """Why this cannot be used as a set PIN, or None.

    Refusing where it is set beats discovering it where somebody cannot join:
    a PIN nobody can type is a session nobody can enter, and by then the owner
    is standing in front of a television with guests waiting.
    """
    if not pin:
        return None
    pin = str(pin)
    if not pin.isdigit():
        return "A set PIN must be digits only, so it can be typed on a phone."
    if not MIN_FIXED_PIN <= len(pin) <= MAX_FIXED_PIN:
        return ("A set PIN must be between %d and %d digits."
                % (MIN_FIXED_PIN, MAX_FIXED_PIN))
    if len(set(pin)) == 1:
        return "A PIN of one repeated digit is guessed on the first try."
    return None


@dataclass
class RateLimiter:
    """Escalating lockout per caller. Failures are what count, not requests."""

    failures: dict = field(default_factory=dict)
    locked_until: dict = field(default_factory=dict)
    strikes: dict = field(default_factory=dict)

    def check(self, who, now):
        until = self.locked_until.get(who, 0.0)
        if now < until:
            raise LockedOut(until - now)

    def record_failure(self, who, now):
        count = self.failures.get(who, 0) + 1
        self.failures[who] = count
        if count < LOCKOUT_AFTER:
            return 0.0
        self.failures[who] = 0
        step = min(self.strikes.get(who, 0), len(LOCKOUT_STEPS) - 1)
        self.strikes[who] = step + 1
        duration = LOCKOUT_STEPS[step]
        self.locked_until[who] = now + duration
        return duration

    def record_success(self, who):
        # A correct PIN clears the near-miss count but not the strike history:
        # someone who has already been locked out twice does not get their
        # generous first tier back by guessing right once.
        self.failures.pop(who, None)


@dataclass
class Guest:
    slot: int
    token_digest: bytes
    joined_at: float
    label: str = ""
    address: str = ""


class Session:
    """One open session: an invite, a set of slots, and a deadline."""

    def __init__(self, slots, duration, now, label="Fourth Player", pin=None):
        if slots < 1:
            raise ValueError("a session with no slots admits nobody")
        problem = check_fixed_pin(pin)
        if problem:
            raise ValueError(problem)
        self.slots = slots
        self.label = label
        self.started_at = now
        self.expires_at = now + duration
        self.destroyed = False

        self._token = new_token()
        # A set PIN is the same one every session -- that is the point of it,
        # so the owner stops reading a new number off the television each
        # time. The token is still fresh, so the link remains good for this
        # session only even when the PIN is not.
        self.fixed_pin = str(pin) if pin else ""
        self._pin = self.fixed_pin or new_pin()
        self.token_digest = _digest(self._token)
        self.pin_digest = _digest(self._pin)

        self.pin_attempts = 0
        self.limiter = RateLimiter()
        self.guests = {}             # slot -> Guest
        self._burned = set()         # digests of kicked guests, never readmitted
        self._claims = {}            # digest -> (slot, when they left)

    # -- what the owner may read -------------------------------------------

    def adopt_fixed_pin(self, pin):
        """Tell a rebuilt invite which PIN the owner had set.

        Called after restore(), where the clear pair is gone by design. If it
        is still the PIN this invite was built with, the digests agree and it
        can be shown again; if the owner changed it while the service was down,
        they disagree and the stored one stays in force until the next
        re-share, rather than locking out everybody holding the old link.
        """
        if not pin:
            return False
        self.fixed_pin = str(pin)
        if _matches(self.fixed_pin, self.pin_digest):
            self._pin = self.fixed_pin
            return True
        return False

    def set_pin(self, pin):
        """Change the PIN of a session that is already open.

        Setting one and being told it applies to some future session is not
        what somebody asks for when guests are waiting. People already playing
        are unaffected -- they hold their own guest tokens and never type the
        PIN again -- so this only changes what the next person has to type.
        The failed-attempt count resets with it: it counted guesses at a PIN
        that no longer exists.
        """
        problem = check_fixed_pin(pin)
        if problem:
            raise ValueError(problem)
        self.fixed_pin = str(pin) if pin else ""
        self._pin = self.fixed_pin or new_pin()
        self.pin_digest = _digest(self._pin)
        self.pin_attempts = 0
        return self._pin

    @property
    def clear_invite(self):
        """The link token and PIN in the clear, or None after a restart."""
        if self._token is None:
            return None
        return self._token, self._pin

    @property
    def unlimited(self):
        """A session with no deadline at all.

        Held as an infinite expiry rather than a flag, so every comparison that
        asks whether this is still alive keeps working without knowing about
        it. What it must not do is reach a wire: JSON has no infinity, and
        `JSON.parse("Infinity")` is an error in a browser -- so the places that
        serialise a remaining time send null instead.
        """
        return math.isinf(self.expires_at)

    def remaining(self, now):
        return max(0.0, self.expires_at - now)

    def forget_clear(self):
        """Drop the readable copy without touching the invite's validity."""
        self._token = self._pin = None

    # -- lifecycle ----------------------------------------------------------

    def alive(self, now):
        return not self.destroyed and now < self.expires_at

    def check_alive(self, now):
        if self.destroyed:
            raise InviteDestroyed("this invite was cancelled")
        if now >= self.expires_at:
            raise InviteExpired("this invite has run out")

    def destroy(self):
        self.destroyed = True
        self.guests.clear()
        self.forget_clear()

    # -- joining ------------------------------------------------------------

    def token_valid(self, token, now):
        """Whether a link is live.

        Deliberately answerable without the PIN. Whoever holds a 256-bit token
        already knows it existed, so hiding its liveness only makes an expired
        link indistinguishable from a typo -- which helps nobody except an
        attacker who is not guessing tokens anyway.
        """
        return self.alive(now) and _matches(token, self.token_digest)

    def join(self, token, pin, now, address="", label="", require_token=True):
        """Spend the factors for a slot and a guest token.

        `require_token` False lets somebody in on the PIN alone, so a host can
        read out an address rather than send a link. The token is still checked
        when one is offered: a wrong link must never pass, or a stale one would
        silently work.

        The PIN alone is six digits against a lockout of three tries, then
        thirty seconds, then two minutes, then ten. That is about a hundred
        days of guessing per address for one session, which is why this is
        offered at all -- but it is one secret instead of two, and the caller
        decides.
        """
        self.check_alive(now)
        self.limiter.check(address, now)

        if (require_token or token) and not _matches(token, self.token_digest):
            # Counted against the address, which is what slows somebody down,
            # but not against the tally that destroys the invite outright.
            # That tally is there for a six-digit PIN. The link is 43
            # characters of random, where guessing is not the threat -- and it
            # can now be pasted in by hand, so a guest fumbling their own link
            # would otherwise be able to end the session for everybody in it.
            self._fail(address, now, counts=False)
            raise BadPin("that link is not for this session")
        if not _matches(pin, self.pin_digest):
            self._fail(address, now)
            raise BadPin("wrong PIN")

        slot = self.free_slot()
        if slot is None:
            raise SessionFull("every slot is taken")

        self.limiter.record_success(address)
        guest_token = new_token()
        self.guests[slot] = Guest(slot=slot, token_digest=_digest(guest_token),
                                  joined_at=now, label=label or f"Guest {slot + 1}",
                                  address=address)
        return slot, guest_token

    def _fail(self, address, now, counts=True):
        self.limiter.record_failure(address, now)
        if not counts:
            return
        self.pin_attempts += 1
        if self.pin_attempts >= MAX_PIN_ATTEMPTS:
            self.destroy()

    def free_slot(self):
        for i in range(self.slots):
            if i not in self.guests:
                return i
        return None

    # -- an established guest -----------------------------------------------

    def guest_for(self, guest_token, now):
        """Resolve a returning guest. Reconnecting must not need the PIN again."""
        self.check_alive(now)
        digest = _digest(guest_token)
        if any(hmac.compare_digest(digest, b) for b in self._burned):
            raise UnknownGuest("this guest was removed")
        for guest in self.guests.values():
            if hmac.compare_digest(digest, guest.token_digest):
                return guest
        raise UnknownGuest("not a guest of this session")

    def release(self, slot, now=None):
        """Give a slot back, but remember who had it.

        Distinct from `kick`, which is a refusal: this is for a guest who left
        or whose connection died, and who is welcome to come back. The slot is
        what has to return -- it is allocated here, not in the live session, and
        a live session that forgot a guest while this still remembered them
        reported empty slots and refused everybody who asked for one.

        The claim is kept so they can walk back in on their token alone. The
        slot is not held for them: somebody present beats somebody who might
        return, and if the room has filled by then they are told so honestly.
        """
        guest = self.guests.pop(slot, None)
        if guest is None:
            return False
        if now is not None:
            self._claims[guest.token_digest] = (slot, now)
        return True

    def reshare(self):
        """Mint a fresh link and PIN without disturbing the session.

        Needed after a restart, when the clear pair is gone by design and the
        owner has no way to read it back. Everyone already playing is
        untouched: their slots and their claims are identified by their own
        guest tokens, not by the invite's.

        The old link stops working, which is the point -- it is the same
        promise a re-share has always made.
        """
        self._token = new_token()
        # The link changes, the set PIN does not: re-sharing is for handing the
        # link to somebody new, and a PIN the owner chose is theirs to change,
        # not something a re-share should quietly replace.
        self._pin = getattr(self, "fixed_pin", "") or new_pin()
        self.token_digest = _digest(self._token)
        self.pin_digest = _digest(self._pin)
        self.pin_attempts = 0
        self.limiter = RateLimiter()
        return self._token, self._pin

    def reclaim(self, guest_token, now):
        """Let a guest who left back in on their token, without the PIN.

        This is what makes a dropped connection recoverable in a second rather
        than a scramble: the browser still holds the token, so a network switch
        or a closed tab costs nothing. It is not a way past the PIN for anyone
        else -- the token was minted here, is stored only as a digest, and a
        kicked guest's is burned.
        """
        self.check_alive(now)
        digest = _digest(guest_token)
        if any(hmac.compare_digest(digest, b) for b in self._burned):
            raise UnknownGuest("this guest was removed")

        claim = self._claims.get(digest)
        if claim is None or now - claim[1] > CLAIM_SECONDS:
            self._claims.pop(digest, None)
            raise UnknownGuest("nothing to reclaim")

        wanted = claim[0]
        slot = wanted if wanted not in self.guests else self.free_slot()
        if slot is None:
            raise SessionFull("every slot is taken")

        self._claims.pop(digest, None)
        # The same digest, so the token they are holding keeps working if they
        # drop again -- which is the whole point.
        self.guests[slot] = Guest(slot=slot, token_digest=digest, joined_at=now,
                                  label=f"Guest {slot + 1}")
        return slot

    # -- surviving a restart -------------------------------------------------

    def snapshot(self, now):
        """Everything needed to keep this invite working across a restart.

        Digests only, exactly as in memory: a stolen copy of this yields no
        usable link and no PIN. What it does preserve is that the link and PIN
        already in somebody's hands keep working, and that a guest can reclaim
        their slot -- which is the difference between a crash being invisible
        and everybody being locked out of a session that no longer exists.

        Deadlines are written as wall-clock seconds because the monotonic clock
        this runs on does not survive the process, let alone a reboot.
        """
        b64 = lambda raw: base64.b64encode(raw).decode()
        return {
            "slots": self.slots,
            "label": self.label,
            "token": b64(self.token_digest),
            "pin": b64(self.pin_digest),
            "expires_in": None if self.unlimited else max(0.0, self.expires_at - now),
            "saved_at": time.time(),
            "pin_attempts": self.pin_attempts,
            "burned": [b64(d) for d in self._burned],
            "claims": {b64(d): [slot, max(0.0, now - when)]
                       for d, (slot, when) in self._claims.items()},
            # Everybody currently in the session counts as a claimant too. They
            # never got the chance to leave -- that is what a crash is -- and
            # without this the guests who were actually playing are the only
            # ones who cannot get back in, which is precisely backwards.
            "playing": {b64(g.token_digest): [slot, 0.0]
                        for slot, g in self.guests.items()},
        }

    @classmethod
    def restore(cls, data, now):
        """Rebuild an invite from a snapshot, or None if it has run out."""
        elapsed = max(0.0, time.time() - float(data.get("saved_at", 0)))
        written = data.get("expires_in", 0)
        if written is None:
            remaining = math.inf              # a session with no deadline
        else:
            remaining = float(written) - elapsed
            if remaining <= 0:
                return None

        raw = lambda text: base64.b64decode(text)
        invite = cls.__new__(cls)
        invite.slots = int(data["slots"])
        invite.label = data.get("label", "Fourth Player")
        invite.started_at = now
        invite.expires_at = now + remaining
        invite.destroyed = False
        # The clear pair is deliberately not written down, so it cannot come
        # back. The invite still works for anybody already holding it; the
        # owner is told to re-share if they want to read it again.
        invite._token = invite._pin = None
        # Whether a PIN was set is not written down here -- the setting lives
        # in the config, which is the thing the owner edits. It is filled in
        # from there by adopt_fixed_pin() once this is rebuilt; without it a
        # re-share after a restart would quietly hand out a random PIN and
        # undo a choice the owner made.
        invite.fixed_pin = ""
        invite.token_digest = raw(data["token"])
        invite.pin_digest = raw(data["pin"])
        invite.pin_attempts = int(data.get("pin_attempts", 0))
        invite.limiter = RateLimiter()
        invite.guests = {}
        invite._burned = {raw(d) for d in data.get("burned", [])}
        claims = dict(data.get("claims") or {})
        claims.update(data.get("playing") or {})
        invite._claims = {raw(d): (int(slot), now - float(ago))
                          for d, (slot, ago) in claims.items()}
        return invite

    def kick(self, slot):
        """Remove one guest and make sure they cannot walk back in.

        Burning the digest matters: without it a kicked guest still holds a
        valid link and PIN, and the freed slot is the one they would take.
        """
        guest = self.guests.pop(slot, None)
        if guest is None:
            return False
        self._burned.add(guest.token_digest)
        return True
