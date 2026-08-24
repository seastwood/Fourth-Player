"""Entry points: run the server, drive it, or check the machine can host it."""

import argparse
import asyncio
import json
import logging
import os
import socket
import sys
import time

from .config import Config, CONFIG_PATH
from .server import Server, CONTROL_SOCKET


def _control(request, wait=0.0):
    """Speak one command to a running server over its Unix socket.

    `wait` exists because the obvious way to start this is
    `serve & fourth-player start`, and the control socket does not exist for
    the first second or two of the server's life. Failing there sent people
    back to the shell to run the same command again, which looked like the
    server needed starting twice.
    """
    deadline = time.monotonic() + wait
    while True:
        reply = _control_once(request)
        if reply.get("ok") or not reply.get("retryable"):
            return reply
        if time.monotonic() >= deadline:
            return reply
        time.sleep(0.25)


def _control_once(request):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(10)
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
        return {"ok": False, "retryable": True,
                "error": "no server is running (start it with: "
                         "python3 -m fourthplayer serve)"}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="fourth-player", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("serve", help="run the server")
    run.add_argument("--port", type=int)
    run.add_argument("--behind-proxy", action="store_true",
                     help="plain HTTP; a reverse proxy terminates TLS")
    run.add_argument("--public-url", help="the base URL guests will use")
    run.add_argument("--slots", type=int, help="how many guests may join")
    run.add_argument("--fps", type=int, help="capture frame rate (try 30 if it lags)")
    run.add_argument("--bitrate", type=int, metavar="KBPS",
                     help="video bitrate ceiling in kb/s")
    run.add_argument("--width", type=int)
    run.add_argument("--height", type=int)
    run.add_argument("--software", action="store_true",
                     help="encode with x264 instead of the GPU")
    run.add_argument("--verbose", "-v", action="store_true")

    start = sub.add_parser("start", help="open a session")
    start.add_argument("--minutes", type=int)
    start.add_argument("--wait", type=float, default=20.0, metavar="SECONDS",
                       help="how long to wait for the server to come up")

    extend = sub.add_parser("extend", help="add time to the open session")
    extend.add_argument("--minutes", type=int, default=15)

    sub.add_parser("stop", help="close the session")
    sub.add_parser("status", help="show the session and its guests")

    kick = sub.add_parser("kick", help="remove one guest")
    kick.add_argument("slot", type=int)

    sub.add_parser("check", help="report whether this machine can host")
    sub.add_parser("write-config", help="write the default config file")

    args = parser.parse_args(argv)
    cfg = Config.load()

    if args.command == "serve":
        for field in ("port", "slots", "public_url", "fps", "width", "height"):
            value = getattr(args, field, None)
            if value:
                setattr(cfg, field, value)
        if args.bitrate:
            cfg.bitrate_kbps = args.bitrate
        if args.behind_proxy:
            cfg.behind_proxy = True
        if args.software:
            cfg.hardware_encode = False
        logging.basicConfig(
            level=logging.DEBUG if args.verbose else logging.INFO,
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S")
        try:
            asyncio.run(Server(cfg).run())
        except KeyboardInterrupt:
            print("\nstopped", file=sys.stderr)
        return 0

    if args.command == "write-config":
        cfg.save()
        print(f"wrote {CONFIG_PATH}")
        return 0

    if args.command == "check":
        return _check(cfg)

    request = {"cmd": args.command}
    if args.command in ("start", "extend") and args.minutes:
        request["minutes"] = args.minutes
    if args.command == "kick":
        request["slot"] = args.slot

    reply = _control(request, wait=getattr(args, "wait", 0.0))
    if not reply.get("ok"):
        print("error:", reply.get("error", "unknown"), file=sys.stderr)
        return 1
    _print_status(reply)
    return 0


def _print_status(reply):
    if not reply.get("open"):
        print("no session is open")
        return
    minutes, seconds = divmod(reply["remaining"], 60)
    print(f"session open, {minutes}m {seconds}s left")
    if reply.get("url"):
        print(f"  link: {reply['url']}")
        print(f"  PIN:  {reply['pin']}")
    else:
        print("  link and PIN were forgotten on restart -- re-share to get new ones")
    guests = reply.get("guests") or []
    print(f"  guests: {len(guests)}/{reply.get('slots')}")
    for guest in guests:
        state = "connected" if guest["connected"] else "away"
        print(f"    slot {guest['slot']}  {guest['label']:<10} {state:<10} "
              f"{guest['frames']} frames  {guest['pad']}")


def _check(cfg):
    """What a new machine gets wrong, reported before it wastes an evening."""
    problems, notes = [], []

    if not os.path.exists("/dev/uinput"):
        problems.append("/dev/uinput is missing -- the uinput module is not loaded")
    elif not os.access("/dev/uinput", os.W_OK):
        problems.append("/dev/uinput is not writable by this user "
                        "(a uaccess udev rule or the input group fixes it)")

    if not os.environ.get("DISPLAY"):
        notes.append("DISPLAY is unset; the server will use " + cfg.display)

    try:
        import gi
        gi.require_version("Gst", "1.0")
        gi.require_version("GstWebRTC", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        for element in ("ximagesrc", "webrtcbin", "rtph264pay", "h264parse"):
            if not Gst.ElementFactory.find(element):
                problems.append(f"the GStreamer element {element} is missing")
        if cfg.hardware_encode and not Gst.ElementFactory.find("vah264enc"):
            notes.append("vah264enc is missing; set hardware_encode=false to use x264")
        # webrtcbin loads without libnice and then refuses to run, which is a
        # uniquely annoying way to fail: the element exists, so every naive
        # check passes, and the error arrives only once a guest is waiting.
        probe = Gst.ElementFactory.make("webrtcbin", None)
        if probe is None or probe.set_state(Gst.State.READY) == Gst.StateChangeReturn.FAILURE:
            problems.append("webrtcbin will not start -- gstreamer1.0-nice is probably missing")
        if probe:
            probe.set_state(Gst.State.NULL)
    except ValueError as exc:
        problems.append(f"GStreamer introspection is incomplete: {exc} "
                        "(gir1.2-gst-plugins-bad-1.0 provides GstWebRTC)")
    except ImportError:
        problems.append("PyGObject is missing (python3-gi)")

    for module in ("evdev", "websockets", "cryptography"):
        try:
            __import__(module)
        except ImportError:
            problems.append(f"the python module {module} is missing")

    for note in notes:
        print("note:    " + note)
    for problem in problems:
        print("PROBLEM: " + problem)
    if not problems:
        print("this machine can host a session")
    return 1 if problems else 0
