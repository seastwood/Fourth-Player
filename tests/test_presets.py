"""The picture presets, and the arithmetic that makes them necessary.

Reported as: "I increased the resolution to 1080p and increased the bitrate to
4500, but the quality remained kind of poor -- are these settings actually
getting applied?" They were, exactly: the host announced 1920x1080 at 4500 and
was measured delivering 4194 kb/s. The softness was arithmetic. 1080p carries
2.25 times the pixels of 720p, so at a fixed bitrate every pixel gets 44% of
what it had, and reaching for "bigger" gets you "softer".

Four dials that are not independent, with no hint of how they trade against
each other, is a trap. These presets are combinations that hold together, and
this checks they really do: each sits in a sane band of bits per pixel, more
pixels a second always asks for more bitrate, and the setting that prompted
the question falls below the band. Needs node, not Chrome.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))

node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the presets cannot be checked.")
    sys.exit(0)

result = subprocess.run([node, os.path.join(HERE, "browser", "presets.mjs")],
                        cwd=os.path.join(HERE, "browser"), text=True)
sys.exit(result.returncode)
