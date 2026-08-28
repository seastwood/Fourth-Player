"""Setting the GPU clocks from inside a service that cannot use sudo.

The unit sets NoNewPrivileges, which is the point of it, and sudo refuses
outright under that flag -- "the no new privileges flag is set, which prevents
sudo from running as root", exit status 1. That was logged as a warning at
every session start and otherwise ignored, so the clocks had never once been
set on the machine this was built for: the card idles at 300 MHz, where the
same encode runs at 23 fps instead of 36.

So the helper is handed to the service manager as its own transient unit,
outside this service's confinement -- the same escape the launcher uses to
start games. This checks the ways it will try, in order.
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from fourthplayer import gpu

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


ways = gpu._privileged("high")
described = [name for name, _ in ways]

print("it tries the sandbox escape before plain sudo")
if shutil.which("systemd-run"):
    check(described and described[0] == "a transient unit",
          "the transient unit is tried first: %s" % described)
    first = ways[0][1]
    check("systemd-run" in first[0], "and it is systemd-run that runs it")
    check("--user" in first, "as the user's own manager, not the system one")
    check(first[-3:] == ["sudo", "-n", gpu.HELPER] or first[-4:-1] == ["sudo", "-n", gpu.HELPER],
          "with the helper at the end: %s" % " ".join(first[-4:]))
    check(first[-1] == "high", "and the level it was asked for")
else:
    print("  --   systemd-run is not installed here")

print("and plain sudo is still there for a machine without one")
check(described[-1] == "sudo", "the last resort is a direct sudo: %s" % described)
direct = ways[-1][1]
check(direct == ["sudo", "-n", gpu.HELPER, "high"], "run exactly as before")

print("the level is still checked before anything is run")
try:
    gpu.set_level("; rm -rf /")
    check(False, "a level that is not one of the three is refused")
except ValueError:
    check(True, "a level that is not one of the three is refused")

print(("FAILED: %d" % len(fails)) if fails else "test_clocks: all ok")
sys.exit(1 if fails else 0)
