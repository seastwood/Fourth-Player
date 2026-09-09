"""The second factor, over a real socket, end to end.

The shape this exists for: somebody logged in by a remembered device taps
"use the mouse", the host says it wants an authenticator code, and there has
to be a way to give it one. There was not -- the code field lived in the login
form and the login form is not drawn for somebody already logged in.

Not in run.sh: it needs an open session and an account. Run it by hand:

    fourth-player admin add codeprobe --can desk
    fourth-player reshare
    python3 tests/live/code_live.py <token> <pin> codeprobe <password> <secret>

It waits for a fresh thirty-second step on purpose. A code that has been
accepted once is spent, so re-using the one the setup burnt would look
exactly like the path being broken when it is the replay guard working.
"""

import asyncio, base64, hashlib, hmac, json, ssl, struct, sys, time
import websockets

TOKEN, PIN, USER, PASS, SECRET = sys.argv[1:6]
LAX = ssl.create_default_context(); LAX.check_hostname = False
LAX.verify_mode = ssl.CERT_NONE
fails = []
def check(c, m):
    print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)

def code(step_offset=0):
    key = base64.b32decode(SECRET, casefold=True)
    step = int(time.time() // 30) + step_offset
    d = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = d[-1] & 15
    return "%06d" % ((struct.unpack(">I", d[o:o+4])[0] & 0x7fffffff) % 1000000)

async def grab(ws, kind, tries=14):
    for _ in range(tries):
        msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if msg.get("t") == kind:
            return msg
        if msg.get("t") == "error" and kind != "error":
            return msg
    return None

async def main():
    # 1. Join as a stranger, then log in and ask to be remembered. One code
    #    spent, so the next step is free for the part being tested.
    async with websockets.connect("wss://127.0.0.1:8443/ws", ssl=LAX) as ws:
        await ws.send(json.dumps({"t": "join", "token": TOKEN, "pin": PIN,
                                  "name": "codeprobe", "input": "only"}))
        await grab(ws, "joined")
        spent = code()
        await ws.send(json.dumps({"t": "login", "name": USER,
                                  "password": PASS, "code": spent,
                                  "remember": True}))
        got = await grab(ws, "loggedin")
        device = (got or {}).get("device")
        check(bool(device), "a device token is handed out to be remembered")

    # Wait for the next thirty-second step, so the code below is one this
    # account has not already spent -- which is the replay guard working, and
    # would otherwise look like the new path being broken.
    while code() == spent:
        await asyncio.sleep(2)
    # 2. Come back on the remembered device: knows who, not fresh.
    async with websockets.connect("wss://127.0.0.1:8443/ws", ssl=LAX) as ws:
        await ws.send(json.dumps({"t": "join", "token": TOKEN, "pin": PIN,
            "name": "codeprobe2", "input": "only",
            "device": device}))
        joined = await grab(ws, "joined")
        acct = (joined or {}).get("account") or {}
        check(acct.get("name") == USER, "it knows who we are: %r" % acct.get("name"))
        check(acct.get("fresh") is False, "and that we have not just proved we are here")

        # 3. Ask for the desk. Refused, for want of a code.
        await ws.send(json.dumps({"t": "desk", "take": True}))
        said = await grab(ws, "error")
        check((said or {}).get("reason") == "code",
              "the desk is refused for want of a code: %r" % (said,))

        # 4. Answer with the code alone -- the thing there was no way to do.
        await ws.send(json.dumps({"t": "login", "code": code()}))
        back = await grab(ws, "loggedin")
        check((back or {}).get("fresh") is True,
              "the code alone is accepted: %r" % (back,))

        # 5. And now the desk is given.
        await ws.send(json.dumps({"t": "desk", "take": True}))
        now = await grab(ws, "desk")
        check((now or {}).get("on") is True,
              "and the keyboard and mouse are handed over: %r" % (now,))

        # 6. The same code cannot be spent twice.
        await ws.send(json.dumps({"t": "login", "code": code()}))
        again = await grab(ws, "error")
        check((again or {}).get("reason") in ("login", "locked"),
              "a code already spent is refused: %r" % (again,))
        await ws.send(json.dumps({"t": "desk", "take": False}))

asyncio.run(main())
print()
print("%d FAILED" % len(fails) if fails else "all good")
sys.exit(1 if fails else 0)
