"""The channel `start`, `status` and the add-on use to drive a running server.

One socket, carrying line-delimited JSON, and the only thing platform-specific
about it is how it is addressed and how it keeps other people out.

**Linux: a Unix socket in XDG_RUNTIME_DIR.** That directory is mode 0700, so
the permission check is the filesystem's and there is nothing to get wrong.

**Windows: a TCP socket on the loopback, and a token.** Windows asyncio has no
start_unix_server, and the loopback is not private the way that directory is --
any process on the machine can reach 127.0.0.1. So the port and a 32-byte
random token are written to a file under LOCALAPPDATA, which is private to the
user by its own ACL, and a connection that does not present the token is
dropped before its request is read. That makes reading the file the thing you
must be able to do, which is the same test the Unix socket applies, arrived at
by a different route.

Worth being plain about what that is not: an administrator on the machine can
read the file, and so can anything running as this user. Both are true of the
Unix socket as well -- root can connect to it and so can anything running as
this user -- so this is the same guarantee, not a weaker one.
"""
import asyncio
import json
import os
import secrets
import socket
import sys

WINDOWS = sys.platform == "win32"

UNIX_PATH = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "fourth-player.sock")


def _windows_state_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "fourth-player", "control.json")


def address():
    """Where this machine's control channel lives, for a message to a human."""
    return _windows_state_path() if WINDOWS else UNIX_PATH


def _read_state():
    try:
        with open(_windows_state_path(), "r", encoding="utf-8") as handle:
            state = json.load(handle)
        return int(state["port"]), str(state["token"])
    except (OSError, ValueError, KeyError):
        return None, None


def _write_state(port, token):
    path = _windows_state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"port": port, "token": token}, handle)


def connect(timeout=10):
    """A socket connected to the running server, ready to take a request.

    Raises the same errors a bare connect would, so the callers' existing
    "no server is running" handling is unchanged -- ConnectionRefusedError
    where nothing is listening, FileNotFoundError where nothing has been set
    up at all.
    """
    if not WINDOWS:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(UNIX_PATH)
        return sock

    port, token = _read_state()
    if not port:
        raise FileNotFoundError(_windows_state_path())
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(("127.0.0.1", port))
    # The token first, on its own line, before anything worth having.
    sock.sendall((token + "\n").encode())
    return sock


def in_use():
    """Whether a server is already answering here. Clears a stale address."""
    if not WINDOWS:
        if not os.path.exists(UNIX_PATH):
            return False
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(2)
        try:
            probe.connect(UNIX_PATH)
        except OSError:
            os.unlink(UNIX_PATH)            # only ever a leftover
            return False
        else:
            probe.close()
            return True

    port, _token = _read_state()
    if not port:
        return False
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(2)
    try:
        probe.connect(("127.0.0.1", port))
    except OSError:
        return False                        # stale file; the port is nobody's
    else:
        probe.close()
        return True


async def serve(handler):
    """Listen, and hand accepted connections to `handler(reader, writer)`.

    On Windows the token is checked here rather than by the handler, so the
    server's own code is the same on both platforms and cannot forget to.
    """
    if not WINDOWS:
        server = await asyncio.start_unix_server(handler, path=UNIX_PATH)
        os.chmod(UNIX_PATH, 0o600)
        return server

    token = secrets.token_hex(32)

    async def guarded(reader, writer):
        try:
            offered = await asyncio.wait_for(reader.readline(), timeout=5)
        except (asyncio.TimeoutError, ConnectionError):
            writer.close()
            return
        # compare_digest, because the difference between "wrong at the first
        # byte" and "wrong at the last" is a way to guess a token one byte at
        # a time.
        if not secrets.compare_digest(offered.decode(errors="replace").strip(),
                                      token):
            writer.close()
            return
        await handler(reader, writer)

    server = await asyncio.start_server(guarded, host="127.0.0.1", port=0)
    port = server.sockets[0].getsockname()[1]
    _write_state(port, token)
    return server


def cleanup():
    """Take the address away on the way out, so the next start is not refused."""
    try:
        if WINDOWS:
            os.unlink(_windows_state_path())
        else:
            os.unlink(UNIX_PATH)
    except OSError:
        pass
