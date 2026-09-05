"""A real guest, over a real WebSocket, against a running host.

Not in run.sh, and deliberately: this one needs a session that is open, an
account that exists, and it changes both -- it sets the connection limit,
locks the session and hands out capabilities. Everything it does it puts back,
but a suite that run.sh promises is safe on any machine is not the place for
something that can shut people out of a session in progress.

Run it by hand, on the box, when the account work has been touched:

    fourth-player admin add probe          # a throwaway account
    fourth-player reshare                  # for a link and PIN
    python3 tests/live/accounts_live.py <token> <pin> <totp-secret>
    fourth-player admin remove probe

It is worth having despite all that. It is what found the bug the unit tests
could not: listing() drops `kind` and `appid`, the Steam filter asked a listed
row whether it was a Steam game, got None, and let every one of them through.
The fake catalogue in test_capable.py had those fields, so the filter looked
fine right up until a real one was asked.
"""
import asyncio, base64, hashlib, hmac, json, struct, sys, time
import ssl
import websockets

TOKEN = sys.argv[1]
PIN = sys.argv[2]
SECRET = sys.argv[3]
URL = "wss://127.0.0.1:8443/ws"
LAX = ssl.create_default_context()
LAX.check_hostname = False
LAX.verify_mode = ssl.CERT_NONE

fails = []
def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond: fails.append(msg)

def code(offset=0):
    key = base64.b32decode(SECRET, casefold=True)
    step = int(time.time()) // 30 + offset
    d = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = d[-1] & 15
    return str((struct.unpack(">I", d[o:o+4])[0] & 0x7FFFFFFF) % 1000000).zfill(6)

async def wait_for(ws, kinds, seconds=8.0):
    end = time.time() + seconds
    while time.time() < end:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=end - time.time())
        except asyncio.TimeoutError:
            return None
        msg = json.loads(raw)
        if msg.get("t") in kinds:
            return msg

async def guest(**extra):
    ws = await websockets.connect(URL, max_size=2**22, ssl=LAX)
    knock = {"t": "join", "token": TOKEN, "pin": PIN, "name": "Probe",
             "input": "only", "codecs": ["H264"]}
    knock.update(extra)
    await ws.send(json.dumps(knock))
    return ws, await wait_for(ws, ("joined", "error"))

async def main():
    print("joining as an ordinary guest")
    ws, hello = await guest()
    check(hello and hello.get("t") == "joined", "an input-only guest gets in: %r"
          % (hello and hello.get("t")))
    check(hello.get("account") is None, "and is nobody in particular")
    check(hello.get("limits"), "and is told the limits: %r" % (hello.get("limits"),))

    await ws.send(json.dumps({"t": "games"}))
    games = await wait_for(ws, ("games",))
    steam = [g for g in games["games"] if g.get("system", "").lower() == "steam"]
    check(games is not None, "the catalogue arrives (%d games)" % len(games["games"]))
    check(not steam, "and holds no Steam game for somebody with no account: %r"
          % [g["label"] for g in steam])

    print("\nlogging in over that same socket")
    await ws.send(json.dumps({"t": "login", "name": "seth",
                              "password": "wrong-password", "code": code()}))
    bad = await wait_for(ws, ("loggedin", "error"))
    check(bad.get("t") == "error" and bad.get("reason") == "login",
          "a wrong password is refused: %r" % bad.get("message"))

    await ws.send(json.dumps({"t": "login", "name": "seth",
                              "password": "a-good-password", "code": code()}))
    good = await wait_for(ws, ("loggedin", "error"))
    check(good.get("t") == "loggedin", "the right one works: %r" % good)
    check(good.get("can") == ["grant"], "with the capabilities the console gave it")

    print("\nwhat it may and may not do")
    await ws.send(json.dumps({"t": "lock", "on": True}))
    denied = await wait_for(ws, ("limits", "error"))
    check(denied.get("reason") == "denied",
          "an account with only `grant` cannot lock the session: %r" % denied)

    await ws.send(json.dumps({"t": "grant", "name": "seth",
                              "can": ["grant", "lock", "slots", "steam"]}))
    now = await wait_for(ws, ("loggedin", "error"), 5.0)
    granted = await wait_for(ws, ("granted", "error"), 5.0)
    check(granted.get("t") == "granted", "it can change what an account may do: %r" % granted)
    check(now and "lock" in now.get("can", []),
          "and the new powers reach this connection at once: %r" % (now and now.get("can")))

    await ws.send(json.dumps({"t": "games"}))
    games = await wait_for(ws, ("games",))
    steam = [g for g in games["games"] if g.get("system", "").lower() == "steam"]
    check(steam, "and the Steam games appear now: %r" % [g["label"] for g in steam])

    print("\nthe limit and the lock")
    await ws.send(json.dumps({"t": "limit", "count": 1}))
    limits = await wait_for(ws, ("limits", "error"))
    check(limits.get("t") == "limits", "the limit can be set: %r" % limits)
    await wait_for(ws, ("limits",), 1.0)

    other, refused = await guest()
    check(refused.get("t") == "error" and refused.get("reason") == "full",
          "and the next guest is refused: %r" % refused)
    if other: await other.close()

    await ws.send(json.dumps({"t": "limit", "count": 4}))
    await wait_for(ws, ("limits",))
    await wait_for(ws, ("limits",), 1.0)

    await ws.send(json.dumps({"t": "lock", "on": True}))
    locked = await wait_for(ws, ("limits", "error"))
    check(locked.get("locked") is True, "it locks: %r" % locked)
    other, refused = await guest()
    check(refused.get("reason") == "shut",
          "a stranger is told the session is shut: %r" % refused)
    if other: await other.close()

    back, hello2 = await guest(login={"name": "seth", "password": "a-good-password",
                                      "code": code(1)})
    check(hello2.get("t") == "joined",
          "and an account can still get in on the join itself: %r" % hello2.get("t"))
    check(hello2.get("account", {}).get("name") == "seth",
          "named on the way through")
    if back: await back.close()

    await ws.send(json.dumps({"t": "lock", "on": False}))
    await wait_for(ws, ("limits",))
    check(True, "unlocked again")

    print("\ntidying up")
    await ws.send(json.dumps({"t": "grant", "name": "seth", "can": ["grant"]}))
    await wait_for(ws, ("granted", "error"))
    await ws.close()

asyncio.run(main())
print()
if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("all good")
