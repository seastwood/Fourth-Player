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

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field

TOKEN_BYTES = 32                 # 256 bits
PIN_DIGITS = 6

# Ten wrong PINs destroy the invite outright. A leaked link then costs a
# re-share and nothing more, which is a far better failure than an attacker
# with unlimited guesses at a six-digit number.
MAX_PIN_ATTEMPTS = 10

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


def new_pin() -> str:
    return f"{secrets.randbelow(10 ** PIN_DIGITS):0{PIN_DIGITS}d}"


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

    def __init__(self, slots, duration, now, label="Fourth Player"):
        if slots < 1:
            raise ValueError("a session with no slots admits nobody")
        self.slots = slots
        self.label = label
        self.started_at = now
        self.expires_at = now + duration
        self.destroyed = False

        self._token = new_token()
        self._pin = new_pin()
        self.token_digest = _digest(self._token)
        self.pin_digest = _digest(self._pin)

        self.pin_attempts = 0
        self.limiter = RateLimiter()
        self.guests = {}             # slot -> Guest
        self._burned = set()         # digests of kicked guests, never readmitted

    # -- what the owner may read -------------------------------------------

    @property
    def clear_invite(self):
        """The link token and PIN in the clear, or None after a restart."""
        if self._token is None:
            return None
        return self._token, self._pin

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

    def join(self, token, pin, now, address="", label=""):
        """Spend the two factors for a slot and a guest token."""
        self.check_alive(now)
        self.limiter.check(address, now)

        if not _matches(token, self.token_digest):
            self._fail(address, now)
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
                                  joined_at=now, label=label or f"Player {slot + 1}",
                                  address=address)
        return slot, guest_token

    def _fail(self, address, now):
        self.pin_attempts += 1
        self.limiter.record_failure(address, now)
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

    def release(self, slot):
        """Give a slot back without burning the guest's credential.

        Distinct from `kick`, which is a refusal: this is for a guest who left
        or whose connection died, and who is welcome to come back. The slot is
        what has to return -- it is allocated here, not in the live session, and
        a live session that forgot a guest while this still remembered them
        reported empty slots and refused everybody who asked for one.
        """
        return self.guests.pop(slot, None) is not None

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
