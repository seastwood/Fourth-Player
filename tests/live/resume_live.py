"""Closing the page mid-game and opening it again.

The bug this exists for: reopen the page while a Steam game is playing and
you were shown as logged out while still being able to play it. resume()
hands back the same connection object -- deliberately, so the slot, the pad
and the player port survive a network switch -- and the account was surviving
with it. The page had forgotten and the host had not.

Not in run.sh: it needs a session that is open and an account that exists.
Run it by hand, on the box, against a throwaway account:

    fourth-player admin add probe
    fourth-player admin can probe steam lock kick
    fourth-player reshare
    python3 tests/live/resume_live.py <token> <pin> <secret> <password>
    fourth-player admin remove probe
"""
import asyncio, base64, hashlib, hmac, json, ssl, struct, sys, time, websockets
TOKEN, PIN, SECRET, PASSWORD = sys.argv[1:5]
ACCOUNT = sys.argv[5] if len(sys.argv) > 5 else "probe"
LAX = ssl.create_default_context(); LAX.check_hostname = False; LAX.verify_mode = ssl.CERT_NONE
fails = []
def check(c, m):
    print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)
def code(o=0):
    k = base64.b32decode(SECRET, casefold=True)
    d = hmac.new(k, struct.pack(">Q", int(time.time())//30 + o), hashlib.sha1).digest()
    i = d[-1] & 15
    return str((struct.unpack(">I", d[i:i+4])[0] & 0x7FFFFFFF) % 1000000).zfill(6)
async def wait(ws, kinds, s=8.0):
    end = time.time() + s
    while time.time() < end:
        try: m = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(0.05, end-time.time())))
        except asyncio.TimeoutError: return None
        if m.get("t") in kinds: return m
async def main():
    ws = await websockets.connect("wss://127.0.0.1:8443/ws", ssl=LAX, max_size=2**22)
    await ws.send(json.dumps({"t":"join","token":TOKEN,"pin":PIN,"name":"Probe",
                              "input":"only","codecs":["H264"]}))
    hello = await wait(ws, ("joined","error"))
    guest_token = hello.get("guest")
    check(hello.get("t") == "joined", "joined")

    await ws.send(json.dumps({"t":"login","name":ACCOUNT,"password":PASSWORD,
                              "code":code(),"remember":False}))
    got = await wait(ws, ("loggedin","error"))
    check(got.get("t") == "loggedin", "logged in without asking to be remembered: %r" % got)
    await ws.send(json.dumps({"t":"games"}))
    games = await wait(ws, ("games",))
    check(any(g.get("system","").lower() == "steam" for g in games["games"]),
          "and can see the Steam games")

    print("\nnow close the page and reopen it -- a new socket, same guest token")
    await ws.close()
    ws2 = await websockets.connect("wss://127.0.0.1:8443/ws", ssl=LAX, max_size=2**22)
    await ws2.send(json.dumps({"t":"resume","guest":guest_token,"name":"Probe",
                               "codecs":["H264"],"media":"new"}))
    back = await wait(ws2, ("joined","error"))
    check(back.get("t") == "joined", "the resume works: %r" % back.get("t"))
    check(back.get("account") is None,
          "and the host does NOT still think we are logged in: %r" % back.get("account"))
    await ws2.send(json.dumps({"t":"games"}))
    games = await wait(ws2, ("games",))
    steam = [g["label"] for g in games["games"] if g.get("system","").lower() == "steam"]
    check(not steam, "the Steam games are hidden again: %r" % steam)
    await ws2.send(json.dumps({"t":"lock","on":True}))
    denied = await wait(ws2, ("limits","error"))
    check(denied.get("reason") == "denied",
          "and the powers are gone with it: %r" % denied)
    await ws2.close()

    print("\nwith a remembered device, it comes back on its own")
    ws3 = await websockets.connect("wss://127.0.0.1:8443/ws", ssl=LAX, max_size=2**22)
    await ws3.send(json.dumps({"t":"join","token":TOKEN,"pin":PIN,"name":"Probe2",
                               "input":"only","codecs":["H264"]}))
    h3 = await wait(ws3, ("joined","error"))
    tok3 = h3.get("guest")
    await ws3.send(json.dumps({"t":"login","name":ACCOUNT,"password":PASSWORD,
                               "code":code(1),"remember":True}))
    got = await wait(ws3, ("loggedin","error"))
    device = got.get("device")
    check(device, "asking to be remembered hands back a device token")
    await ws3.close()
    ws4 = await websockets.connect("wss://127.0.0.1:8443/ws", ssl=LAX, max_size=2**22)
    await ws4.send(json.dumps({"t":"resume","guest":tok3,"name":"Probe2",
                               "codecs":["H264"],"media":"new","device":device}))
    back = await wait(ws4, ("joined","error"))
    check((back.get("account") or {}).get("name") == ACCOUNT,
          "and the reopened page is logged straight back in: %r" % back.get("account"))
    check((back.get("account") or {}).get("fresh") is False,
          "but says no code was presented, so kick and lock still ask for one")
    await ws4.close()
asyncio.run(main())
print()
print("%d FAILED" % len(fails) if fails else "all good")
sys.exit(1 if fails else 0)
