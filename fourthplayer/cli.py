"""Entry points: run the server, drive it, or check the machine can host it."""

import argparse
import asyncio
import json
import logging
import os
import socket
import sys
import time

from .config import Config, CONFIG_PATH, PRESETS
from .session import LAUNCH_POLICIES
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
                     help="trust X-Forwarded-For, so rate limiting sees each "
                          "guest rather than the proxy")
    run.add_argument("--no-tls", action="store_true",
                     help="serve plain HTTP. Only when a proxy re-encrypts in "
                          "front: browsers withhold the Gamepad API from pages "
                          "that are not a secure context")
    run.add_argument("--public-url", help="the base URL guests will use")
    run.add_argument("--slots", type=int, help="how many guests may join")
    run.add_argument("--fps", type=int, help="capture frame rate (try 30 if it lags)")
    run.add_argument("--bitrate", type=int, metavar="KBPS",
                     help="video bitrate ceiling in kb/s")
    run.add_argument("--width", type=int)
    run.add_argument("--height", type=int)
    run.add_argument("--preset", choices=sorted(PRESETS),
                     help="smooth (default), sharp, remote (over a VPN or the "
                          "internet), minimum (as little bandwidth as it can use)")
    run.add_argument("--codec", choices=["h264", "h265"],
                     help="h265 halves the bitrate for the same picture and is "
                          "refused by most browsers; try it only if every guest "
                          "is on recent Apple hardware")
    run.add_argument("--jitter", type=int, metavar="MS",
                     help="how much the guest's browser buffers before playing. "
                          "Lower is less delay and more stutter (default 30)")
    run.add_argument("--queue", type=int, metavar="MS",
                     help="how much encoded video may pile up per guest when the "
                          "link is tight. This is delay (default 60)")
    run.add_argument("--mtu", type=int, metavar="BYTES",
                     help="RTP packet size (lower it for VPNs; default 1200)")
    run.add_argument("--no-audio", action="store_true", help="stream silently")
    run.add_argument("--audio-device", metavar="SOURCE",
                     help="PulseAudio source to capture (default: the monitor "
                          "of whatever sink applications are using)")
    run.add_argument("--software", action="store_true",
                     help="encode with x264 instead of the GPU")
    run.add_argument("--verbose", "-v", action="store_true")

    start = sub.add_parser("start", help="open a session")
    start.add_argument("--minutes", type=int,
                       help="how long, in minutes. 0 for no time limit.")
    start.add_argument("--unlimited", action="store_true",
                       help="open with no deadline; it runs until stopped")
    start.add_argument("--slots", type=int,
                       help="how many can join at once (default 3)")
    start.add_argument("--wait", type=float, default=20.0, metavar="SECONDS",
                       help="how long to wait for the server to come up")

    extend = sub.add_parser("extend", help="add time to the open session")
    extend.add_argument("--minutes", type=int, default=15)

    sub.add_parser("stop", help="close the session")
    sub.add_parser("reshare", help="new link and PIN, same session and players")
    sub.add_parser("status", help="show the session and its guests")

    policy = sub.add_parser(
        "policy", help="whether guests may start games, and on what terms")
    policy.add_argument("set", nargs="?", choices=LAUNCH_POLICIES,
                        help="off: not at all. open: any time, interrupting "
                             "whatever is playing. idle: only when nothing is "
                             "running. approve: ask, and answer within 30s. "
                             "Omit to read the current setting.")
    address = sub.add_parser(
        "url", help="the address guests' links are built on")
    address.add_argument("set", nargs="?",
                         help="e.g. https://fourthplayer.example.com. Pass an "
                              "empty string to go back to this machine's "
                              "address on the network. Omit to read it.")

    link = sub.add_parser(
        "link", help="whether guests need the whole link or just the PIN")
    link.add_argument("set", nargs="?", choices=["required", "open"],
                      help="required: the link and the PIN, which is the "
                           "default. open: the address and the PIN, so it can "
                           "be read out loud. Omit to read the setting.")

    slots = sub.add_parser(
        "slots", help="how many can join at once, from the next session on")
    slots.add_argument("set", nargs="?", type=int,
                       help="omit to read the current setting")

    sub.add_parser("approve", help="say yes to the waiting launch request")
    deny = sub.add_parser("deny", help="say no to the waiting launch request")
    deny.add_argument("--reason", default="the owner said no")

    kick = sub.add_parser("kick", help="remove one guest")
    kick.add_argument("slot", type=int)

    sub.add_parser("check", help="report whether this machine can host")
    sub.add_parser("write-config", help="write the default config file")

    args = parser.parse_args(argv)
    cfg = Config.load()

    if args.command == "serve":
        # A preset first, so the individual flags can still override parts of it.
        if args.preset:
            for key, value in PRESETS[args.preset].items():
                setattr(cfg, key, value)
        for field in ("port", "slots", "public_url", "fps", "width", "height"):
            value = getattr(args, field, None)
            if value:
                setattr(cfg, field, value)
        if args.bitrate:
            cfg.bitrate_kbps = args.bitrate
        if args.mtu:
            cfg.rtp_mtu = args.mtu
        if args.codec:
            cfg.codec = args.codec
        if args.jitter is not None:
            cfg.jitter_ms = args.jitter
        if args.queue is not None:
            cfg.queue_ms = args.queue
        if args.behind_proxy:
            cfg.behind_proxy = True
        if args.no_tls:
            cfg.tls = False
        if args.software:
            cfg.hardware_encode = False
        if args.no_audio:
            cfg.audio = False
        if args.audio_device:
            cfg.audio_device = args.audio_device
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
    if args.command == "start" and getattr(args, "unlimited", False):
        request["minutes"] = 0
    elif args.command in ("start", "extend") and args.minutes is not None:
        request["minutes"] = args.minutes
    if args.command == "kick":
        request["slot"] = args.slot
    if args.command == "policy" and args.set:
        request["set"] = args.set
    if args.command == "url" and args.set is not None:
        request["set"] = args.set
    if args.command == "link" and args.set is not None:
        request["set"] = (args.set == "required")
    if args.command == "slots" and args.set is not None:
        request["set"] = args.set
    if args.command == "start" and args.slots:
        request["slots"] = args.slots
    if args.command == "deny":
        request["reason"] = args.reason

    reply = _control(request, wait=getattr(args, "wait", 0.0))
    if not reply.get("ok"):
        print("error:", reply.get("error", "unknown"), file=sys.stderr)
        return 1
    if args.command in ("approve", "deny"):
        print(reply.get("error") or reply.get("label")
              or reply.get("state") or "done")
        return 0
    _print_status(reply)
    return 0


def _print_address(reply):
    if reply.get("require_link") is False:
        base = reply.get("base_url") or reply.get("public_url") or ""
        print(f"  guests need only {base or 'the address'} and the PIN "
              f"-- the link is not required")
    if reply.get("public_url"):
        print(f"  links are built on {reply['public_url']}")
    elif reply.get("example_url"):
        # No public address set, so links point at this machine on the LAN --
        # which works from the sofa and nowhere else.
        print(f"  no address set; links point at "
              f"{reply['example_url'].split('/j/')[0]} (this network only)")


def _print_status(reply):
    if not reply.get("open"):
        print("no session is open")
        if reply.get("slots"):
            print(f"  the next one will hold {reply['slots']} "
                  f"(up to {reply.get('max_slots', '?')})")
        _print_address(reply)
        return
    if reply.get("unlimited") or reply.get("remaining") is None:
        print("session open, no time limit")
    else:
        minutes, seconds = divmod(reply["remaining"], 60)
        print(f"session open, {minutes}m {seconds}s left")
    if reply.get("url"):
        print(f"  link: {reply['url']}")
        print(f"  PIN:  {reply['pin']}")
    else:
        print("  link and PIN were forgotten on restart -- re-share to get new ones")
    launch = reply.get("launch") or {}
    if launch:
        wording = {
            "off": "guests cannot start games",
            "open": "guests may start any game, interrupting what is playing",
            "idle": "guests may start a game when nothing is running",
            "approve": "guests may ask to start a game; you have 30s to answer",
        }
        print("  " + wording.get(launch.get("policy"), str(launch)))
        if launch.get("pending"):
            waiting = launch["pending"]
            print(f"    WAITING: {waiting['who']} wants {waiting['label']} "
                  f"({waiting['seconds']}s left) -- approve or deny")
    _print_address(reply)
    guests = reply.get("guests") or []
    print(f"  guests: {len(guests)}/{reply.get('slots')}")
    for guest in guests:
        state = "connected" if guest["connected"] else "away"
        print(f"    slot {guest['slot']}  {guest['label']:<10} {state:<10} "
              f"{guest['frames']} inputs  {guest['pad']}")
        print(f"      sent to them: {guest.get('video_kb', 0)} kB video "
              f"({guest.get('video_packets', 0)} packets), "
              f"{guest.get('audio_kb', 0)} kB audio")


def _monitor_sources():
    """The monitor sources this machine offers, for `check` to report."""
    try:
        import subprocess as _sp
        out = _sp.run(["pactl", "list", "short", "sources"],
                      capture_output=True, text=True, timeout=5)
        return [line.split("\t")[1] for line in out.stdout.splitlines()
                if "\t" in line and ".monitor" in line]
    except Exception:
        return []


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
            # Not an instruction any more: the server drops to software on its
            # own when the hardware is not there. Worth saying, because it is
            # the difference between a session that is smooth and one that is
            # merely watchable.
            notes.append("no hardware encoder here (vah264enc); sessions will "
                         "encode in software, which is slower and works")
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

    if cfg.audio:
        for element in ("pulsesrc", "opusenc", "rtpopuspay"):
            try:
                import gi as _gi  # noqa: F401
                from gi.repository import Gst as _Gst
                if not _Gst.ElementFactory.find(element):
                    notes.append(f"{element} is missing, so sessions will be silent")
            except Exception:
                break
        sources = _monitor_sources()
        if sources:
            notes.append("sound will come from the default sink's monitor; "
                         "available monitors: " + ", ".join(sources[:3]))
        else:
            notes.append("no PulseAudio monitor sources found -- sessions may be silent")

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
