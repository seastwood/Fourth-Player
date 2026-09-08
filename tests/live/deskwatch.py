"""Wait for the desk devices to appear, then say what arrives on them."""
import sys, time, threading, evdev
from evdev import ecodes as e

deadline = time.time() + float(sys.argv[1] if len(sys.argv) > 1 else 45)
found = {}
print("watching for the desk devices...", flush=True)
while time.time() < deadline and len(found) < 2:
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        if dev.name.startswith("Fourth Player ") and dev.name not in found and (
                dev.name.endswith("Keyboard") or dev.name.endswith("Mouse")):
            found[dev.name] = dev
            print("appeared: %s at %s" % (dev.name, dev.path), flush=True)
    time.sleep(0.2)

if not found:
    print("NOTHING APPEARED", flush=True)
    sys.exit(1)

seen = []
def watch(dev):
    try:
        for ev in dev.read_loop():
            if ev.type == e.EV_KEY:
                table = evdev.ecodes.KEY if ev.code < 256 else evdev.ecodes.BTN
                name = table.get(ev.code, ev.code)
                if isinstance(name, list): name = name[0]
                seen.append("%s %s=%d" % (dev.name.split()[-1], name, ev.value))
            elif ev.type == e.EV_REL:
                seen.append("%s %s%+d" % (dev.name.split()[-1],
                                          evdev.ecodes.REL[ev.code], ev.value))
    except OSError:
        pass
for dev in found.values():
    threading.Thread(target=watch, args=(dev,), daemon=True).start()

time.sleep(max(2, deadline - time.time()))
print("\n--- %d events ---" % len(seen), flush=True)
for row in seen:
    print("   " + row, flush=True)
