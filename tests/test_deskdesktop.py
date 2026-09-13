"""The keyboard-and-mouse desk on a laptop, where the pointer is locked.

Three things reported from a real desktop, all of them consequences of a
pointer lock:

  "the extra utility buttons lay over the video stream" -- they float in a
  corner over the picture, and a locked pointer cannot reach them at all,
  because it is on the console. They fade while captured and come back on
  Escape, which is the same key that gives the pointer back.

  "zooming with the trackpad zooms into one spot and doesn't follow the
  cursor" -- under a lock the browser stops updating clientX and clientY, so
  the zoom aimed at wherever the lock began, for ever. The relative motion
  already being sent to the host is now also added up here as a guess at where
  the console's pointer has got to, and the zoom aims at that. It is an
  estimate -- the host clamps at its own edges and may accelerate -- so it is
  reset to the middle on each capture to bound the drift.

  "scrolling behaves unpredictably, like scrolling and zooming happen at the
  same time" -- because they did. The capture-phase listener sent the notch to
  the host and called preventDefault, which does not stop the page's own wheel
  listener from zooming the picture with the same event. One scroll gesture
  scrolled the window on the console and zoomed the view of it at once. The
  wheel is routed now: a pinch (ctrl held) is the picture's, a scroll is the
  host's, and neither sees the other.

Needs node, not Chrome.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))

node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the page's rules cannot be run.")
    sys.exit(0)

result = subprocess.run(
    [node, os.path.join(HERE, "browser", "deskdesktop.mjs")],
    cwd=os.path.join(HERE, "browser"), text=True)
sys.exit(result.returncode)
