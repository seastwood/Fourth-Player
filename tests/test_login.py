"""Logging in to a named account, over the socket that is already open.

This drives the real Server._login and the real LiveSession limiter with a
fake websocket and a fake guest, so the message handling, the thread the
hashing runs in, the lockout keying and the replay guard are all the shipping
code. No video, no WebRTC, no accounts file but a temporary one.

The clock is a parameter throughout, which is what lets a ten-minute lockout
be tested in microseconds.
"""
import asyncio
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


try:
    from fourthplayer import accounts, invites, server as serverlib
    from fourthplayer.session import LiveSession, GuestConnection
except Exception as exc:                  # evdev, GStreamer, websockets
    print("SKIPPED: this machine cannot import the host (%s)" % exc)
    sys.exit(0)

folder = tempfile.mkdtemp(prefix="fp-login-")
accounts.STORE = os.path.join(folder, "accounts.json")
REAL_N = accounts.SCRYPT_N
accounts.SCRYPT_N = 2 ** 8                # the cost is proved in test_accounts

CLOCK = [1000.0]


class FakeSocket:
    remote_address = ("10.0.0.5", 4000)
    request_headers = {}


def make_config():
    """The real defaults, so this test does not drift from them."""
    from fourthplayer.config import Config
    cfg = Config()
    cfg.behind_proxy = False
    return cfg


class FakeGuest(GuestConnection):
    """A connection with no pad, no peer and no socket -- just the fields."""
    def __init__(self, label="guest"):
        self.label = label
        self.slot = 1


def make_server():
    loop = asyncio.new_event_loop()
    srv = serverlib.Server(make_config())
    srv.loop = loop
    session = LiveSession(make_config(), loop, now=lambda: CLOCK[0])
    session.guests = {}
    srv.session = session
    return srv, session, loop


def login(srv, loop, guest, **fields):
    """Send one login message and return what came back."""
    outbox = asyncio.Queue()
    message = {"t": "login"}
    message.update(fields)
    loop.run_until_complete(srv._login(guest, FakeSocket(), message, outbox))
    return outbox.get_nowait() if not outbox.empty() else None


srv, session, loop = make_server()
_, secret = accounts.add("seth", "a-good-password", ["kick", "steam"])
NOW = 1_700_000_000
step = NOW // accounts.TOTP_STEP


# accounts.verify reads the wall clock, so every code has to be for now.
# Only three steps are ever valid -- this one and one either side -- so a
# suite that needs more than three successful logins cannot simply count
# upwards. It clears the replay guard instead, which is exactly what the
# dedicated replay check below does not do.
import time as _time


def code(offset=0, sec=None):
    now_step = int(_time.time()) // accounts.TOTP_STEP
    return accounts.code_at(sec or secret, now_step + offset)


def fresh_code(sec=None):
    """A code that has not been used, for a test that is not about replay."""
    data = accounts._read()
    for account in data["accounts"]:
        account["used_step"] = -1
    accounts._write(data)
    return code(sec=sec)

print("a login that works")
guest = FakeGuest()
reply = login(srv, loop, guest, name="seth", password="a-good-password", code=code())
check(reply and reply.get("t") == "loggedin", "it says logged in: %r" % (reply,))
check(reply.get("name") == "seth", "it says who")
check(sorted(reply.get("can", [])) == ["kick", "steam"], "it says what they may do")
check(reply.get("fresh") is True, "and that a code was presented just now")
check(guest.account == "seth", "the connection remembers the account")
check(guest.can("kick") and guest.can("steam:1"), "and answers for its capabilities")
check(not guest.can("grant"), "and only for the ones it has")
check(guest.logged_in_at > 0, "the moment of the code is written down")
check("device" not in reply, "nothing is remembered unless it was asked for")

print("\nthe same code cannot be used twice")
again = FakeGuest("someone else")
reply = login(srv, loop, again, name="seth", password="a-good-password", code=code())
check(reply.get("t") == "error", "a replayed code is refused")
check(again.account is None, "and the second connection got nothing")

print("\none answer for every kind of wrong")
said = set()
for wrong in ({"name": "nobody", "password": "a-good-password", "code": code()},
              {"name": "seth", "password": "wrong-password!", "code": code()},
              {"name": "seth", "password": "a-good-password", "code": "000000"},
              {"name": "", "password": "", "code": ""}):
    # A fresh limiter each time: three of these in a row would lock out, and
    # what is being compared here is the refusal, not the lockout.
    srv, session, loop = make_server()
    reply = login(srv, loop, FakeGuest(), **wrong)
    said.add((reply.get("reason"), reply.get("message")))
check(len(said) == 1, "every refusal says exactly the same thing: %r" % said)
check(said.pop() == ("login", serverlib.LOGIN_REFUSED), "and it says nothing useful")

print("\nthree strikes")
srv, session, loop = make_server()
for n in range(invites.LOCKOUT_AFTER - 1):
    reply = login(srv, loop, FakeGuest(), name="seth", password="no", code="000000")
    check(reply.get("reason") == "login", "wrong answer %d is just refused" % (n + 1))
reply = login(srv, loop, FakeGuest(), name="seth", password="no", code="000000")
check(reply.get("reason") == "locked", "the third locks out")
check(reply.get("retry_after") == round(invites.LOCKOUT_STEPS[0]),
      "and says for how long, got %r" % reply.get("retry_after"))
reply = login(srv, loop, FakeGuest(), name="seth", password="a-good-password",
              code=fresh_code())
check(reply.get("reason") == "locked",
      "even the right answer is refused while locked out")
CLOCK[0] += invites.LOCKOUT_STEPS[0] + 1
reply = login(srv, loop, FakeGuest(), name="seth", password="a-good-password",
              code=fresh_code())
check(reply.get("t") == "loggedin", "and works once the wait is over")

print("\nthe lockout follows the name, not only the address")
srv, session, loop = make_server()


class OtherAddress(FakeSocket):
    remote_address = ("10.0.0.99", 4000)


for _ in range(invites.LOCKOUT_AFTER):
    login(srv, loop, FakeGuest(), name="seth", password="no", code="000000")
outbox = asyncio.Queue()
loop.run_until_complete(srv._login(
    FakeGuest(), OtherAddress(),
    {"t": "login", "name": "seth", "password": "a-good-password",
     "code": fresh_code()},
    outbox))
reply = outbox.get_nowait()
check(reply.get("reason") == "locked",
      "moving to another network does not reset it, got %r" % (reply,))

print("\nand the address, not only the name")
srv, session, loop = make_server()
accounts.add("mate", "another-password")
for _ in range(invites.LOCKOUT_AFTER):
    login(srv, loop, FakeGuest(), name="seth", password="no", code="000000")
reply = login(srv, loop, FakeGuest(), name="mate", password="another-password",
              code="000000")
check(reply.get("reason") == "locked",
      "trying a different name from the same address does not reset it")

print("\nremembering a device")
srv, session, loop = make_server()
guest = FakeGuest()
reply = login(srv, loop, guest, name="seth", password="a-good-password",
              code=fresh_code(), remember=True)
check(reply.get("t") == "loggedin" and reply.get("device"),
      "asking to be remembered hands back a device token")
token = reply["device"]
back = FakeGuest("the same phone, later")
reply = login(srv, loop, back, device=token)
check(reply.get("t") == "loggedin", "the token logs in on its own")
check(reply.get("name") == "seth" and sorted(reply.get("can", [])) == ["kick", "steam"],
      "with the same account and capabilities")
check(reply.get("fresh") is False, "but it says no code was presented")
check(back.logged_in_at == 0.0,
      "so the capabilities that land on other people can still ask for one")
reply = login(srv, loop, FakeGuest(), device="not-a-real-token")
check(reply.get("t") == "error", "a made-up token is refused")
check(reply.get("reason") == "login", "as a login problem")
check(session.login_limiter.failures == {},
      "and an aged-out token is not counted against anybody")

print("\nlogging out, and having it taken away")
srv, session, loop = make_server()
guest = FakeGuest()
session.guests = {1: guest}
login(srv, loop, guest, name="seth", password="a-good-password",
      code=fresh_code())
check(guest.can("kick"), "logged in and allowed")
accounts.set_capabilities("seth", ["steam"])
session.refresh_capabilities("seth")
check(not guest.can("kick"),
      "a capability taken away at the console reaches a phone already connected")
check(guest.can("steam"), "and the ones left still work")
accounts.remove("seth")
session.refresh_capabilities("seth")
check(guest.account is None and not guest.can("steam"),
      "and a deleted account logs the connection out")
session.logout(guest)
check(guest.account is None and guest.capabilities == (),
      "logging out clears it")

print("\na login dies with the socket that made it")
# The one place a connection outlives its socket: resume hands back the same
# object so the slot, the pad and the player port survive a network switch.
# The account was surviving with them, which is how somebody reopened their
# page, was shown as logged out, and could still play a Steam game nobody had
# given them -- the page had forgotten and the host had not.
# The block above deleted the account, so build it again.
_, secret = accounts.add("seth", "a-good-password", ["kick", "steam"])
srv, session, loop = make_server()
guest = FakeGuest()
guest.slot = 2
session.guests = {2: guest}
login(srv, loop, guest, name="seth", password="a-good-password", code=fresh_code())
check(guest.account == "seth", "logged in on the first socket")


class Record:
    slot = 2


class Invite:
    guests = {2: Record()}

    def guest_for(self, token, now=None):
        return Record()


session.invite = Invite()
session.detach_peer = lambda g: None
session.publish_pad_names = lambda: None
back = session.resume("a-token", object(), "")
check(back is guest, "resume gives the same connection back")
check(back.account is None and back.capabilities == (),
      "and it is nobody again: %r %r" % (back.account, back.capabilities))
check(back.logged_in_at == 0.0, "with no code to its name either")

print("\nand a remembered device is how it comes back")
srv, session, loop = make_server()
guest = FakeGuest()
session.guests = {1: guest}
reply = login(srv, loop, guest, name="seth", password="a-good-password",
              code=fresh_code(), remember=True)
token = reply.get("device")
check(token, "the first login hands one out")
session.logout(guest)
back = FakeGuest()
reply = login(srv, loop, back, device=token)
check(back.account == "seth",
      "and it logs the new connection in without a password")
check(back.logged_in_at == 0.0,
      "though not freshly enough for the things that land on other people")

print("\na damaged accounts file is a refusal, not a crash")
srv, session, loop = make_server()
open(accounts.STORE, "w").write("{ not json")
reply = login(srv, loop, FakeGuest(), name="seth", password="a-good-password",
              code=code())
check(reply.get("t") == "error", "a login against a corrupt store is refused")

print("\nhashing does not stall everybody else")
# scrypt is meant to be slow. On the event loop that slowness is everybody
# else's video signalling, chat and input stopping for the duration, and a
# handful of wrong passwords would be a way to make the session stutter on
# purpose. So it runs in a thread, and this is the check that says so.
# The check above left the store deliberately broken, so build it again.
os.unlink(accounts.STORE)
accounts.SCRYPT_N = REAL_N
_, secret = accounts.add("seth", "a-good-password", ["kick", "steam"])
srv, session, loop = make_server()
beats = []


async def heartbeat():
    for _ in range(40):
        beats.append(1)
        await asyncio.sleep(0.005)


async def both():
    ticking = asyncio.ensure_future(heartbeat())
    outbox = asyncio.Queue()
    started = _time.time()
    await srv._login(FakeGuest(), FakeSocket(),
                     {"t": "login", "name": "seth", "password": "a-good-password",
                      "code": fresh_code()}, outbox)
    took = _time.time() - started
    during = len(beats)
    ticking.cancel()
    return outbox.get_nowait(), took, during


reply, took, during = loop.run_until_complete(both())
check(reply.get("t") == "loggedin", "the login works at the real cost")
check(took > 0.02, "and it really is doing the slow hashing: %.0f ms" % (took * 1000))
check(during > 2,
      "the loop kept running while it hashed -- %d other turns in %.0f ms"
      % (during, took * 1000))

loop.close()
shutil.rmtree(folder, ignore_errors=True)
print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
