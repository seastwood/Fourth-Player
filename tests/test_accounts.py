"""Accounts: the password, the second factor, and what each one may do.

Nothing here touches the real store -- A.STORE is pointed at a temporary file
first. Most of the suite also winds scrypt down to a toy cost, because the
point of those checks is the logic around it; the real cost is measured once,
at the end, at the parameters the program actually ships with.
"""
import base64
import hashlib
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from fourthplayer import accounts as A

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


REAL_N = A.SCRYPT_N
folder = tempfile.mkdtemp(prefix="fp-accounts-")
A.STORE = os.path.join(folder, "state", "accounts.json")
A.SCRYPT_N = 2 ** 8          # fast, for everything but the cost check


def wipe():
    try:
        os.unlink(A.STORE)
    except OSError:
        pass


print("the file")
wipe()
A.add("seth", "a-good-password", ["kick"])
check(os.path.exists(A.STORE), "the store is created on first use")
check(oct(os.stat(A.STORE).st_mode & 0o777) == "0o600",
      "the store is readable only by its owner, got %s"
      % oct(os.stat(A.STORE).st_mode & 0o777))
check(oct(os.stat(os.path.dirname(A.STORE)).st_mode & 0o700) == "0o700",
      "the folder it sits in is private too")
check(not os.path.exists(A.STORE + ".new"), "no temporary file is left behind")
seen = []
real_replace = os.replace
os.replace = lambda a, b: (seen.append(os.stat(a).st_mode & 0o777), real_replace(a, b))[1]
A.set_password("seth", "a-good-password")
os.replace = real_replace
check(seen and seen[0] == 0o600,
      "the temporary file is private before it is renamed into place, got %s"
      % (oct(seen[0]) if seen else "no write seen"))
raw = open(A.STORE, encoding="utf-8").read()
check("a-good-password" not in raw, "the password is not in the file")

print("\nthe password is not a token")
plain = "a-good-password"
stored = A.find("seth")["password"]
bare = hashlib.sha256(plain.encode()).digest()
check(base64.b64decode(stored["key"]) != bare,
      "the stored key is not a bare sha256 of the password")
check(len(base64.b64decode(stored["salt"])) == A.SALT_BYTES, "there is a real salt")
check(A.hash_password(plain)["salt"] != A.hash_password(plain)["key"],
      "salt and key are different things")
check(A.hash_password(plain)["salt"] != A.hash_password(plain)["salt"],
      "two hashes of one password use different salts")
check(A.check_password(plain, stored), "the right password verifies")
check(not A.check_password("a-good-passworD", stored), "one wrong character fails")
check(not A.check_password("", stored), "an empty password fails")
check(not A.check_password(plain, {}), "a missing hash fails rather than raising")
check(not A.check_password(plain, {"salt": "!!", "key": "!!"}),
      "a mangled hash fails rather than raising")

print("\nthe authenticator, against RFC 6238")
# Appendix B: the secret is the ASCII "12345678901234567890", and at T=59 the
# eight-digit code is 94287082. Six digits is the last six of that.
vector = base64.b32encode(b"12345678901234567890").decode()
check(A.code_at(vector, 59 // 30) == "287082",
      "T=59 gives 287082, got %s" % A.code_at(vector, 59 // 30))
check(A.code_at(vector, 1111111109 // 30) == "081804",
      "T=1111111109 gives 081804, got %s" % A.code_at(vector, 1111111109 // 30))
check(A.code_at(vector, 2000000000 // 30) == "279037",
      "T=2000000000 gives 279037, got %s" % A.code_at(vector, 2000000000 // 30))
secret = A.new_secret()
check(len(base64.b32decode(secret)) == A.TOTP_SECRET_BYTES, "a fresh secret is 160 bits")
check(A.new_secret() != A.new_secret(), "two fresh secrets differ")

print("\ndrift")
now = 1_700_000_000
step = now // A.TOTP_STEP
check(A.check_code(secret, A.code_at(secret, step), now=now) == step,
      "this step's code is accepted")
check(A.check_code(secret, A.code_at(secret, step - 1), now=now) == step - 1,
      "the previous step is accepted")
check(A.check_code(secret, A.code_at(secret, step + 1), now=now) == step + 1,
      "the next step is accepted")
check(A.check_code(secret, A.code_at(secret, step - 2), now=now) is None,
      "two steps ago is refused")
check(A.check_code(secret, A.code_at(secret, step + 2), now=now) is None,
      "two steps ahead is refused")
check(A.check_code(secret, "12345", now=now) is None, "a five-digit code is refused")
check(A.check_code(secret, "", now=now) is None, "no code is refused")
check(A.check_code(secret, None, now=now) is None, "a missing code is refused")
check(A.check_code(secret, " %s " % A.code_at(secret, step), now=now) == step,
      "spaces around a code are ignored")

print("\na code is one-time")
check(A.check_code(secret, A.code_at(secret, step), now=now, after=step) is None,
      "the step already used is refused")
check(A.check_code(secret, A.code_at(secret, step - 1), now=now, after=step) is None,
      "an earlier step is refused once a later one has been used")
check(A.check_code(secret, A.code_at(secret, step + 1), now=now, after=step) == step + 1,
      "the next step is still accepted after this one was used")

print("\nlogging in")
wipe()
account, secret = A.add("seth", "a-good-password", ["kick", "steam"])
code = A.code_at(secret, now // A.TOTP_STEP)
check(A.verify("nobody", "a-good-password", code, now=now) is None,
      "an unknown name is refused")
check(A.verify("seth", "wrong-password-x", code, now=now) is None,
      "a wrong password is refused")
check(A.verify("seth", "a-good-password", "000000" if code != "000000" else "111111",
               now=now) is None, "a wrong code is refused")
check(A.find("seth").get("used_step", -1) == -1,
      "a failed login records no step, so a good code is not burnt by a bad password")
got = A.verify("seth", "a-good-password", code, now=now)
check(got is not None and got["name"] == "seth", "the right name, password and code work")
check(A.find("seth")["used_step"] == now // A.TOTP_STEP, "the step used is written down")
check(A.find("seth")["last_seen"] == now, "the login is dated")
check(A.verify("seth", "a-good-password", code, now=now) is None,
      "the same code cannot be used twice")
check(A.verify("SETH", "a-good-password",
               A.code_at(secret, now // A.TOTP_STEP + 1), now=now) is not None,
      "the name is matched without regard to case")

print("\nan unknown name costs the same as a known one")
calls = []
real = A.hash_password
A.hash_password = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
A.verify("nobody-at-all", "a-good-password", code, now=now)
A.hash_password = real
check(calls, "a login for a name that does not exist still hashes a password")

print("\nwhat an account may do")
wipe()
A.add("seth", "a-good-password", ["steam", "kick"])
A.add("guest", "another-password", ["steam:274190"])
admin, guest = A.find("seth"), A.find("guest")
check(A.allows(admin, "kick"), "a granted capability is allowed")
check(not A.allows(guest, "kick"), "one not granted is not")
check(A.allows(admin, "steam:9999"), "plain steam covers any one game")
check(A.allows(guest, "steam:274190"), "a per-game grant covers that game")
check(not A.allows(guest, "steam:9999"), "and no other")
check(not A.allows(guest, "steam"), "a per-game grant is not all of steam")
check(not A.allows(None, "kick"), "nobody is allowed nothing")
check(not A.allows({"can": []}, "steam"), "an account with nothing is allowed nothing")

print("\ncapabilities are checked when they are written")
for bad in ("stem", "STEAM", "steam:", "steam:abc", "", "kick ", "admin"):
    try:
        A.check_capability(bad)
        check(False, "%r was accepted as a capability" % bad)
    except A.AccountError:
        check(True, "%r is refused" % bad)
for good in ("steam", "stop", "kick", "reshare", "grant", "steam:274190"):
    try:
        A.check_capability(good)
        check(True, "%r is accepted" % good)
    except A.AccountError:
        check(False, "%r should be a capability" % good)
try:
    A.add("typo", "a-good-password", ["stem"])
    check(False, "an account was created with a capability that does not exist")
except A.AccountError:
    check(True, "a bad capability stops the account being created")
check(A.find("typo") is None, "and nothing was written")

print("\nkeeping the list straight")
try:
    A.add("Seth", "a-good-password")
    check(False, "a name that differs only in case was allowed twice")
except A.AccountError as exc:
    check("'seth'" in str(exc),
          "the refusal names the account that is in the way, not what was typed: %s" % exc)
try:
    A.add("short", "abc")
    check(False, "a three-character password was allowed")
except A.AccountError:
    check(True, "a password under eight characters is refused")
try:
    A.add("  ", "a-good-password")
    check(False, "a blank name was allowed")
except A.AccountError:
    check(True, "a blank name is refused")
for call in (lambda: A.remove("ghost"),
             lambda: A.set_capabilities("ghost", ["kick"]),
             lambda: A.reset_totp("ghost"),
             lambda: A.set_password("ghost", "a-good-password")):
    try:
        call()
        check(False, "a missing account was changed without complaint")
    except A.AccountError:
        check(True, "changing an account that does not exist says so")

print("\nchanging one")
A.set_capabilities("guest", ["steam:274190", "stop"])
check(sorted(A.find("guest")["can"]) == ["steam:274190", "stop"],
      "capabilities are replaced wholesale")
before = A.find("guest")["totp"]
A.verify("guest", "another-password", A.code_at(before, now // A.TOTP_STEP), now=now)
check(A.find("guest")["used_step"] > -1, "a login left a used step")
_, after = A.reset_totp("guest")
check(after != before, "a reset gives a new secret")
check(A.find("guest")["used_step"] == -1, "and forgets the step, so the new phone works")
A.set_password("guest", "third-password")
check(A.verify("guest", "third-password",
               A.code_at(after, now // A.TOTP_STEP), now=now) is not None,
      "the new password works")
check(A.verify("guest", "another-password",
               A.code_at(after, now // A.TOTP_STEP + 1), now=now) is None,
      "the old one does not")
A.remove("guest")
check(A.find("guest") is None, "a removed account is gone")
check(A.find("seth") is not None, "and the other one is not")

print("\nremembered devices")
wipe()
_, sec = A.add("seth", "a-good-password", ["kick"])
A.add("other", "another-password")
token = A.remember_device("seth", label="the kitchen phone", now=now)
check(A.device_account(token, now=now)["name"] == "seth",
      "a remembered device says who it belongs to")
check(A.device_account("nonsense", now=now) is None, "a made-up token does not")
check(A.device_account("", now=now) is None, "no token does not")
check(A.device_account(token, now=now + A.DEVICE_DAYS * 86400 + 1) is None,
      "it stops working after a fortnight")
check(A.device_account(token, now=now + A.DEVICE_DAYS * 86400 - 1)["name"] == "seth",
      "and works right up to then")
raw = open(A.STORE, encoding="utf-8").read()
check(token not in raw, "only a digest of the device token is stored")
second = A.remember_device("seth", now=now)
check(second != token, "a second device gets its own token")
check(len(A.find("seth")["devices"]) == 2, "and both are remembered")
check(A.device_account(token, now=now) is not None
      and A.device_account(second, now=now) is not None, "both work")
check(A.forget_devices("seth") == 2, "signing out says how many went")
check(A.device_account(token, now=now) is None, "and then neither works")
token = A.remember_device("seth", now=now)
A.reset_totp("seth")
check(A.device_account(token, now=now) is None,
      "a new authenticator secret forgets the devices too -- the phone was lost")
token = A.remember_device("seth", now=now)
A.remove("seth")
check(A.device_account(token, now=now) is None,
      "removing the account removes its devices")
check(A.remember_device("other", now=now), "another account still gets one")
try:
    A.remember_device("ghost")
    check(False, "a device was remembered for an account that does not exist")
except A.AccountError:
    check(True, "there is no remembering a device for nobody")

print("\na store that has been damaged")
open(A.STORE, "w").write("{ not json")
try:
    A.all_accounts()
    check(False, "a corrupt store was treated as no accounts at all")
except A.AccountError:
    check(True, "a corrupt store refuses rather than starting again empty")
open(A.STORE, "w").write('{"accounts": "seth"}')
try:
    A.all_accounts()
    check(False, "a store of the wrong shape was accepted")
except A.AccountError:
    check(True, "a store of the wrong shape is refused")
wipe()
check(A.all_accounts() == [], "no file at all is simply no accounts")
check(A.find("seth") is None, "and nobody is found in it")

print("\nthe URI an authenticator reads")
uri = A.otpauth("seth", "ABCDEF")
check(uri.startswith("otpauth://totp/"), "it is an otpauth URI")
check("secret=ABCDEF" in uri, "it carries the secret")
check("issuer=Fourth%20Player" in uri, "it names the issuer, escaped")
check("digits=6" in uri and "period=30" in uri, "it states digits and period")

print("\nwhat it costs, at the real parameters")
A.SCRYPT_N = REAL_N
started = time.time()
A.hash_password("a-good-password")
took = time.time() - started
check(0.03 < took < 2.0, "one hash takes %.0f ms -- slow to grind, quick to log in"
      % (took * 1000))
check(A.SCRYPT_MAXMEM >= 128 * A.SCRYPT_N * A.SCRYPT_R,
      "maxmem is big enough for the parameters (the default is not)")

shutil.rmtree(folder, ignore_errors=True)
print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
