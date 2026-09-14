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

  * The token proves *where* you are; an account proves *who* you are, and
    both are required once there is an account to ask for. I argued at first
    that a login here was asking somebody to prove twice they were at their
    own machine, and that was wrong in a way worth writing down: a token can
    leak through browser history, a screen share or a terminal somebody
    scrolled back through, and a machine left unlocked is exactly the case
    this page should survive. Two different kinds of proof, not the same one
    twice.

  * Until the first account exists there is nothing to log in with, so the
    token alone opens it -- and it says so on the page. That is the bootstrap
    and it closes itself: the moment an account exists, this asks for one.
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


def _launch_policies():
    try:
        from .session import LAUNCH_POLICIES
        return LAUNCH_POLICIES
    except Exception:
        return ()

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
        # Signed-in browsers: cookie -> (account name, when it expires). Kept
        # in memory only, so restarting the host signs everybody out -- which
        # is the same thing the per-run token already does.
        self.signins = {}
        # Wrong passwords, for the troubleshooting page. Counted rather than
        # only logged: "it says my password is wrong" and "nothing has reached
        # the host" look identical from the other side of a screen.
        self.bad_signins = 0

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

    # -- who is asking ------------------------------------------------------

    SIGNIN_HOURS = 12

    def needs_signin(self):
        """Whether there is an account to ask for yet."""
        try:
            return bool(accounts.all_accounts())
        except Exception:
            # An unreadable accounts file must not become a way in.
            return True

    def signed_in(self, headers):
        for part in (headers.get("cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name != "fp_signin" or not value:
                continue
            for known, (who, until) in list(self.signins.items()):
                if time.time() > until:
                    self.signins.pop(known, None)
                    continue
                if secrets.compare_digest(value, known):
                    return who
        return None

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

        # The second lock. The token says this request came from someone who
        # can read a file only this user can read; an account says who they
        # are. Both, once there is an account to ask for.
        #
        # Three things stay open, and each has to be: the page itself (it
        # draws the sign-in form), its stylesheet and script (it cannot draw
        # without them), and /api/signin (it is how you get in). None of them
        # says anything about the host -- and all of them still need the
        # token, so this is not an unauthenticated surface, it is the login.
        public = route in ("/", "", "/" + PAGE, "/setup.css", "/setup.js",
                           "/api/signin", "/api/whoami")
        if not public and self.needs_signin() and not self.signed_in(headers):
            await self._send(writer, 401, "application/json",
                             b'{"ok": false, "error": "sign in first", '
                             b'"signin": true}\n')
            return

        if route.startswith("/api/"):
            await self._api(route, body, writer, headers)
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

    async def _api(self, route, raw, writer, headers=None):
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
        extra = []
        try:
            if route == "/api/signin":
                answer, extra = self._signin(body)
            else:
                answer = await handler(body)
                if route == "/api/whoami":
                    answer["who"] = self.signed_in(headers or {})
        except Exception as exc:
            log.exception("setup api %s failed", route)
            answer = {"ok": False, "error": str(exc)}
        await self._send(writer, 200, "application/json",
                         (json.dumps(answer) + "\n").encode(), extra)

    def _signin(self, body):
        """Name, password and the six digits, checked the way the guest page
        checks them -- accounts.verify, which also writes down the step a code
        was used for so the same code cannot be presented twice."""
        if not self.needs_signin():
            return {"ok": True, "who": None, "bootstrap": True}, []
        name = (body.get("name") or "").strip()
        account = accounts.verify(name, body.get("password") or "",
                                  body.get("code") or "")
        if account is None:
            self.bad_signins += 1
            log.warning("a sign-in to the setup page was refused (%d so far)",
                        self.bad_signins)
            # One answer for a wrong name, a wrong password and a wrong code,
            # so guessing tells the guesser nothing about which was wrong.
            return {"ok": False,
                    "error": "That name, password or code is not right."}, []
        cookie = secrets.token_urlsafe(32)
        self.signins[cookie] = (account["name"],
                                time.time() + self.SIGNIN_HOURS * 3600)
        log.info("%s signed in to the setup page", account["name"])
        return ({"ok": True, "who": account["name"]},
                ["set-cookie: fp_signin=%s; Path=/; SameSite=Strict; HttpOnly"
                 % cookie])

    async def _api_whoami(self, _body):
        """Whether anybody needs to sign in, and whether this browser has."""
        return {"ok": True, "needs_signin": self.needs_signin()}

    async def _api_signout(self, _body):
        return {"ok": True}

    async def _api_signin(self, _body):
        """Handled in _api, which is where the cookie can be set."""
        return {"ok": False, "error": "unreachable"}

    async def _api_state(self, _body):
        """Everything the page draws, in one request.

        Every part of it is guarded separately, and that is the whole design
        rather than caution. This is the page somebody opens *because*
        something is wrong: a host with no GStreamer, a broken plugin, a
        video.py that will not even import. A status page that cannot draw
        itself when the thing it reports on is broken is a status page for
        the days you do not need one.
        """
        session = self.server.session
        picked = {}
        try:
            from . import video
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
        try:
            listed = [
                {"name": a.get("name"),
                 "can": list(a.get("can") or []),
                 "devices": len(a.get("devices") or []),
                 "last_seen": a.get("last_seen")}
                for a in accounts.all_accounts()
            ]
            trouble = None
        except Exception as exc:
            listed, trouble = [], "the accounts file could not be read: %s" % exc
        try:
            summary = self.server._status() if session else None
        except Exception as exc:
            summary = {"error": str(exc)}
        return {
            "ok": True,
            "trouble": trouble,
            "accounts": listed,
            "capabilities": list(accounts.CAPABILITIES),
            "primary": (accounts.primary() or {}).get("name"),
            # The same summary `status` prints, from the same method, so the
            # page and the command line can never disagree about what is open.
            "session": summary,
            "picked": picked,
            "refused": self.refused,
            "diagnostics": self._diagnostics(),
            "needs_signin": self.needs_signin(),
            "stream": self._stream_now(),
            # The page offers these rather than inventing its own list, so a
            # policy added to the host appears here without a second edit.
            "policies": list(_launch_policies()),
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

    # -- everything the command line can do --------------------------------

    async def _api_control(self, body):
        """Forward one command to the server's own control handler.

        Deliberately a forwarder rather than an endpoint per command. The
        control channel already carries the whole operating surface -- start,
        stop, extend, reshare, pin, slots, limit, lock, link, url, policy,
        share, kick, approve, deny, stream -- and writing each of them out
        again here would be a second list to keep in agreement with the first,
        which is how a page ends up quietly missing the one setting somebody
        needs.

        It is not a widening of anything. Reaching this page at all means
        holding a token written where only this user can read it, and that
        user can already run `fourth-player stop` from a terminal. What it
        forwards to is the same method the control socket calls, in the same
        process, so there is no second implementation to disagree.

        `quit` is refused here on purpose -- see the note on it below.
        """
        command = (body.get("cmd") or "").strip()
        if not command:
            return {"ok": False, "error": "no command given"}
        if command == "quit":
            # Not because it would be dangerous -- the tray does exactly this
            # -- but because a page that shuts down the server it is served by
            # cannot report what happened, and leaves a browser tab that looks
            # broken rather than finished. The tray asks, and can say so.
            return {"ok": False,
                    "error": "stopping the host is done from the tray icon, "
                             "which can still tell you what happened "
                             "afterwards"}
        request = dict(body)
        request["cmd"] = command
        return await self.server._command(request)

    async def _api_qr(self, body):
        """The authenticator URI as something a camera can read.

        Drawn here rather than by a library pulled into the page: the host
        already has the one the command line uses, and a setup page on a
        machine with no internet should not need to fetch anything to show a
        QR code -- which is the one moment it absolutely must work.

        invert=True for the same reason `admin add` uses it: dark-on-light is
        what a phone camera expects, and a light-on-dark code is a
        photographic negative most scanners refuse.
        """
        text = body.get("text") or ""
        if not text:
            return {"ok": False, "error": "nothing to draw"}
        try:
            import io
            import qrcode
        except ImportError:
            return {"ok": False, "error": "python3-qrcode is not installed"}
        drawn = qrcode.QRCode(border=2)
        drawn.add_data(text)
        out = io.StringIO()
        drawn.print_ascii(out=out, invert=True)
        return {"ok": True, "art": out.getvalue()}

    async def _api_diagnostics(self, _body):
        return {"ok": True, **self._diagnostics()}

    def _diagnostics(self):
        """What somebody would otherwise have to ask three programs for.

        Each piece guarded on its own, for the reason _api_state is: this is
        the page you open when something is wrong, so it has to draw on a
        machine where things are.
        """
        import platform
        out = {}
        try:
            out["platform"] = "%s %s" % (platform.system(), platform.release())
            out["python"] = platform.python_version()
        except Exception:
            pass
        try:
            from .codes import HAVE_EVDEV
            from .virtual import BACKEND
            out["pads"] = "%s (%s codes)" % (
                BACKEND, "evdev" if HAVE_EVDEV else "built-in table")
        except Exception as exc:
            out["pads"] = "unavailable: %s" % exc
        try:
            from gi.repository import Gst
            out["gstreamer"] = Gst.version_string()
        except Exception as exc:
            out["gstreamer"] = "unavailable: %s" % exc
        out["uptime"] = time.time() - self.started
        # What has been turned away, which is the other half of troubleshooting:
        # "it says the PIN is wrong" and "nothing has even reached the host"
        # look identical from a guest's side.
        server = self.server
        out["bad_pins"] = getattr(server, "refused_pins", None)
        out["bad_logins"] = getattr(server, "refused_logins", None)
        out["bad_setup_signins"] = self.bad_signins
        return out

    # -- the picture -------------------------------------------------------

    async def _api_stream(self, body):
        """Change how the picture is sent.

        Not through /api/control, because `stream` is not a control-channel
        command at all -- it arrives over the guest socket from an account
        holding the `stream` capability. This calls the same session method
        that handler does, so the bounds, the "only what changed" rule and the
        recapture are one implementation rather than two.

        `by=None`: the capability check belongs to the socket, where the
        question is which account is asking. Here the question is already
        answered by being able to reach this page at all.
        """
        session = self.server.session
        if session is None or not session.open:
            return {"ok": False, "error": "no session is open"}
        try:
            return await session.set_stream(body.get("settings") or {}, by=None)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def _stream_now(self):
        """What the picture is set to, and what it may be set to."""
        cfg = self.server.cfg
        out = {
            "height": getattr(cfg, "height", None),
            "width": getattr(cfg, "width", None),
            "fps": getattr(cfg, "fps", None),
            "bitrate_kbps": getattr(cfg, "bitrate_kbps", None),
            "codec": getattr(cfg, "codec", None),
        }
        session = self.server.session
        try:
            from .session import LiveSession
            out["limits"] = {k: list(v) for k, v in LiveSession.STREAM_LIMITS.items()}
        except Exception:
            out["limits"] = {}
        out["can_apply"] = bool(session and session.open)
        return out
