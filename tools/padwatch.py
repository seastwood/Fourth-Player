#!/usr/bin/env python3
"""Watch a session's virtual pads the way RetroArch would, and report.

This is the last link in the chain. Everything else can pass while the pad
stays inert -- the frames arrive, the code runs, and nothing reaches the
kernel. So this opens the device by name with plain evdev, exactly as any game
would, and says what it saw move.

    python3 tools/padwatch.py --seconds 12
"""
import argparse
import select
import sys
import time

import evdev
from evdev import ecodes as e


def find_pads(prefix="Fourth Player"):
    found = []
    for path in evdev.list_devices():
        try:
            device = evdev.InputDevice(path)
        except OSError:
            continue
        if device.name.startswith(prefix):
            found.append(device)
    return sorted(found, key=lambda d: d.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=12.0)
    args = parser.parse_args()

    pads = find_pads()
    if not pads:
        print("no Fourth Player pads exist -- is a session open?", file=sys.stderr)
        return 2
    print("watching:")
    for pad in pads:
        print(f"  {pad.name:<20} {pad.path}  ({pad.info.vendor:04x}:{pad.info.product:04x})")

    seen = {pad.path: {"buttons": set(), "axes": {}, "events": 0} for pad in pads}
    by_fd = {pad.fd: pad for pad in pads}
    deadline = time.monotonic() + args.seconds
    print(f"\nlistening for {args.seconds:.0f}s ...")

    while time.monotonic() < deadline:
        ready, _, _ = select.select(by_fd, [], [], 0.25)
        for fd in ready:
            pad = by_fd[fd]
            record = seen[pad.path]
            try:
                for event in pad.read():
                    record["events"] += 1
                    if event.type == e.EV_KEY and event.value == 1:
                        record["buttons"].add(
                            e.BTN.get(event.code, [str(event.code)])[0]
                            if isinstance(e.BTN.get(event.code), list)
                            else str(e.BTN.get(event.code, event.code)))
                    elif event.type == e.EV_ABS and event.value != 0:
                        name = e.ABS.get(event.code, str(event.code))
                        record["axes"][name] = event.value
            except BlockingIOError:
                pass

    print("\nwhat the kernel saw")
    live = 0
    for pad in pads:
        record = seen[pad.path]
        if record["events"]:
            live += 1
        print(f"  {pad.name}")
        print(f"    events   {record['events']}")
        print(f"    buttons  {', '.join(sorted(record['buttons'])) or '(none)'}")
        print(f"    axes     {record['axes'] or '(none moved)'}")

    print("\nPASS -- input reached a real device" if live
          else "\nFAIL -- nothing reached any pad")
    return 0 if live else 1


if __name__ == "__main__":
    sys.exit(main())
