"""The host asks to be scheduled like something that has to keep up.

It was running at BELOW_NORMAL on the Windows machine and had been since it
was installed. Nothing chose that: a scheduled task whose settings do not name
a priority gets task priority 7, and 7 is BELOW_NORMAL_PRIORITY_CLASS. The
installer never named one.

A capture and encode at 60 frames a second is soft-real-time work. Scheduled
below every ordinary process on the machine it loses the CPU to whatever wants
it -- and what wants it is the game being streamed. Reported exactly that way:
"the freeze ups seem to only happen while I am playing a game... if I put the
game in the background, the freeze ups happen less frequently." The same
starvation overran the audio ring buffer, which is why the microphone
indicator kept flickering in the tray.

Runs everywhere. The Windows calls are skipped off Windows, but that the host
*asks*, that the installer names a priority, and that it asks for above-normal
rather than high, are all readable anywhere.
"""
import importlib.machinery
import importlib.util
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, REPO)
from fourthplayer import winpriority                      # noqa: E402

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


print("-- it asks for above normal, not high --")
check(winpriority.ABOVE_NORMAL_PRIORITY_CLASS == 0x00008000,
      "ABOVE_NORMAL_PRIORITY_CLASS is the documented constant")
source = open(os.path.join(REPO, "fourthplayer", "winpriority.py")).read()
check("0x00000080" not in source.split("CLASSES")[0],
      "HIGH is not what is asked for: above the game is the point, above the "
      "window manager and the audio service is how a streaming host makes the "
      "game stutter instead of itself")
check("SetPriorityClass" in source, "it sets the class")
check("GetPriorityClass" in source,
      "and reads it back, so the log says what actually happened rather than "
      "what was requested")

print("\n-- and out of Windows 11's background throttling --")
check("SetProcessInformation" in source, "it asks")
check(winpriority.ProcessPowerThrottling == 4,
      "with the documented information class")
check(winpriority.PROCESS_POWER_THROTTLING_EXECUTION_SPEED == 0x1,
      "naming the execution-speed knob")
check("StateMask=0" in source,
      "and turning it off rather than clearing the control mask, which would "
      "mean 'use the system default' -- the default being the thing escaped")

print("\n-- nothing here may stop the host serving --")
check("except Exception" in source, "every call is best effort")
check("Never raises" in source, "and says so")
out = winpriority.apply()
check(out is None, "apply() returns quietly off Windows, got %r" % out)
check(winpriority.raise_priority() is None or sys.platform.startswith("win"),
      "and so does raise_priority()")
check(winpriority.leave_eco_mode() is False or sys.platform.startswith("win"),
      "and leave_eco_mode()")

print("\n-- the host asks before it builds anything --")
cli = open(os.path.join(REPO, "fourthplayer", "cli.py")).read()
serve = cli.split('if args.command == "serve":')[1][:900]
check("winpriority.apply()" in serve,
      "serve asks on the way in")
check(serve.index("winpriority.apply()") < serve.index("PRESETS"),
      "before the settings are read and the pipeline is built, so the whole "
      "of the startup runs at the priority it will keep")

print("\n-- and a fresh install does not start out below normal --")
ps1 = open(os.path.join(REPO, "install", "windows", "install.ps1")).read()
check("$settings.Priority" in ps1, "the task names a priority")
check("$settings.Priority = 4" in ps1,
      "4, which is normal -- the host raises itself the rest of the way, which "
      "also repairs installs made before this existed")
check("BELOW_NORMAL" in ps1,
      "and says why, because a task priority of 7 meaning below-normal is not "
      "something anybody guesses")

print()
if fails:
    print("FAILURES: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_priority: all ok")
