"""The page's dials and the host's limits, which must be the same numbers.

The setup page carried `max="20000"` on the bitrate box while the host decided
what it would accept in STREAM_LIMITS. Two copies of one fact: raise the host's
ceiling and the page silently refuses to let anybody reach it, which reads as
the setting not working rather than as a stale attribute.

This is the third time a page here has held its own copy of something the host
publishes -- the size dropdown once offered 1600x900, which no host has ever
had -- so what is checked is not only that today's numbers agree, but that the
page takes them from the host at all.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


session = open(os.path.join(ROOT, "fourthplayer", "session.py")).read()
page = open(os.path.join(ROOT, "web", "setup.html")).read()
script = open(os.path.join(ROOT, "web", "setup.js")).read()

block = re.search(r"STREAM_LIMITS = \{(.*?)\n    \}", session, re.S)
check(block is not None, "the host states its limits in one place")
limits = {name: (int(lo), int(hi)) for name, lo, hi in re.findall(
    r'"(\w+)": \((\d+), (\d+)\)', block.group(1) if block else "")}
check("bitrate_kbps" in limits, "including the bitrate")

print()
print("the host allows a bitrate worth having on a fast link")
low, high = limits.get("bitrate_kbps", (0, 0))
check(high >= 50000,
      "the ceiling is at least 50 Mb/s (it is %d kb/s); 20 Mb/s was less than "
      "an ethernet link can carry and less than 1080p60 can use" % high)

print()
print("and the page does not disagree with it")
box = re.search(r'<input id="set-bitrate"[^>]*>', page)
check(box is not None, "the bitrate box exists")
if box:
    attr = re.search(r'max="(\d+)"', box.group(0))
    check(attr is not None, "it has a max at all")
    check(attr and int(attr.group(1)) == high,
          "its max matches the host's ceiling (page %s, host %d)"
          % (attr.group(1) if attr else "none", high))
    floor = re.search(r'min="(\d+)"', box.group(0))
    check(floor and int(floor.group(1)) == low,
          "and its min matches the host's floor (page %s, host %d)"
          % (floor.group(1) if floor else "none", low))

print()
print("the page takes the limits from the host rather than keeping its own")
check("stream.limits" in script,
      "the script reads the limits the host publishes")
check(re.search(r"box\.max = bound\[1\]", script),
      "and applies them to the box, so the attribute in the HTML is only a "
      "starting value and not the authority")
check('"limits"' in session,
      "the host actually publishes them in stream_settings")

print()
print("a screen bigger than 1080p can be asked for")
low_h, high_h = limits.get("height", (0, 0))
check(high_h >= 1440,
      "the host offers more than 1080p (up to %dp); a 2560-wide laptop was "
      "capped below its own panel" % high_h)
sizes = re.search(r"STREAM_SIZES = \{(.*?)\n    \}", session, re.S)
named = [int(h) for h in re.findall(r"(\d+): \(\d+, \d+\)",
                                    sizes.group(1) if sizes else "")]
check(1440 in named, "1440p is one of the named sizes")
check(all(h <= high_h for h in named),
      "and no named size is above the limit, which could only be refused: %s"
      % named)

print()
print("and the host says how big its own desktop is")
# Asking for more than the desktop has is an upscale, not a sharper picture.
# Nothing said so, and the number is only knowable at the host.
check('"desktop"' in session, "stream_settings publishes the desktop size")
check("desktop_size" in open(os.path.join(ROOT, "fourthplayer",
                                          "screen.py")).read(),
      "and there is something that reads it")
check("stream.desktop" in script,
      "the page reads it, so somebody can see they are upscaling")

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
