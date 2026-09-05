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
    share = sub.add_parser(
        "share", help="whether guests may drive the same controller together")
    share.add_argument("set", nargs="?", choices=["on", "off"],
                       help="on: everybody who picks a controller drives it, "
                            "for games meant to be played by passing one pad "
                            "round. off: picking a taken one swaps you. Omit "
                            "to read the current setting.")
    pin = sub.add_parser(
        "pin", help="set the PIN guests type, instead of a new one each session")
    pin.add_argument("set", nargs="?",
                     help="4 to 12 digits, used for every session from now on. "
                          "Pass an empty string to go back to a fresh random "
                          "PIN each time. Omit to see which is in use.")
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

    # Both of these act on the session that is open now, rather than on the
    # next one, because "there are too many people in this" and "everybody out
    # but me" are things you want to say while it is happening.
    limit = sub.add_parser(
        "limit", help="how many may be connected to the open session")
    limit.add_argument("set", nargs="?", type=int,
                       help="omit to read it; never goes below the accounts here")
    lock = sub.add_parser(
        "lock", help="say who may be in the open session")
    lock.add_argument("set", nargs="?", choices=("off", "accounts", "named"),
                      help="off: anybody with the invite. accounts: only "
                           "somebody logged in. named: only the accounts you "
                           "list. Omit to read it.")
    lock.add_argument("who", nargs="*",
                      help="account names, for `named`. The first account made "
                           "is always let in, whatever you say here.")

    sub.add_parser("approve", help="say yes to the waiting launch request")
    deny = sub.add_parser("deny", help="say no to the waiting launch request")
    deny.add_argument("--reason", default="the owner said no")

    kick = sub.add_parser("kick", help="remove one guest, or all of them")
    kick.add_argument("slot", help="a slot number, or `all`")

    # Which Steam games guests may start. Its own list, because a Steam
    # library is mostly things the owner would not hand to a stranger on a
    # phone, and on the console this was written for four of the six installed
    # "games" are Proton and the Steam runtime.
    steam = sub.add_parser("steam", help="choose which Steam games guests get")
    steam_sub = steam.add_subparsers(dest="steam_command", required=True)
    steam_sub.add_parser("installed", help="what Steam has on this machine")
    steam_sub.add_parser("list", help="what guests are offered")
    steam_add = steam_sub.add_parser("add", help="offer one to guests")
    steam_add.add_argument("game", nargs="+",
                           help="a name or an appid; the name need not match "
                                "Valve's capitalisation")
    steam_drop = steam_sub.add_parser("remove", help="stop offering one")
    steam_drop.add_argument("game", nargs="+")

    # Accounts. Only here: the web may hand out capabilities that already
    # exist, and may never create one. Making an account, resetting a second
    # factor and adding a Steam game are all acts of creation, so all three
    # happen sitting in front of the machine or not at all.
    admin = sub.add_parser("admin", help="the accounts guests can log in to")
    admin_sub = admin.add_subparsers(dest="admin_command", required=True)
    admin_add = admin_sub.add_parser("add", help="create an account")
    admin_add.add_argument("name")
    admin_add.add_argument("--can", nargs="*", default=None, metavar="CAPABILITY",
                           help="what it may do; the first account gets grant")
    admin_sub.add_parser("list", help="every account and what it may do")
    admin_can = admin_sub.add_parser("can", help="show or set what an account may do")
    admin_can.add_argument("name")
    admin_can.add_argument("capability", nargs="*",
                           help="the complete list; none at all shows it instead")
    admin_pass = admin_sub.add_parser("passwd", help="change an account's password")
    admin_pass.add_argument("name")
    admin_2fa = admin_sub.add_parser("reset-2fa", help="new authenticator secret")
    admin_2fa.add_argument("name")
    admin_forget = admin_sub.add_parser(
        "forget-devices", help="sign out every remembered device")
    admin_forget.add_argument("name")
    admin_drop = admin_sub.add_parser("remove", help="delete an account")
    admin_drop.add_argument("name")
    admin_drop.add_argument("--yes", action="store_true", help="do not ask")

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

    if args.command == "steam":
        return _steam(args)

    if args.command == "admin":
        return _admin(args)

    if args.command == "check":
        return _check(cfg)

    request = {"cmd": args.command}
    if args.command == "start" and getattr(args, "unlimited", False):
        request["minutes"] = 0
    elif args.command in ("start", "extend") and args.minutes is not None:
        request["minutes"] = args.minutes
    if args.command == "kick" and str(args.slot).lower() == "all":
        # One command for "clear the room". Doing it a slot at a time is four
        # commands and a race with anybody reconnecting between them.
        request["slot"] = "all"
    elif args.command == "kick":
        request["slot"] = args.slot
    if args.command == "policy" and args.set:
        request["set"] = args.set
    if args.command == "share" and args.set is not None:
        request["set"] = (args.set == "on")
    if args.command == "pin" and args.set is not None:
        request["set"] = args.set
    if args.command == "url" and args.set is not None:
        request["set"] = args.set
    if args.command == "link" and args.set is not None:
        request["set"] = (args.set == "required")
    if args.command == "limit" and args.set is not None:
        request["set"] = args.set
    if args.command == "lock" and args.set is not None:
        request["set"] = "" if args.set == "off" else args.set
        request["who"] = list(getattr(args, "who", []) or [])
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


def _print_share_rule(reply):
    if reply.get("share_pads"):
        print("  guests may share one controller")
    else:
        print("  each guest gets a controller of their own")


def _print_pin_rule(reply):
    # The digits themselves are only ever shown beside a live session; this
    # says which rule is in force, which is what somebody setting it wants.
    if reply.get("pin_fixed"):
        print("  guests type the PIN that was set")
    else:
        print("  each session gets a new random PIN")


def _print_status(reply):
    if not reply.get("open"):
        print("no session is open")
        if reply.get("slots"):
            print(f"  the next one will hold {reply['slots']} "
                  f"(up to {reply.get('max_slots', '?')})")
        _print_address(reply)
        _print_pin_rule(reply)
        _print_share_rule(reply)
        return
    _print_pin_rule(reply)
    _print_share_rule(reply)
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
    limit, slots = reply.get("limit"), reply.get("slots")
    # Always said, never only when it is on. "I'm not sure what it's set at
    # now" is what a setting that only announces itself half the time gets.
    mode = reply.get("locked") or ""
    if mode == "accounts":
        print("  who may join: only somebody logged in to an account")
    elif mode == "named":
        print("  who may join: only %s (and the first account made, always)"
              % (", ".join(reply.get("allowed") or []) or "the owner"))
    else:
        print("  who may join: anybody with the link and PIN")
    if limit and slots and limit < slots:
        print(f"  at most {limit} connected at once (of {slots} slots)")
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
    pads = reply.get("pads") or {}
    if pads:
        ports = pads.get("ports") or {}
        if not pads.get("playing"):
            print("  no game running, so no player numbers to give")
        elif ports:
            print("  players: " + ", ".join(
                "controller %d is player %s" % (int(i) + 1, ports[i])
                for i in sorted(ports, key=int)))
        else:
            print("  a game is running, but which controller is which player "
                  "could not be read")
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


def _admin(args):
    """Create and edit accounts.

    Local, like `steam`: it edits a file rather than the running session, so
    it works whether or not a session is open. A change takes effect at the
    next login; anybody already logged in keeps what they had until their
    socket closes, which is the same rule the rest of the program follows.
    """
    import getpass
    import subprocess
    from . import accounts

    def ask_password(who):
        """Twice, and never from the command line.

        A password given as an argument is in `ps`, in the shell history and
        in whatever the terminal scrolled back to. Asking is not politeness.

        Piped in, it is read as two plain lines instead. That is how an
        install script creates the first account, and it gives up nothing:
        stdin is not in `ps` either, and anybody who can pipe to this command
        is already at a shell on the machine, which is further in than any
        password here would take them.
        """
        if not sys.stdin.isatty():
            lines = [sys.stdin.readline().rstrip("\n"),
                     sys.stdin.readline().rstrip("\n")]
        else:
            lines = [getpass.getpass("A password for %s: " % who),
                     getpass.getpass("And again: ")]
        if len(lines[0]) < 8:
            print("A password needs to be at least eight characters. "
                  "Nothing was changed.")
            return None
        if lines[0] != lines[1]:
            print("Those did not match. Nothing was changed.")
            return None
        return lines[0]

    def show_secret(name, secret):
        """The one time the secret is ever printed."""
        print()
        print("Set up an authenticator app for %s now -- this is shown once." % name)
        print()
        print("  secret:  %s" % secret)
        print("  or URI:  %s" % accounts.otpauth(name, secret))
        # A nicety when the tool happens to be installed. The secret above is
        # the real answer; this only saves typing it.
        try:
            subprocess.run(["qrencode", "-t", "ANSIUTF8",
                            accounts.otpauth(name, secret)], check=True)
        except (OSError, subprocess.SubprocessError):
            pass
        print()

    try:
        if args.admin_command == "add":
            first = not accounts.all_accounts()
            can = args.can
            if can is None:
                # The first account is the way in, so it gets the bit that can
                # hand out the others. Later accounts start with nothing,
                # because an account that can do nothing is a safe mistake and
                # an account that can do everything is not.
                can = ["grant"] if first else []
            for cap in can:
                accounts.check_capability(cap)
            password = ask_password(args.name)
            if password is None:
                return 1
            account, secret = accounts.add(args.name, password, can)
            print("Created %s." % account["name"])
            if first:
                print("This is the first account, so it may grant capabilities "
                      "to the others.")
            print("It may: %s" % (" ".join(account["can"]) or "nothing yet"))
            show_secret(account["name"], secret)
            return 0

        if args.admin_command == "list":
            everyone = accounts.all_accounts()
            if not everyone:
                print("There are no accounts. Make one with: "
                      "fourth-player admin add <name>")
                return 0
            for account in everyone:
                seen = account.get("last_seen") or 0
                devices = len([d for d in account.get("devices", [])
                               if d.get("expires", 0) > time.time()])
                print("  %-16s %-32s %s%s" % (
                    account["name"],
                    " ".join(account.get("can") or []) or "-",
                    ("last seen " + time.strftime("%d %b %H:%M", time.localtime(seen)))
                    if seen else "never logged in",
                    (", %d remembered device%s" % (devices, "" if devices == 1 else "s"))
                    if devices else ""))
            return 0

        if args.admin_command == "can":
            account = accounts.find(args.name)
            if account is None:
                print("There is no account called %r." % args.name)
                return 1
            if not args.capability:
                print("%s may: %s" % (account["name"],
                                      " ".join(account.get("can") or []) or "nothing"))
                print("Available: %s, or steam:<appid>"
                      % ", ".join(accounts.CAPABILITIES))
                return 0
            account = accounts.set_capabilities(args.name, args.capability)
            print("%s may now: %s" % (account["name"],
                                      " ".join(account["can"]) or "nothing"))
            if "grant" in account["can"]:
                print("Note: grant lets this account give itself the rest.")
            return 0

        if args.admin_command == "passwd":
            if accounts.find(args.name) is None:
                print("There is no account called %r." % args.name)
                return 1
            password = ask_password(args.name)
            if password is None:
                return 1
            accounts.set_password(args.name, password)
            print("The password for %s was changed." % args.name)
            return 0

        if args.admin_command == "reset-2fa":
            account, secret = accounts.reset_totp(args.name)
            print("%s has a new authenticator secret, and every remembered "
                  "device was signed out." % account["name"])
            show_secret(account["name"], secret)
            return 0

        if args.admin_command == "forget-devices":
            gone = accounts.forget_devices(args.name)
            print("Signed out %d remembered device%s for %s."
                  % (gone, "" if gone == 1 else "s", args.name))
            return 0

        if args.admin_command == "remove":
            account = accounts.find(args.name)
            if account is None:
                print("There is no account called %r." % args.name)
                return 1
            # Deleting the last account that can grant leaves a session nobody
            # can administer from a phone. It is recoverable -- `admin add` is
            # right here -- but not by anybody who is not at the console, so
            # it is worth one question.
            granters = [a for a in accounts.all_accounts()
                        if "grant" in (a.get("can") or [])]
            last = [a["name"] for a in granters] == [account["name"]]
            if last and not args.yes:
                print("%s is the only account that can grant capabilities."
                      % account["name"])
                print("Removing it leaves nobody who can administer a session "
                      "from a phone.")
                if input("Remove it anyway? [y/N] ").strip().lower() not in ("y", "yes"):
                    print("Nothing was changed.")
                    return 1
            accounts.remove(args.name)
            print("Removed %s, and any devices it had remembered."
                  % account["name"])
            return 0

    except accounts.AccountError as exc:
        print("%s" % exc)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nNothing was changed.")
        return 1

    return 1


def _steam(args):
    """Read and edit the list of Steam games guests may start.

    Local: this touches a file, not the running session, so it works whether
    or not a session is open and needs no control socket.
    """
    from . import steamgames

    if args.steam_command == "installed":
        here = steamgames.installed()
        if not here:
            print("Steam has no games installed here, or Steam is not "
                  "installed at all.")
            return 0
        picked = {g["appid"] for g in steamgames.offered()}
        for game in here:
            print("  %-9s %-40s %s" % (
                game["appid"], game["name"],
                "offered" if game["appid"] in picked else ""))
        return 0

    if args.steam_command == "list":
        offered = steamgames.offered()
        for game in offered:
            print("  %-9s %s" % (game["appid"], game["name"]))
        # Said plainly, because a name on the list that matches nothing
        # installed is the likeliest reason a game is not showing up.
        known = {g["name"].lower() for g in offered} | {g["appid"] for g in offered}
        for want in steamgames.chosen():
            if want.lower() not in known and want not in known:
                print("  (not installed, so not offered: %s)" % want)
        if not offered:
            print("No Steam games are offered to guests.")
        return 0

    wanted = " ".join(args.game).strip()
    have = steamgames.chosen()
    if args.steam_command == "add":
        match = next((g for g in steamgames.installed()
                      if wanted.lower() in g["name"].lower()
                      or wanted == g["appid"]), None)
        if match is None:
            print("Steam has nothing installed matching %r." % wanted)
            print("Try: fourth-player steam installed")
            return 1
        if any(w.lower() == match["name"].lower() or w == match["appid"]
               for w in have):
            print("%s is already offered." % match["name"])
            return 0
        steamgames.write_chosen(have + [match["name"]])
        print("Guests can now start %s." % match["name"])
        return 0

    kept = [w for w in have if w.lower() != wanted.lower() and w != wanted]
    if len(kept) == len(have):
        print("%r is not on the list." % wanted)
        return 1
    steamgames.write_chosen(kept)
    print("Guests can no longer start %s." % wanted)
    return 0


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
