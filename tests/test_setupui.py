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

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


try:
    from fourthplayer import setupui
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)


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
    try:
        loop = asyncio.get_running_loop()

        print("the door")
        status, _h, _b = await loop.run_in_executor(None, request, page.port, "/")
        check(status == 403, "no token at all is refused: %d" % status)
        status, _h, _b = await loop.run_in_executor(
            None, request, page.port, "/?t=nope")
        check(status == 403, "a wrong token is refused: %d" % status)
        # Same answer either way, saying nothing about which half was wrong.
        status, _h, body = await loop.run_in_executor(
            None, request, page.port, "/?t=" + page.token)
        check(status == 200 and b"Fourth Player" in body,
              "the right one is let in: %d, %d bytes" % (status, len(body)))
        check(page.refused == 2, "and the refusals are counted: %d" % page.refused)

        print("\nthe token moves into a cookie")
        # So it is not in every later address, the title bar, or whatever the
        # browser syncs.
        _status, headers, _body = await loop.run_in_executor(
            None, request, page.port, "/?t=" + page.token)
        setcookie = headers.get("set-cookie") or ""
        check("fp_setup=" in setcookie, "a cookie is set: %r" % setcookie[:40])
        check("HttpOnly" in setcookie and "SameSite=Strict" in setcookie,
              "HttpOnly and SameSite=Strict, so no script or other page reaches it")
        status, _h, body = await loop.run_in_executor(
            None, request, page.port, "/api/state", b"{}",
            "fp_setup=" + page.token)
        check(status == 200 and json.loads(body).get("ok"),
              "and the cookie alone works on the api: %d" % status)

        print("\nand it is not on the network")
        # The one that would matter most. Bound to 127.0.0.1, so the machine's
        # own LAN address must refuse.
        addresses = {s.getsockname()[0] for s in server.sockets}
        check(addresses == {"127.0.0.1"},
              "listening only on the loopback: %s" % ", ".join(sorted(addresses)))
        lan = None
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("192.0.2.1", 9))          # routes nowhere, reveals the local ip
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

        print("\nthe second lock: an account, once there is one")
        # The token says where you are; an account says who. Both, once there
        # is an account to ask for -- and nothing at all until then, because
        # the first account is made through this page.
        check(not page.needs_signin(),
              "with no accounts, nothing to ask for: the token alone opens it")
        status, _h, body = await loop.run_in_executor(
            None, request, page.port, "/api/state", b"{}",
            "fp_setup=" + page.token)
        check(status == 200 and json.loads(body).get("ok"),
              "and the page works, which is the bootstrap")

        import types
        # One account, without touching the real accounts file.
        page.needs_signin = types.MethodType(lambda _self: True, page)
        status, _h, body = await loop.run_in_executor(
            None, request, page.port, "/api/state", b"{}",
            "fp_setup=" + page.token)
        answer = json.loads(body)
        check(status == 401 and answer.get("signin"),
              "the moment an account exists, the token alone is refused: %d"
              % status)

        # The form still has to be reachable, or there is no way back in.
        for open_path in ("/", "/setup.css", "/setup.js"):
            status, _h, _b = await loop.run_in_executor(
                None, request, page.port, open_path + "?t=" + page.token)
            check(status == 200, "%s is still served, so the form can draw: %d"
                  % (open_path, status))
        # But only with the token. The login is not an unauthenticated surface.
        status, _h, _b = await loop.run_in_executor(None, request, page.port, "/")
        check(status == 403, "and not without the token: %d" % status)

        print("\nand a wrong sign-in says nothing useful")
        status, _h, body = await loop.run_in_executor(
            None, request, page.port, "/api/signin",
            json.dumps({"name": "nobody", "password": "x" * 12,
                        "code": "000000"}).encode(),
            "fp_setup=" + page.token)
        answer = json.loads(body)
        check(not answer.get("ok"), "a made-up account is refused")
        check("name, password or code" in (answer.get("error") or ""),
              "with one answer for all three, so guessing learns nothing: %r"
              % answer.get("error"))
        check(page.bad_signins == 1, "and it is counted: %d" % page.bad_signins)

        # Back to the real answer, so what follows tests the file handler
        # rather than the sign-in gate in front of it. Left patched, the
        # traversal checks below pass for the wrong reason -- refused at the
        # door instead of refused by the path check -- which is a test that
        # would go on passing if the path check were deleted.
        del page.needs_signin

        print("\nand it serves only what is in web/")
        for attempt in ("/../fourthplayer/accounts.py", "/..%2ffourthplayer/accounts.py",
                        "/../../etc/passwd"):
            status, _h, _b = await loop.run_in_executor(
                None, request, page.port, attempt + "?t=" + page.token)
            check(status == 404, "%s is refused: %d" % (attempt, status))
    finally:
        server.close()
        await server.wait_closed()

asyncio.run(main())

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
