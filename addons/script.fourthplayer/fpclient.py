"""Talking to the fourth-player server, and to systemd when it is not running.

Everything the add-on does goes through the server's Unix control socket. There
is no network call here and no credential: the socket is a file in the user's
runtime directory, so being able to open it *is* the authorisation.
"""

import json
import os
import socket
import subprocess

CONTROL_SOCKET = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid()),
    "fourth-player.sock")

SERVICE = "fourth-player"


class NotRunning(Exception):
    """No server answered. The add-on offers to start one."""


def ask(request, timeout=10):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(CONTROL_SOCKET)
            sock.sendall((json.dumps(request) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        return json.loads(data or b"{}")
    except (FileNotFoundError, ConnectionRefusedError):
        raise NotRunning()
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def status():
    return ask({"cmd": "status"})


def start_session(minutes):
    return ask({"cmd": "start", "minutes": minutes}, timeout=30)


def stop_session():
    return ask({"cmd": "stop"}, timeout=30)


def reshare():
    return ask({"cmd": "reshare"})


def extend(minutes):
    return ask({"cmd": "extend", "minutes": minutes})


def kick(slot):
    return ask({"cmd": "kick", "slot": slot})


# -- the service itself --------------------------------------------------

def _systemctl(*args, timeout=25):
    try:
        done = subprocess.run(["systemctl", "--user", *args],
                              capture_output=True, text=True, timeout=timeout)
        return done.returncode, (done.stdout + done.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def service_installed():
    code, _ = _systemctl("cat", SERVICE, timeout=10)
    return code == 0


def service_active():
    code, _ = _systemctl("is-active", SERVICE, timeout=10)
    return code == 0


def start_service():
    return _systemctl("start", SERVICE)


def stop_service():
    return _systemctl("stop", SERVICE)


def restart_service():
    return _systemctl("restart", SERVICE)
