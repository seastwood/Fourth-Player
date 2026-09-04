"""Named accounts, and what each of them is allowed to do.

These sit *inside* the invite. Nobody reaches a login without a working link
and PIN, so this is a second door rather than a second front door, and it
inherits the lockouts and the token the invite already enforces.

Two things here are worth reading before changing anything.

The first is that a password is not a token. Elsewhere in this program
`invites._digest` is a bare SHA-256, and that is correct where it is used: a
256-bit token and a six-digit PIN behind a three-strike lockout are not
attacked by hashing quickly. A password is exactly that, so passwords go
through scrypt, which is deliberately slow -- about 160 ms on the console this
was written for. Reusing _digest here would be the one mistake in this file
that mattered.

The second is that a one-time code has to be one-time. Verifying the maths and
stopping there leaves a code good for the whole thirty seconds to anybody who
repeats it, so the step it was issued for is written down and a code from that
step or earlier is refused afterwards.

What this file cannot do is as important as what it can: it never creates a
capability. It stores which of the capabilities the program already has have
been given to whom. See the design note.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import time

log = logging.getLogger("fourthplayer.accounts")

STORE = os.path.expanduser("~/.local/state/fourth-player/accounts.json")

# scrypt, at parameters that cost about 160 ms on the console this runs on.
# maxmem is passed explicitly and must be: 128 * n * r is exactly 32 MiB here,
# which is OpenSSL's default limit, and the call fails on the boundary.
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 64 * 1024 * 1024
SALT_BYTES = 16
KEY_BYTES = 32

# RFC 6238, as the authenticator apps implement it.
TOTP_STEP = 30
TOTP_DIGITS = 6
TOTP_SECRET_BYTES = 20          # 160 bits, which is what most apps expect
# One step either side, for a phone whose clock is a little out. Wider than
# this starts to matter: every extra step is another code an attacker who saw
# one may still use.
TOTP_DRIFT = 1

# How long a remembered device stays remembered. A fortnight is long enough
# that a phone used every weekend never asks twice, and short enough that a
# phone lent to somebody and forgotten about stops working by itself.
DEVICE_DAYS = 14
DEVICE_BYTES = 32                # 256 bits

# Everything an account can be given. Checked on the way in, so a typo in a
# console command is refused rather than stored and silently never matched.
# A per-game grant is written "steam:274190" and is checked separately.
CAPABILITIES = ("steam", "stop", "kick", "reshare", "grant")


class AccountError(Exception):
    """Something the person running the command needs to be told."""


# ---- the file -------------------------------------------------------------

def _read():
    try:
        with open(STORE, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {"accounts": []}
    except (OSError, ValueError) as exc:
        # A corrupt store is not an empty store. Refusing every login is the
        # safe answer; quietly starting again would delete the admin.
        raise AccountError("The accounts file could not be read: %s" % exc)
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), list):
        raise AccountError("The accounts file is not in the expected shape.")
    return data


def _write(data):
    """Save, atomically, and readable only by this user.

    Written to a temporary file in the same directory and renamed over the
    old one, so an interrupted write cannot leave a half-file where the
    accounts used to be. The mode is set before anything is written into it:
    the secrets in here are what make the second factor a second factor.
    """
    folder = os.path.dirname(STORE)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    temporary = STORE + ".new"
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as writing:
            json.dump(data, writing, indent=2, sort_keys=True)
            writing.write("\n")
            writing.flush()
            os.fsync(writing.fileno())
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    os.replace(temporary, STORE)
    os.chmod(STORE, 0o600)


# ---- passwords ------------------------------------------------------------

def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(SALT_BYTES)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N,
                         r=SCRYPT_R, p=SCRYPT_P, dklen=KEY_BYTES,
                         maxmem=SCRYPT_MAXMEM)
    return {"salt": base64.b64encode(salt).decode("ascii"),
            "key": base64.b64encode(key).decode("ascii"),
            "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P}


def check_password(password, stored):
    """Constant-time, and never raising on a store somebody has edited."""
    try:
        salt = base64.b64decode(stored["salt"])
        want = base64.b64decode(stored["key"])
        key = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                             n=int(stored.get("n", SCRYPT_N)),
                             r=int(stored.get("r", SCRYPT_R)),
                             p=int(stored.get("p", SCRYPT_P)),
                             dklen=len(want) or KEY_BYTES,
                             maxmem=SCRYPT_MAXMEM)
    except Exception:
        return False
    return hmac.compare_digest(key, want)


# ---- the authenticator ----------------------------------------------------

def new_secret():
    """A fresh shared secret, in the base32 an authenticator app expects."""
    return base64.b32encode(secrets.token_bytes(TOTP_SECRET_BYTES)).decode("ascii")


def code_at(secret, step):
    """The six digits for one thirty-second step. RFC 6238 with SHA-1.

    SHA-1 because that is what the apps do; the construction is HMAC, where
    SHA-1 is not the weakness, and an authenticator that cannot read the
    secret is no authenticator at all.
    """
    try:
        key = base64.b32decode(secret, casefold=True)
    except Exception:
        raise AccountError("That authenticator secret is not valid base32.")
    digest = hmac.new(key, struct.pack(">Q", int(step)), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    chunk = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(chunk % (10 ** TOTP_DIGITS)).zfill(TOTP_DIGITS)


def check_code(secret, given, now=None, after=-1):
    """The step a code belongs to, or None.

    `after` is the last step already used by this account: a code from that
    step or earlier is refused however good the arithmetic is, which is what
    stops one being replayed inside its own window.
    """
    given = "".join(ch for ch in (given or "") if ch.isdigit())
    if len(given) != TOTP_DIGITS:
        return None
    step_now = int((now if now is not None else time.time()) // TOTP_STEP)
    # Every candidate is checked even once one matches, so the time taken says
    # nothing about which step it was.
    found = None
    for step in range(step_now - TOTP_DRIFT, step_now + TOTP_DRIFT + 1):
        if step <= after:
            continue
        if hmac.compare_digest(code_at(secret, step), given) and found is None:
            found = step
    return found


def otpauth(name, secret, issuer="Fourth Player"):
    """The URI an authenticator app reads, by QR or by hand."""
    from urllib.parse import quote
    label = quote("%s:%s" % (issuer, name))
    return ("otpauth://totp/%s?secret=%s&issuer=%s&digits=%d&period=%d"
            % (label, secret, quote(issuer), TOTP_DIGITS, TOTP_STEP))


# ---- accounts -------------------------------------------------------------

def _key(name):
    """How names are compared: trimmed and case-folded.

    Stored as typed, matched without regard to case, so "Seth" and "seth" are
    one account rather than two people who think they are the same one.
    """
    return (name or "").strip().casefold()


def all_accounts():
    return _read()["accounts"]


def find(name):
    wanted = _key(name)
    if not wanted:
        return None
    for account in all_accounts():
        if _key(account.get("name")) == wanted:
            return account
    return None


def add(name, password, capabilities=()):
    """Create one. Returns (account, secret) -- the secret is shown once."""
    name = (name or "").strip()
    if not name:
        raise AccountError("An account needs a name.")
    if len(name) > 32:
        raise AccountError("That name is too long -- 32 characters at most.")
    existing = find(name)
    if existing is not None:
        # Named as it is stored, not as it was typed: "there is already an
        # account called 'SETH'" is baffling when the one in the way is seth.
        raise AccountError("There is already an account called %r."
                           % existing["name"])
    if not password or len(password) < 8:
        raise AccountError("A password needs to be at least eight characters.")
    for cap in capabilities:
        check_capability(cap)

    data = _read()
    secret = new_secret()
    account = {
        "name": name,
        "password": hash_password(password),
        "totp": secret,
        "used_step": -1,
        "can": sorted(set(capabilities)),
        "added": int(time.time()),
        "last_seen": 0,
    }
    data["accounts"].append(account)
    _write(data)
    log.info("account %r created", name)
    return account, secret


def remove(name):
    data = _read()
    kept = [a for a in data["accounts"] if _key(a.get("name")) != _key(name)]
    if len(kept) == len(data["accounts"]):
        raise AccountError("There is no account called %r." % name)
    data["accounts"] = kept
    _write(data)
    log.info("account %r removed", name)


def set_capabilities(name, capabilities):
    for cap in capabilities:
        check_capability(cap)
    data = _read()
    for account in data["accounts"]:
        if _key(account.get("name")) == _key(name):
            account["can"] = sorted(set(capabilities))
            _write(data)
            log.info("account %r may now: %s", name, " ".join(account["can"]) or "nothing")
            return account
    raise AccountError("There is no account called %r." % name)


def reset_totp(name):
    """A new shared secret, for a phone that has been lost. Shown once."""
    data = _read()
    for account in data["accounts"]:
        if _key(account.get("name")) == _key(name):
            secret = new_secret()
            account["totp"] = secret
            account["used_step"] = -1
            # A new secret is asked for because a phone was lost, and that
            # phone is exactly the kind of thing that was remembered.
            account["devices"] = []
            _write(data)
            log.info("account %r has a new authenticator secret", name)
            return account, secret
    raise AccountError("There is no account called %r." % name)


def set_password(name, password):
    if not password or len(password) < 8:
        raise AccountError("A password needs to be at least eight characters.")
    data = _read()
    for account in data["accounts"]:
        if _key(account.get("name")) == _key(name):
            account["password"] = hash_password(password)
            _write(data)
            return account
    raise AccountError("There is no account called %r." % name)


def check_capability(cap):
    """Refuse a capability this program does not have, at the door.

    A typo stored is a permission that never matches and a person who thinks
    they were given something. Per-game grants are "steam:<appid>".
    """
    if cap in CAPABILITIES:
        return cap
    if cap.startswith("steam:") and cap[6:].isdigit():
        return cap
    raise AccountError(
        "%r is not something an account can be given. Try: %s, or steam:<appid>."
        % (cap, ", ".join(CAPABILITIES)))


def allows(account, capability):
    """Whether this account holds a capability.

    `steam` covers every Steam game; `steam:274190` covers one. Asked as
    allows(account, "steam:274190"), a holder of plain `steam` is allowed --
    which is what "any Steam game on the list" means.
    """
    if not account:
        return False
    can = account.get("can") or []
    if capability in can:
        return True
    if capability.startswith("steam:") and "steam" in can:
        return True
    return False


# ---- remembered devices ---------------------------------------------------
#
# A device token is 256 bits of randomness this program generated, so a bare
# SHA-256 of it is the right hash -- the same reasoning as the invite token,
# and the opposite of the reasoning for a password. There is nothing to grind:
# an attacker guessing a device token is guessing a 256-bit number.
#
# They live inside the account they belong to, which means deleting an account
# deletes its devices without anybody having to remember to.

def _device_digest(token):
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def remember_device(name, label="", now=None):
    """Issue a token for one device. Returns it once; only a digest is kept."""
    now = int(now if now is not None else time.time())
    token = secrets.token_urlsafe(DEVICE_BYTES)
    data = _read()
    for account in data["accounts"]:
        if _key(account.get("name")) == _key(name):
            devices = [d for d in account.get("devices", [])
                       if int(d.get("expires", 0)) > now]
            devices.append({"digest": _device_digest(token),
                            "label": (label or "")[:40],
                            "added": now,
                            "expires": now + DEVICE_DAYS * 86400})
            account["devices"] = devices
            _write(data)
            return token
    raise AccountError("There is no account called %r." % name)


def device_account(token, now=None):
    """The account a remembered device belongs to, if it is still valid.

    A remembered device restores who you are. It is deliberately not enough
    for the dangerous capabilities -- what asks for a fresh code is decided
    where the action is taken, not here.
    """
    if not token:
        return None
    now = int(now if now is not None else time.time())
    digest = _device_digest(token)
    for account in all_accounts():
        for device in account.get("devices", []):
            if (hmac.compare_digest(str(device.get("digest", "")), digest)
                    and int(device.get("expires", 0)) > now):
                return account
    return None


def forget_devices(name):
    """Sign every remembered device out -- the lent-phone path."""
    data = _read()
    for account in data["accounts"]:
        if _key(account.get("name")) == _key(name):
            count = len(account.get("devices", []))
            account["devices"] = []
            _write(data)
            return count
    raise AccountError("There is no account called %r." % name)


def verify(name, password, code, now=None):
    """A login. Returns the account, or None -- and never says which half failed.

    The step a code was accepted for is written down before this returns, so
    the same code cannot be presented twice. That write is the reason this
    takes the whole account rather than a password checker: a verification
    that does not record anything is one an attacker may repeat.
    """
    account = find(name)
    if account is None:
        # Still spend the time. Answering instantly for an unknown name tells
        # an attacker which names exist, which is the one thing a login should
        # not volunteer.
        hash_password(password or "")
        return None
    if not check_password(password or "", account.get("password") or {}):
        return None
    step = check_code(account.get("totp") or "", code,
                      now=now, after=int(account.get("used_step", -1)))
    if step is None:
        return None

    data = _read()
    for stored in data["accounts"]:
        if _key(stored.get("name")) == _key(account["name"]):
            stored["used_step"] = step
            stored["last_seen"] = int(now if now is not None else time.time())
            account = stored
            break
    _write(data)
    return account
