"""Two sockets: one the internet may reach, and one it may not.

The public socket serves the join page and carries signalling. It is the only
thing that ever needs to be exposed, and everything it will do for an anonymous
caller is hand them a static file.

The control socket -- starting sessions, reading the roster, kicking people --
is a Unix domain socket in the user's runtime directory. That is not a check
that administration is local; it is a transport that cannot be anything else.
There is no header to forge and no address to spoof, and a misconfigured
reverse proxy cannot accidentally expose it.
"""

import asyncio
import json
import logging
import math
import mimetypes
import os
import re
import socket
import ssl
import urllib.parse
from http import HTTPStatus

import websockets

from . import invites
from .config import Config
from .session import LAUNCH_POLICIES, LiveSession
from .tls import ensure_certificate

log = logging.getLogger("fourthplayer.server")

# Not in the system table on every host, and a manifest served as
# octet-stream is ignored by the browser reading it.
mimetypes.add_type("application/manifest+json", ".webmanifest")


def _lan_address():
    """The address of the interface that reaches the default route."""
    import socket as _socket
    try:
        probe = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 1))       # TEST-NET-1, never routed
        address = probe.getsockname()[0]
        probe.close()
        return address
    except OSError:
        return "127.0.0.1"

# FP_WEB_ROOT lets a test serve a modified copy of the page from this same
# server, which is the only way to exercise the real client against real
# signalling: the WebSocket has to be same-origin.
WEB_ROOT = os.environ.get("FP_WEB_ROOT") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
CONTROL_SOCKET = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "fourth-player.sock")

# A guest is told what went wrong in the same words for every kind of failure
# that could be probing: a wrong PIN, an unknown link and an expired one all
# read alike, so guessing tells the guesser nothing about which half was wrong.
REFUSED = "That link or PIN is not valid."


class Server:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.loop = None
        self.session = None
        self._sockets = []
        self._outboxes = set()

    # -- public surface -----------------------------------------------------

    async def _http(self, path, request_headers):
        route = path.split("?", 1)[0]
        if route == "/ws":
            return None                      # let the WebSocket handshake run
        if route == "/mode":
            # What the join page needs to know before anybody has joined:
            # whether the link is required. It decides what the page says
            # about adding itself to a home screen, which is a different
            # answer depending. Reveals nothing a guess would not: trying to
            # join without a link answers the same question.
            body = json.dumps({"require_link": self.cfg.require_link}).encode()
            return HTTPStatus.OK, [("content-type", "application/json"),
                                   ("cache-control", "no-store")], body
        if route == "/healthz":
            return HTTPStatus.OK, [("content-type", "text/plain")], b"ok\n"
        if route == "/" or route.startswith("/j/"):
            return self._file("index.html")
        if route.startswith("/static/"):
            return self._file(route[len("/static/"):])
        if route.startswith("/art/"):
            return self._art(route[len("/art/"):])
        return HTTPStatus.NOT_FOUND, [], b"not found\n"

    def _file(self, relative):
        # Resolve and confirm the result is still inside the web root: the
        # alternative is trusting that no arrangement of dots escapes it.
        full = os.path.realpath(os.path.join(WEB_ROOT, relative))
        if not full.startswith(os.path.realpath(WEB_ROOT) + os.sep):
            return HTTPStatus.FORBIDDEN, [], b"no\n"
        if not os.path.isfile(full):
            return HTTPStatus.NOT_FOUND, [], b"not found\n"
        kind = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as handle:
            body = handle.read()
        return HTTPStatus.OK, [("content-type", kind),
                               ("cache-control", "no-store")], body

    def _art(self, key):
        """Box art for one game id.

        Served by id rather than by path on purpose: the id is the only name a
        guest ever has for a game, so this route cannot be asked for a file
        that is not artwork for something already in the catalogue.
        """
        if self.session is None or not self.session.open:
            return HTTPStatus.NOT_FOUND, [], b"not found\n"
        if not re.fullmatch(r"[0-9a-f]{1,64}", key or ""):
            return HTTPStatus.NOT_FOUND, [], b"not found\n"
        path = self.session.catalogue.art(key)
        if not path or not os.path.isfile(path):
            return HTTPStatus.NOT_FOUND, [], b"not found\n"
        try:
            with open(path, "rb") as handle:
                body = handle.read()
        except OSError:
            return HTTPStatus.NOT_FOUND, [], b"not found\n"
        # Box art does not change under a given id, and a phone scrolling two
        # hundred games should not fetch each one twice.
        return HTTPStatus.OK, [("content-type", "image/png"),
                               ("cache-control", "private, max-age=3600")], body

    def _address(self, socket_):
        """Who is calling, for rate-limiting purposes.

        X-Forwarded-For is trusted only when we were told we are behind a
        proxy. Trusting it unconditionally would let any caller pick their own
        rate-limit bucket by sending a header, which defeats the lockout
        entirely.
        """
        if self.cfg.behind_proxy:
            forwarded = socket_.request_headers.get("X-Forwarded-For", "")
            if forwarded:
                return forwarded.split(",")[-1].strip()
        peer = socket_.remote_address
        return peer[0] if peer else ""

    async def _guest(self, socket_, path):
        if path.split("?", 1)[0] != "/ws":
            await socket_.close(1008, "unexpected path")
            return

        guest = None
        outbox = asyncio.Queue()
        self._outboxes.add(outbox)
        writer = self.loop.create_task(self._drain(socket_, outbox))
        try:
            async for raw in socket_:
                try:
                    message = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                kind = message.get("t")

                if kind in ("join", "resume") and guest is None:
                    guest = await self._admit(socket_, message, outbox)
                elif guest is None:
                    await outbox.put({"t": "error", "message": REFUSED})
                elif kind == "answer":
                    sdp = message.get("sdp", "")
                    # Port 0 on the video section is a refusal. Worth a line in
                    # the log, because from the host everything else looks
                    # perfect: the peer connects, input flows, and the guest
                    # sits in front of a black screen.
                    if re.search(r"^m=video 0[ ]", sdp, re.M):
                        log.warning("%s: their browser refused the video "
                                    "-- freeing the slot", guest.label)
                        # They will never see a picture, so holding the slot
                        # only locks the next person out -- including this one
                        # after they reload. Give the page a moment to say what
                        # happened, then let go.
                        slot = guest.slot
                        self.loop.call_later(
                            5.0, lambda s=slot: self.session
                            and self.session.drop(s, reason="could not take the video"))
                    guest.peer.set_remote_answer(sdp)
                elif kind == "ice" and message.get("candidate"):
                    guest.peer.add_ice_candidate(
                        int(message.get("sdpMLineIndex") or 0), message["candidate"])
                elif kind == "games":
                    # The catalogue itself, which is public to anyone already
                    # in the session: labels, systems and player counts, and
                    # an id per game that means nothing anywhere else.
                    await outbox.put({
                        "t": "games",
                        "systems": self.session.catalogue.systems(),
                        "games": self.session.catalogue.listing(),
                        **self.session.launch_state(),
                    })
                elif kind == "launch":
                    result = await self.session.request_launch(
                        guest, str(message.get("game") or ""),
                        resume=bool(message.get("resume")))
                    await outbox.put({"t": "launchresult", **result})
                elif kind == "report":
                    # What the guest's own browser sees. The host can only
                    # know what it sent; whether any of it arrived is visible
                    # from one side only, and that side is usually a phone in
                    # somebody else's house.
                    log.info("%s reports: %s", guest.label,
                             str(message.get("detail", ""))[:300])
                elif kind == "renew" and guest is not None:
                    # Their network changed under them. Everything negotiated
                    # before refers to addresses that no longer exist.
                    log.info("%s: asked for a fresh media connection", guest.label)

                    def on_signal(sig, payload, box=outbox):
                        box.put_nowait({"t": sig, **payload})

                    try:
                        await self.session.renew(guest, on_signal)
                    except asyncio.TimeoutError:
                        await outbox.put({
                            "t": "error",
                            "message": "The host could not restart your video. "
                                       "Try again in a moment."})
                elif kind == "bye":
                    break
        except websockets.ConnectionClosed:
            pass
        finally:
            self._outboxes.discard(outbox)
            writer.cancel()
            # Do NOT tear the guest down here. This socket carries signalling;
            # the picture, the sound and the pad ride a WebRTC connection that
            # does not need it once established. Ending the session because
            # this closed meant a backgrounded tab, a moment of packet loss or
            # a missed keepalive silently killed a game that was working --
            # which is exactly what it did. A guest ends when their *media*
            # ends (Peer.on_dead), when they are kicked, or when time runs out.
            if guest is not None:
                if guest.outbox is outbox:
                    guest.outbox = None
                    guest.socket = None
                log.info("%s: signalling closed, media left alone", guest.label)

    async def _admit(self, socket_, message, outbox):
        address = self._address(socket_)
        if self.session is None or not self.session.open:
            await outbox.put({"t": "error", "message": "There is no session open."})
            return None
        try:
            if message.get("t") == "resume":
                guest = self.session.resume(message.get("guest", ""), socket_,
                                            message.get("name", ""))
                guest_token = message.get("guest", "")
            else:
                guest, guest_token = self.session.admit(
                    message.get("token", ""), str(message.get("pin", "")),
                    socket_, address, message.get("name", ""))
        except invites.LockedOut as exc:
            await outbox.put({"t": "error", "retry_after": round(exc.seconds),
                              "message": f"Too many tries. Wait {round(exc.seconds)}s."})
            return None
        except invites.SessionFull:
            # Somebody is standing at the door being told the room is full. Look
            # at the room: a slot held by a connection that stopped working is
            # not somebody playing, and after a network switch it looks exactly
            # like one until it is checked.
            freed = self.session.reap_now()
            if freed:
                log.info("freed %d slot(s) held by dead connections", freed)
                try:
                    guest, guest_token = self.session.admit(
                        message.get("token", ""), str(message.get("pin", "")),
                        socket_, address, message.get("name", ""))
                except invites.JoinError:
                    await outbox.put({"t": "error",
                                      "message": "Every player slot is taken."})
                    return None
            else:
                await outbox.put({"t": "error",
                                  "message": "Every player slot is taken."})
                return None
        except invites.JoinError:
            await outbox.put({"t": "error", "message": REFUSED})
            return None
        except asyncio.TimeoutError:
            # The pipeline worker is wedged behind a teardown that will not
            # finish. Saying so beats leaving them on "rejoining" forever --
            # and the slot has to go back, or a few timeouts fill the session
            # with people who never got a picture.
            log.error("timed out attaching a peer for %s; freeing the slot",
                      getattr(guest, "label", "a guest"))
            if guest is not None and self.session is not None:
                self.session.drop(guest.slot, reason="could not be given video")
            await outbox.put({"t": "error",
                              "message": "The host could not start your video. "
                                         "Try again in a moment."})
            return None

        # Route through whatever socket the guest currently holds, rather than
        # capturing this one: they may reconnect their signalling several times
        # over one media session.
        def on_signal(kind, payload):
            box = guest.outbox
            if box is not None:
                box.put_nowait({"t": kind, **payload})

        guest.outbox = outbox
        guest.socket = socket_

        # What this browser says it can decode. Settled before their peer is
        # built, because the offer has to describe what they will actually be
        # sent.
        try:
            await self.session.agree_codec(guest, message.get("codecs") or [])
        except Exception as exc:
            log.warning("could not settle on a codec (%s); carrying on", exc)

        keep_media = (message.get("t") == "resume"
                      and message.get("media") == "live"
                      and guest.peer is not None)
        if keep_media:
            # Their picture never stopped; they only lost the socket. Do not
            # renegotiate -- a new offer here would interrupt a working stream.
            guest.peer._on_signal = on_signal
            log.info("%s: signalling restored, stream untouched", guest.label)
        else:
            await self.session.attach_peer(guest, on_signal)

        await outbox.put({
            "t": "joined", "slot": guest.slot, "label": guest.label,
            "guest": guest_token,
            "remaining": None if self.session.unlimited
                         else round(self.session.remaining()),
            "resumed_media": keep_media,
            # So the page knows whether to offer a game list at all, rather
            # than showing a button that always refuses.
            "launch": self.session.launch_state(),
        })
        return guest

    def _broadcast(self, message):
        """Send one message to every connected guest."""
        for outbox in list(self._outboxes):
            outbox.put_nowait(message)

    async def _drain(self, socket_, outbox):
        """One writer per socket, so signalling never races itself.

        The offer must reach the browser before the candidates that belong to
        it. Both arrive from the GStreamer thread, so without a single ordered
        drain they can be sent in either order.
        """
        try:
            while True:
                message = await outbox.get()
                await socket_.send(json.dumps(message))
        except (asyncio.CancelledError, websockets.ConnectionClosed):
            pass

    # -- control surface ----------------------------------------------------

    async def _control(self, reader, writer):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                try:
                    request = json.loads(line)
                except ValueError:
                    continue
                reply = await self._command(request)
                writer.write((json.dumps(reply) + "\n").encode())
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()

    async def _command(self, request):
        command = request.get("cmd")
        try:
            if command == "status":
                return self._status()
            if command == "start":
                if self.session and self.session.open:
                    return {"ok": False, "error": "a session is already open"}
                LiveSession.forget()
                if self.session is not None:
                    # An expired session that nothing has swept still owns its
                    # pads. Dropping the object without closing it leaks a
                    # uinput device per slot, and they accumulate in this
                    # process until it exits -- six pads in a picker built for
                    # three.
                    await self.session.astop(reason="replaced")
                    self.session = None
                # Zero means no deadline, which is why it cannot go through
                # the `or default` below: a falsy minutes is a real answer.
                asked = request.get("minutes")
                if asked is None:
                    asked = self.cfg.default_duration_minutes
                minutes = int(asked)
                if minutes <= 0:
                    seconds = math.inf
                else:
                    # The cap applies to a number of minutes, not to a
                    # deliberate decision to have no limit.
                    minutes = max(1, min(minutes, self.cfg.max_duration_minutes))
                    seconds = minutes * 60
                self.session = LiveSession(self.cfg, self.loop)
                self.session.on_notice = self._broadcast
                self.session.start(seconds, slots=self._slots(request))
                return self._status()
            if command == "stop":
                if self.session:
                    await self.session.astop(reason="stopped by the owner")
                    self.session = None
                return {"ok": True}
            if command == "reshare":
                if not (self.session and self.session.open):
                    return {"ok": False, "error": "no session"}
                self.session.invite.reshare()
                self.session.save()
                log.info("re-shared: a new link and PIN, same session")
                return self._status()
            if command == "extend":
                if not (self.session and self.session.open):
                    return {"ok": False, "error": "no session"}
                if self.session.unlimited:
                    return {"ok": False,
                            "error": "this session has no time limit to extend"}
                minutes = max(1, int(request.get("minutes") or 15))
                added = self.session.extend(minutes * 60)
                if added <= 0:
                    return {"ok": False,
                            "error": "already at the maximum session length"}
                return self._status()
            if command == "url":
                if "set" in request:
                    try:
                        self.cfg.public_url = self.clean_url(request["set"])
                    except ValueError as exc:
                        return {"ok": False, "error": str(exc)}
                    try:
                        self.cfg.save()
                    except OSError as exc:
                        log.warning("could not remember the address: %s", exc)
                    log.info("links will be built on %s",
                             self.cfg.public_url or "the address on this network")
                return self._status()
            if command == "link":
                if "set" in request:
                    self.cfg.require_link = bool(request["set"])
                    try:
                        self.cfg.save()
                    except OSError as exc:
                        log.warning("could not remember it: %s", exc)
                    log.info("guests %s the full link",
                             "need" if self.cfg.require_link
                             else "need only the address and PIN")
                return self._status()
            if command == "slots":
                if request.get("set") is not None:
                    self.cfg.slots = self._slots({"slots": request["set"]},
                                                 fallback=self.cfg.slots)
                    try:
                        self.cfg.save()
                    except OSError as exc:
                        log.warning("could not remember the slot count: %s", exc)
                    log.info("sessions will open with %d slots", self.cfg.slots)
                return self._status()
            if command == "policy":
                if request.get("set"):
                    wanted = str(request["set"])
                    if wanted not in LAUNCH_POLICIES:
                        return {"ok": False,
                                "error": "unknown setting %r" % wanted}
                    # Settable with nothing open, in which case it is only the
                    # answer the next session will start with.
                    chosen = (self.session.set_policy(wanted)
                              if self.session and self.session.open else wanted)
                    # Remembered, so the next session starts the way the last
                    # one ended rather than silently back at off -- which read
                    # as the feature being broken.
                    self.cfg.guest_launch = chosen
                    try:
                        self.cfg.save()
                    except OSError as exc:
                        log.warning("could not remember the launch policy: %s", exc)
                return self._status()
            if command == "approve":
                if not (self.session and self.session.open):
                    return {"ok": False, "error": "no session"}
                return await self.session.approve_launch()
            if command == "deny":
                if not (self.session and self.session.open):
                    return {"ok": False, "error": "no session"}
                return self.session.deny_launch(
                    str(request.get("reason") or "the owner said no"))
            if command == "kick":
                if not (self.session and self.session.open):
                    return {"ok": False, "error": "no session"}
                self.session.kick(int(request.get("slot")))
                return self._status()
        except Exception as exc:                      # never kill the socket
            log.exception("control command failed: %r", request)
            return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": f"unknown command {command!r}"}

    @staticmethod
    def clean_url(text):
        """Tidy what somebody typed into a base a link can be built on.

        Forgiving about the scheme, because "fourthplayer.example.com" is what
        a person types and a bare host is not a URL. Strict about the rest: a
        base that is quietly wrong produces links that look right and go
        nowhere, which is the worst way for this to fail -- the owner sends
        them to a friend and hears back that it does not work.
        """
        text = (text or "").strip().rstrip("/")
        if not text:
            return ""                          # back to the address on the LAN
        if "://" not in text:
            text = "https://" + text
        parsed = urllib.parse.urlsplit(text)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("the address has to start with http:// or https://")
        if not parsed.netloc or " " in parsed.netloc:
            raise ValueError("that does not look like a web address")
        if parsed.query or parsed.fragment:
            raise ValueError("leave off anything after the path")
        # The path is kept: a reverse proxy may serve this under a prefix.
        return urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))

    def _slots(self, request, fallback=None):
        """How many may join, clamped to what this machine will lay out."""
        asked = request.get("slots")
        if asked in (None, ""):
            return fallback if fallback is not None else self.cfg.slots
        return max(1, min(int(asked), self.cfg.max_slots))

    def _restore_session(self):
        """Pick up a session that was running when this process last stopped.

        The point is that a crash costs a reconnect rather than the evening:
        the link and PIN already in people's hands keep working, and a guest
        with a token walks straight back in. The clear pair is not written
        down, so the owner is told to re-share if they want to read it again.
        """
        import time as _time
        invite = LiveSession.saved_invite(_time.monotonic())
        if invite is None:
            return
        try:
            session = LiveSession(self.cfg, self.loop)
            session.on_notice = self._broadcast
            session.start(0, invite=invite)
        except Exception as exc:
            log.warning("could not restore the previous session: %s", exc)
            LiveSession.forget()
            return
        self.session = session
        log.info("restored the session that was open before; %s",
                 "no time limit" if session.unlimited
                 else "%.0f minutes left" % (session.remaining() / 60))

    def _status(self):
        if not (self.session and self.session.open):
            # The remembered setting travels with a closed status too, so the
            # add-on can offer last time's answer when opening the next one.
            return {"ok": True, "open": False,
                    "public_url": self.cfg.public_url,
                    "example_url": self.join_url("EXAMPLE"),
                    "require_link": self.cfg.require_link,
                    "slots": self.cfg.slots,
                    "max_slots": self.cfg.max_slots,
                    "launch": {"policy": self.cfg.guest_launch, "pending": None}}
        clear = self.session.invite.clear_invite
        return {
            "ok": True,
            "open": True,
            # null rather than a number when there is no deadline: JSON has no
            # infinity, and a browser refuses to parse one.
            "remaining": None if self.session.unlimited
                         else round(self.session.remaining()),
            "unlimited": self.session.unlimited,
            "slots": self.session.slots,
            "max_slots": self.cfg.max_slots,
            "public_url": self.cfg.public_url,
            "example_url": self.join_url("EXAMPLE"),
            "require_link": self.cfg.require_link,
            "base_url": self.join_url("").rstrip("/").rsplit("/j", 1)[0],
            "guests": self.session.roster(),
            "url": self.join_url(clear[0]) if clear else None,
            "pin": clear[1] if clear else None,
            "launch": self.session.launch_state(),
        }

    def join_url(self, token):
        base = self.cfg.public_url.rstrip("/") if self.cfg.public_url else ""
        if not base:
            scheme = "https" if self.cfg.tls else "http"
            # 0.0.0.0 means "every interface", which is not somewhere a guest
            # can go. Show the address of the one they could plausibly reach.
            host = self.cfg.host
            if host in ("0.0.0.0", "::", ""):
                host = _lan_address()
            base = f"{scheme}://{host}:{self.cfg.port}"
        return f"{base}/j/{token}"

    # -- running ------------------------------------------------------------

    async def run(self):
        self.loop = asyncio.get_running_loop()
        context = None
        if self.cfg.tls:
            ensure_certificate(self.cfg.cert_path, self.cfg.key_path)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self.cfg.cert_path, self.cfg.key_path)

        try:
            public = await self._listen(context)
        except OSError as exc:
            if exc.errno == 98:      # EADDRINUSE
                raise SystemExit(
                    f"port {self.cfg.port} is already in use -- another "
                    f"fourth-player is probably already running") from None
            raise
        self._sockets.append(public)
        log.info("listening on %s:%d (%s, %s)", self.cfg.host, self.cfg.port,
                 "https" if self.cfg.tls else "http -- plain",
                 "trusting X-Forwarded-For" if self.cfg.behind_proxy
                 else "peer address")

        if os.path.exists(CONTROL_SOCKET):
            # Only a leftover may be removed. A live one belongs to another
            # server, and taking it makes that server unreachable without
            # either of them noticing -- its own `status` then answers "no
            # server is running" while it happily keeps streaming.
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(2)
            try:
                probe.connect(CONTROL_SOCKET)
            except OSError:
                os.unlink(CONTROL_SOCKET)
            else:
                raise SystemExit(
                    f"another fourth-player is already using {CONTROL_SOCKET}.\n"
                    f"Stop it first, or set XDG_RUNTIME_DIR to give this one its "
                    f"own.")
            finally:
                probe.close()
        control = await asyncio.start_unix_server(self._control, path=CONTROL_SOCKET)
        os.chmod(CONTROL_SOCKET, 0o600)
        self._sockets.append(control)
        log.info("control socket at %s", CONTROL_SOCKET)

        self._restore_session()

        try:
            await asyncio.Future()
        finally:
            if self.session:
                self.session.stop(reason="server shutting down")   # sync: loop is going
            for server in self._sockets:
                server.close()
            if os.path.exists(CONTROL_SOCKET):
                os.unlink(CONTROL_SOCKET)

    async def _listen(self, context):
        return await websockets.serve(
            self._guest, self.cfg.host, self.cfg.port,
            ssl=context, process_request=self._http,
            # Generous, because losing this socket used to cost the session.
            # It no longer does, but a browser that backgrounds a tab should
            # not be dropped for it either.
            ping_interval=30, ping_timeout=60, max_size=64 * 1024)
