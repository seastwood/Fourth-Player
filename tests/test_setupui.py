"""The setup page, and the one thing keeping everyone else out of it.

This page can create an account and issue an authenticator secret -- the two
things the guest page refuses to do at any privilege, because a second factor
cannot authorise issuing a second factor. What makes that safe is not a
password: it is that the page is on the loopback and wants a per-run token
written where only this user can read it.

So the door is what is tested. A page like this reachable without the token,
or reachable from the network, would be worse than no page at all.
"""
import asyncio
import json
import os
import socket
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


try:
    from fourthplayer import accounts, setupui
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)

# An accounts file of this test's own.
#
# The same lesson test_control.py learned: a test that reads the machine's real
# state passes on a workstation and fails on a host that is actually being
# used. This one asks "is there an account yet", and on a host where somebody
# has made one the answer is yes -- so the bootstrap checks below failed on
# ultra while passing here. Worse, a test of account handling that wrote to the
# real file would be editing somebody's logins.
import tempfile

_own = tempfile.mkdtemp(prefix="fp-setup-test-")
accounts.STORE = os.path.join(_own, "accounts.json")
OWNER = "owner"
PASSWORD = "a-long-enough-one"


class StubServer:
    """Just enough Server for the page to draw itself."""

    class _Cfg:
        hardware_encode = True

    cfg = _Cfg()
    session = None

    def _status(self):
        return {"ok": True, "open": False}


def request(port, target, body=None, cookie=None, timeout=5):
    """One HTTP request, over a plain socket.

    Not urllib: it honours http_proxy from the environment, and on a machine
    with one set every request to the loopback goes to the proxy instead and
    times out. Testing an HTTP server through a client that might route
    elsewhere is a poor way to find out what the server said.
    """
    head = ["%s %s HTTP/1.1" % ("POST" if body is not None else "GET", target),
            "Host: 127.0.0.1", "Connection: close"]
    if cookie:
        head.append("Cookie: " + cookie)
    if body is not None:
        head.append("Content-Length: %d" % len(body))
    raw = ("\r\n".join(head) + "\r\n\r\n").encode()
    if body:
        raw += body
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        sock.sendall(raw)
        out = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            out += chunk
    finally:
        sock.close()
    head_raw, _, payload = out.partition(b"\r\n\r\n")
    lines = head_raw.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ")[1])
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return status, headers, payload


async def main():
    page = setupui.SetupUI(StubServer())
    server = await page.start()
    loop = asyncio.get_running_loop()
    ask = lambda *a: loop.run_in_executor(None, request, page.port, *a)
    try:
        print("the first lock: a token only this user can read")
        status, _h, _b = await ask("/")
        check(status == 403, "no token at all is refused: %d" % status)
        status, _h, _b = await ask("/?t=nope")
        check(status == 403, "a wrong token is refused: %d" % status)
        # The same answer either way, saying nothing about which half was wrong.
        status, _h, body = await ask("/?t=" + page.token)
        check(status == 200 and b"Fourth Player" in body,
              "the right one is let in: %d, %d bytes" % (status, len(body)))
        check(page.refused == 2, "and the refusals are counted: %d" % page.refused)

        print("\nthe token moves into a cookie")
        _status, headers, _body = await ask("/?t=" + page.token)
        setcookie = headers.get("set-cookie") or ""
        check("fp_setup=" in setcookie, "a cookie is set: %r" % setcookie[:40])
        check("HttpOnly" in setcookie and "SameSite=Strict" in setcookie,
              "HttpOnly and SameSite=Strict, so no script or other page reaches it")

        print("\nbefore anybody has an account, only making one answers")
        # The window this closes: the page used to be fully open until somebody
        # got round to creating an account. However short that is, it is a
        # window in which this page will create accounts, issue authenticator
        # secrets and end sessions for whoever asks.
        check(page.first_run(), "with no accounts, this is a first run")
        for shut in ("/api/state", "/api/control", "/api/stream", "/api/qr",
                     "/api/account/reset2fa", "/api/account/remove"):
            status, _h, body = await ask(shut, b"{}", "fp_setup=" + page.token)
            check(status == 403 and json.loads(body).get("first_run"),
                  "%s is refused until there is an administrator: %d"
                  % (shut, status))

        status, _h, body = await ask(
            "/api/account/add",
            json.dumps({"name": OWNER, "password": PASSWORD}).encode(),
            "fp_setup=" + page.token)
        made = json.loads(body)
        check(status == 200 and made.get("ok"),
              "making the administrator is allowed: %s" % made.get("error"))
        check(made.get("secret") and made.get("uri"),
              "and it hands back the authenticator secret, once")
        # It holds everything, because the next thing that happens is this
        # account being asked to sign in and then run the machine. `admin add`
        # on the command line gives the first account only `grant`, which is a
        # fine default for somebody who left a list out and a trap here.
        check(set(made.get("can") or []) == set(accounts.CAPABILITIES),
              "and holds every capability: %s" % " ".join(made.get("can") or []))

        print("\nand once it exists the first run is over")
        check(not page.first_run(), "this is no longer a first run")
        status, _h, body = await ask("/api/state", b"{}", "fp_setup=" + page.token)
        check(status == 401 and json.loads(body).get("signin"),
              "the token alone no longer opens it: %d" % status)
        # The form still has to draw, or there is no way back in -- and all of
        # it still needs the token, so this is the login rather than an
        # unauthenticated surface.
        for open_path in ("/", "/setup.css", "/setup.js"):
            status, _h, _b = await ask(open_path + "?t=" + page.token)
            check(status == 200, "%s is still served: %d" % (open_path, status))
        status, _h, _b = await ask("/setup.js")
        check(status == 403, "but not without the token: %d" % status)

        print("\na wrong sign-in says nothing useful")
        status, _h, body = await ask(
            "/api/signin",
            json.dumps({"name": OWNER, "password": "not-the-password",
                        "code": "000000"}).encode(),
            "fp_setup=" + page.token)
        answer = json.loads(body)
        check(not answer.get("ok"), "the wrong password is refused")
        check("name, password or code" in (answer.get("error") or ""),
              "with one answer for all three: %r" % answer.get("error"))
        check(page.bad_signins == 1, "and it is counted: %d" % page.bad_signins)

        print("\nand the right one is let in")
        code = accounts.code_at(made["secret"], int(time.time()) // 30)
        status, headers, body = await ask(
            "/api/signin",
            json.dumps({"name": OWNER, "password": PASSWORD,
                        "code": code}).encode(),
            "fp_setup=" + page.token)
        answer = json.loads(body)
        check(answer.get("ok") and answer.get("who") == OWNER,
              "name, password and the six digits: %s" % answer.get("error"))
        signin = ""
        for part in (headers.get("set-cookie") or "").split(";"):
            if part.strip().startswith("fp_signin="):
                signin = part.strip()
        check(signin, "and a sign-in cookie comes back")
        both = "fp_setup=%s; %s" % (page.token, signin)

        status, _h, body = await ask("/api/state", b"{}", both)
        check(status == 200 and json.loads(body).get("ok"),
              "and now the page answers: %d" % status)

        print("\nthe same code cannot be used twice")
        # accounts.verify writes down the step a code was accepted for, which
        # is the reason it takes the whole account rather than a password
        # checker: a verification that records nothing is one an attacker may
        # repeat.
        status, _h, body = await ask(
            "/api/signin",
            json.dumps({"name": OWNER, "password": PASSWORD,
                        "code": code}).encode(),
            "fp_setup=" + page.token)
        check(not json.loads(body).get("ok"),
              "the code that just worked is refused the second time")

        print("\nand it is not on the network")
        addresses = {s.getsockname()[0] for s in server.sockets}
        check(addresses == {"127.0.0.1"},
              "listening only on the loopback: %s" % ", ".join(sorted(addresses)))
        lan = None
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("192.0.2.1", 9))      # routes nowhere, reveals the local ip
            lan = probe.getsockname()[0]
            probe.close()
        except OSError:
            pass
        if lan and lan != "127.0.0.1":
            try:
                reached = socket.create_connection((lan, page.port), timeout=2)
                reached.close()
                check(False, "this machine's own address %s reaches it" % lan)
            except OSError:
                check(True, "this machine's own address %s does not reach it" % lan)
        else:
            print("  ----   no non-loopback address here to try")

        print("\nand it serves only what is in web/")
        for attempt in ("/../fourthplayer/accounts.py",
                        "/..%2ffourthplayer/accounts.py", "/../../etc/passwd"):
            status, _h, _b = await ask(attempt + "?t=" + page.token, None, both)
            check(status == 404, "%s is refused: %d" % (attempt, status))
    finally:
        server.close()
        await server.wait_closed()

asyncio.run(main())

import shutil
shutil.rmtree(_own, ignore_errors=True)

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
