"""Starting a Steam game, and who may play it, against a running host.

The two things this found that nothing else could:

Steam takes about eighteen seconds to spawn the `reaper SteamLaunch AppId=`
marker the poll looks for. Until the appid was written down at launch, a
guest joining inside that window was not held from a game nobody had given
them -- caught here with a real guest playing Broforce with no account.

And logging in while a game is already playing changes whether a controller
is held, which the host was not telling anybody: it started letting the
frames through while the page went on saying "Controls paused".

Not in run.sh: it needs an open session and an account, and it takes over
the television for a minute. Run it by hand, on the box:

    fourth-player admin remove probe
    fourth-player admin add probe        # password throwaway-probe-pw
    fourth-player admin can probe steam
    fourth-player reshare
    python3 tests/live/steamhold_live.py <token> <pin> <secret> <password>
    fourth-player admin remove probe

Remove and remake the account each run: the replay guard refuses a code that
a previous run already used.
"""
import asyncio, base64, hashlib, hmac, json, ssl, struct, sys, time, websockets
TOKEN, PIN, SECRET, PW = sys.argv[1:5]
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
async def wait(ws, kinds, s=10.0):
    end = time.time() + s
    while time.time() < end:
        try: m = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(.05, end-time.time())))
        except asyncio.TimeoutError: return None
        if m.get("t") in kinds: return m
async def main():
    admin = await websockets.connect("wss://127.0.0.1:8443/ws", ssl=LAX, max_size=2**22)
    await admin.send(json.dumps({"t":"join","token":TOKEN,"pin":PIN,"name":"Admin",
                                 "input":"only","codecs":["H264"],
                                 "login":{"name":ACCOUNT,"password":PW,"code":code()}}))
    hello = await wait(admin, ("joined","error")) or {}
    check(hello.get("t") == "joined", "the account joined: %r" % hello)
    if hello.get("t") != "joined":
        return
    await admin.send(json.dumps({"t":"games"}))
    games = await wait(admin, ("games",))
    steam = [g for g in games["games"] if g.get("system","").lower() == "steam"]
    check(steam, "it can see the Steam games: %r" % [g["label"] for g in steam])
    if not steam:
        return
    pick = steam[0]
    print("\nstarting %s" % pick["label"])
    await admin.send(json.dumps({"t":"launch","game":pick["id"]}))
    print("   launchresult:", json.dumps(await wait(admin, ("launchresult",), 30.0)))
    # The host notices on its screen-poll tick.
    # Wait for the game itself to be in front, not the Steam client. While
    # the client is up the shell rule holds everybody, which is the ordinary
    # menu hold and not what is being tested here.
    settled = None
    for _ in range(20):
        hold = await wait(admin, ("hold",), 12.0)
        print("   hold pushed to the admin:", json.dumps(hold))
        if hold and hold.get("held") is False:
            settled = hold
            break
    check(settled is not None,
          "the account holding steam ends up with a live controller")

    print("\na guest with no account joins while it is playing")
    guest = await websockets.connect("wss://127.0.0.1:8443/ws", ssl=LAX, max_size=2**22)
    await guest.send(json.dumps({"t":"join","token":TOKEN,"pin":PIN,"name":"Nobody",
                                 "input":"only","codecs":["H264"]}))
    g = await wait(guest, ("joined","error")) or {}
    before = g.get("hold") or {}
    check(before.get("held") is True, "they are held: %r" % before)
    check(pick["label"] in (before.get("because") or ""),
          "and told which game: %r" % before.get("because"))
    g2 = await wait(guest, ("games",), 2.0)
    await guest.send(json.dumps({"t":"games"}))
    g2 = await wait(guest, ("games",))
    check(not [x for x in g2["games"] if x.get("system","").lower() == "steam"],
          "and cannot see it in the list")

    print("\nnow they log in to an account that has it")
    await guest.send(json.dumps({"t":"login","name":ACCOUNT,"password":PW,"code":code(1)}))
    # Everything that comes back, not the first thing that matches: the hold
    # is queued before the loggedin reply, so waiting for one throws the other
    # away.
    heard = []
    end = time.time() + 8.0
    while time.time() < end:
        try:
            heard.append(json.loads(await asyncio.wait_for(
                guest.recv(), timeout=max(.05, end - time.time()))))
        except asyncio.TimeoutError:
            break
        if any(m.get("t") == "loggedin" for m in heard) and \
           any(m.get("t") == "hold" for m in heard):
            break
    got = next((m for m in heard if m.get("t") == "loggedin"), None)
    check(got is not None, "logged in: %r" % [m.get("t") for m in heard])
    pushed = next((m for m in heard if m.get("t") == "hold"), None)
    check(pushed is not None,
          "the host pushes a fresh hold state, unasked: %r"
          % [m.get("t") for m in heard])
    check(pushed and pushed.get("held") is False,
          "saying the controller is live: %r" % pushed)

    print("\nand ending it")
    await admin.send(json.dumps({"t":"endgame"}))
    print("   endgame:", json.dumps(await wait(admin, ("launchresult",), 40.0)))
    await guest.close(); await admin.close()
asyncio.run(main())
print()
print("%d FAILED" % len(fails) if fails else "all good")
sys.exit(1 if fails else 0)
