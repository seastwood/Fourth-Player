"""The setup page: everything that has to be done at the console, in a browser.

Fourth Player's guest page deliberately cannot create an account or issue an
authenticator secret. The README says why, and it is right: "issuing a second
factor is not something a second factor can authorise". Until now that meant a
command line, which is a poor place to read a QR code from and a worse one to
explain to somebody setting a machine up for the first time.

So this is a second server, and the whole of its security is one idea: it
listens on the loopback only, and it will not answer without a token that was
written where only this user can read it. Being able to reach it *is* being at
the console, which is the same claim `admin add` at a terminal makes. Nothing
here is reachable from the network at any setting, because it is never bound
to one.

Three things follow from that and are worth stating plainly:

  * There is no login. The token is the credential, and asking for a password
    as well would be asking somebody to prove twice that they are sitting at
    their own machine.
  * The token is per-run. Restarting the host invalidates every old URL,
    including one left open in a browser tab, which is what should happen.
  * An administrator on the machine can read the token file. So can anything
    running as this user. That is true of a root shell and a terminal too --
    this is the same boundary the command line has, not a weaker one.
"""
import asyncio
import json
import logging
import mimetypes
import os
import secrets
import time

from . import accounts

log = logging.getLogger("fourthplayer.setup")

HERE = os.path.dirname(os.path.realpath(__file__))
WEB = os.path.join(os.path.dirname(HERE), "web")

# Read per request rather than cached, the same way the guest page is: editing
# a file and reloading should be the whole of the change.
PAGE = "setup.html"


class SetupUI:
    """A loopback-only HTTP server for the things the browser must not do."""

    def __init__(self, server):
        self.server = server              # the Server, for session commands
        self.token = secrets.token_urlsafe(32)
        self.port = None
        self._socket = None
        # What the page is told about attempts to reach it without the token.
        # Kept here rather than logged only, because "somebody is knocking"
        # is exactly the sort of thing a troubleshooting page should show.
        self.refused = 0
        self.started = time.time()

    # -- lifecycle ---------------------------------------------------------

    async def start(self):
        self._socket = await asyncio.start_server(
            self._client, host="127.0.0.1", port=0)
        self.port = self._socket.sockets[0].getsockname()[1]
        log.info("setup page at http://127.0.0.1:%d/ (token in the address the "
                 "tray icon and `fourth-player setup` open)", self.port)
        return self._socket

    def url(self):
        return "http://127.0.0.1:%d/?t=%s" % (self.port or 0, self.token)

    # -- a small HTTP server ------------------------------------------------
    #
    # Its own rather than the websockets one the guest page rides on, for one
    # reason: process_request never sees a request body, so nothing could be
    # POSTed to it -- and the alternative, putting a new password in a query
    # string, is exactly the mistake `admin add` refuses to allow on a command
    # line. It is small because it only ever talks to a page served from the
    # same process.

    MAX_HEAD = 16 * 1024
    MAX_BODY = 256 * 1024

    async def _client(self, reader, writer):
        try:
            head = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), timeout=10)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError, ConnectionError):
            writer.close()
            return
        try:
            await self._answer(head, reader, writer)
        except ConnectionError:
            pass
        except Exception:
            log.exception("setup page failed")
            await self._send(writer, 500, "text/plain", b"something went wrong\n")
        finally:
            writer.close()

    async def _answer(self, head, reader, writer):
        lines = head.decode("latin-1").split("\r\n")
        try:
            method, target, _version = lines[0].split(" ", 2)
        except ValueError:
            await self._send(writer, 400, "text/plain", b"bad request\n")
            return
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            if name:
                headers[name.strip().lower()] = value.strip()

        if not self._authorised(target, headers):
            self.refused += 1
            # The same answer for a missing token and a wrong one, saying
            # nothing about what was expected.
            await self._send(writer, 403, "text/plain; charset=utf-8",
                             b"Open this page from the Fourth Player tray "
                             b"icon, or run `fourth-player setup`.\n")
            return

        body = b""
        length = int(headers.get("content-length") or 0)
        if length > self.MAX_BODY:
            await self._send(writer, 413, "text/plain", b"too much\n")
            return
        if length:
            try:
                body = await asyncio.wait_for(reader.readexactly(length),
                                              timeout=10)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError):
                return

        route = target.split("?", 1)[0]
        if route.startswith("/api/"):
            await self._api(route, body, writer)
        else:
            await self._file(route, writer)

    async def _send(self, writer, status, kind, body, extra=()):
        reason = {200: "OK", 400: "Bad Request", 403: "Forbidden",
                  404: "Not Found", 413: "Payload Too Large",
                  500: "Internal Server Error"}.get(status, "OK")
        head = ["HTTP/1.1 %d %s" % (status, reason),
                "content-type: " + kind,
                "content-length: %d" % len(body),
                "cache-control: no-store",
                # It is a local page with no business being framed, embedded
                # or reached from anything else on the machine.
                "x-content-type-options: nosniff",
                "x-frame-options: DENY",
                "connection: close"]
        head.extend(extra)
        writer.write(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))
        writer.write(body)
        await writer.drain()

    def _authorised(self, target, headers):
        """The token, from the query string or the cookie it was put in.

        compare_digest, because the difference between wrong-at-the-first-byte
        and wrong-at-the-last is a way to find a token one byte at a time.
        """
        query = target.split("?", 1)[1] if "?" in target else ""
        for part in query.split("&"):
            if part.startswith("t=") and secrets.compare_digest(part[2:],
                                                                self.token):
                return True
        for part in (headers.get("cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == "fp_setup" and secrets.compare_digest(value, self.token):
                return True
        return False

    async def _file(self, route, writer):
        name = PAGE if route in ("/", "") else route.lstrip("/")
        # One flat directory, and the name has to live in it.
        safe = os.path.normpath(os.path.join(WEB, name))
        if not safe.startswith(WEB + os.sep) or not os.path.isfile(safe):
            await self._send(writer, 404, "text/plain", b"no such page\n")
            return
        with open(safe, "rb") as handle:
            body = handle.read()
        kind = mimetypes.guess_type(safe)[0] or "application/octet-stream"
        extra = []
        if name == PAGE:
            # The token moves from the address into a cookie on the first
            # load, so it is not in every later request, not in the title bar,
            # and not in whatever the browser decides to sync.
            extra.append("set-cookie: fp_setup=%s; Path=/; SameSite=Strict; "
                         "HttpOnly" % self.token)
        await self._send(writer, 200, kind, body, extra)

    # -- the api -----------------------------------------------------------

    async def _api(self, route, raw, writer):
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            await self._send(writer, 400, "application/json",
                             b'{"ok": false, "error": "unreadable request"}\n')
            return
        handler = getattr(
            self, "_api_" + route[len("/api/"):].strip("/").replace("/", "_"),
            None)
        if handler is None:
            await self._send(writer, 404, "application/json",
                             b'{"ok": false, "error": "no such endpoint"}\n')
            return
        try:
            answer = await handler(body)
        except Exception as exc:
            log.exception("setup api %s failed", route)
            answer = {"ok": False, "error": str(exc)}
        await self._send(writer, 200, "application/json",
                         (json.dumps(answer) + "\n").encode())

    async def _api_state(self, _body):
        """Everything the page draws, in one request."""
        from . import video
        session = self.server.session
        picked = {}
        try:
            source = video.pick_source()
            sound = video.pick_sound()
            encoder = video.pick_encoder(
                "h264", self.server.cfg.hardware_encode)
            picked = {
                "capture": source[0] if source else None,
                "sound": sound[0] if sound else None,
                "encoder": encoder[0] if encoder else None,
                "encoder_kind": encoder[1] if encoder else None,
            }
        except Exception as exc:                   # pragma: no cover
            picked = {"error": str(exc)}
        return {
            "ok": True,
            "accounts": [
                {"name": a.get("name"),
                 "can": list(a.get("can") or []),
                 "devices": len(a.get("devices") or []),
                 "last_seen": a.get("last_seen")}
                for a in accounts.all_accounts()
            ],
            "capabilities": list(accounts.CAPABILITIES),
            "primary": (accounts.primary() or {}).get("name"),
            # The same summary `status` prints, from the same method, so the
            # page and the command line can never disagree about what is open.
            "session": self.server._status() if session else None,
            "picked": picked,
            "refused": self.refused,
        }

    # -- accounts ----------------------------------------------------------
    #
    # The reason this page exists. Everything below is deliberately absent
    # from the guest page: an authenticator secret cannot be issued by
    # somebody holding an authenticator, so it is issued here, where being
    # able to ask at all means being at the machine.

    async def _api_account_add(self, body):
        name = (body.get("name") or "").strip()
        password = body.get("password") or ""
        can = [c for c in (body.get("can") or []) if c]
        if len(password) < 8:
            return {"ok": False, "error": "A password needs to be at least "
                                          "eight characters."}
        for capability in can:
            accounts.check_capability(capability)
        account, secret = accounts.add(name, password, can)
        log.info("account %s created from the setup page", account["name"])
        # The one time it is ever returned. Nothing stores it in a form this
        # could read a second time, which is the property being relied on.
        return {"ok": True, "name": account["name"],
                "can": list(account.get("can") or []),
                "secret": secret,
                "uri": accounts.otpauth(account["name"], secret)}

    async def _api_account_reset2fa(self, body):
        name = (body.get("name") or "").strip()
        _account, secret = accounts.reset_totp(name)
        log.info("a new authenticator secret was issued for %s, and every "
                 "remembered device signed out", name)
        return {"ok": True, "name": name, "secret": secret,
                "uri": accounts.otpauth(name, secret)}

    async def _api_account_can(self, body):
        name = (body.get("name") or "").strip()
        can = [c for c in (body.get("can") or []) if c]
        for capability in can:
            accounts.check_capability(capability)
        accounts.set_capabilities(name, can)
        return {"ok": True, "name": name, "can": can}

    async def _api_account_passwd(self, body):
        name = (body.get("name") or "").strip()
        password = body.get("password") or ""
        if len(password) < 8:
            return {"ok": False, "error": "A password needs to be at least "
                                          "eight characters."}
        accounts.set_password(name, password)
        return {"ok": True, "name": name}

    async def _api_account_remove(self, body):
        name = (body.get("name") or "").strip()
        if accounts.is_primary(name):
            # The account that can never be locked out is also the one that
            # must not be deleted by accident from a page with no password on
            # it. Removing it is a command-line decision.
            return {"ok": False,
                    "error": "%s is the primary account -- the one a session "
                             "lock can never shut out. Remove it from the "
                             "command line if you really mean to." % name}
        accounts.remove(name)
        return {"ok": True, "name": name}

    async def _api_account_forget_devices(self, body):
        name = (body.get("name") or "").strip()
        accounts.forget_devices(name)
        return {"ok": True, "name": name}
