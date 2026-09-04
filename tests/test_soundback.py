"""Sound that comes back without anybody being asked to fix it.

Autoplay with sound needs a user gesture. A guest who *resumes* a session has
made none, so the picture arrives silent through no fault of theirs -- and the
answer used to be a button reading "Sound off - Tap", which asks somebody to
understand a browser policy before they can hear the game.

They do not have to. The rule is that a gesture must have happened, not that
it must have been aimed at the sound, and somebody who joined to play is about
to press something. So the next press -- a pad button, a tap, a key -- is
taken as the gesture and the sound is asked for again behind their back.

Three things here are worth pinning down, because each is a way this could go
quietly wrong:

  * The unmute has to happen *inside* the gesture handler. A browser stops
    counting a gesture the moment the call is deferred, so an `await` in the
    wrong place would leave this looking correct and working never.
  * Watching for the gesture must not consume it. These listeners sit in the
    capture phase, ahead of everything, and a press on the on-screen pad has
    to reach the pad regardless.
  * Volume at zero is somebody switching their own sound off on purpose. It is
    never chased, never restored, never touched -- that is the one case where
    silence is the correct outcome.

The button is kept for the case where a gesture happens and the sound still
does not come back, which is the only time it was ever the answer.
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


source = open(os.path.join(ROOT, "web", "app.js")).read()


def lift(name):
    """One function's source, by brace counting."""
    start = source.index("function " + name + "(")
    depth = 0
    for j in range(source.index("{", start), len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[start:j + 1]
    raise AssertionError(name)


print("the shape of it, without needing a browser")

gestures = re.search(r"const SOUND_GESTURES = (\[[^\]]*\]);", source)
check(bool(gestures), "the gestures it listens for are stated in one place")
listed = json.loads(gestures.group(1).replace("'", '"')) if gestures else []
check("pointerdown" in listed and "keydown" in listed,
      "a touch and a key both count as a gesture, got %s" % listed)

chase = lift("chaseSound") if "function chaseSound(" in source else ""
check("passive: true" in chase and "capture: true" in chase,
      "the listeners are passive and capture-phase, so a pad press still "
      "reaches the pad")
check("showHud" not in lift("startPlayback"),
      "starting up no longer forces the controls open to show a sound button")

if not shutil.which("node"):
    print("\nSKIPPED the behaviour: node is not installed")
    sys.exit(1 if fails else 0)

# --- run the real functions, against a browser that refuses ---

harness = """
'use strict';
%(code)s

function run(opts) {
  const log = [];
  let unmuteHidden = true, hudShown = false, playing = 0;
  let inGesture = false, playedInGesture = false;

  const listeners = {};
  global.document = {
    addEventListener(kind, fn, opts) {
      (listeners[kind] = listeners[kind] || []).push({ fn, opts });
    },
    removeEventListener(kind, fn) {
      listeners[kind] = (listeners[kind] || []).filter((l) => l.fn !== fn);
    },
  };
  global.video = {
    muted: true, volume: opts.saved,
    play() {
      playing++;
      if (inGesture) playedInGesture = true;
      // The browser refuses an unmuted play until a gesture has happened --
      // and, if `neverAllows`, refuses even then.
      if (!this.muted && (!opts.gestureHappened || opts.neverAllows)) {
        return Promise.reject(new Error("NotAllowedError"));
      }
      return Promise.resolve();
    },
  };
  global.el = (id) => ({
    set hidden(v) { if (id === "unmute") unmuteHidden = v; },
    get hidden() { return id === "unmute" ? unmuteHidden : true; },
  });
  global.savedVolume = () => opts.saved;
  global.showHud = () => { hudShown = true; };
  global.hideHud = () => {};
  global.chasingSound = false;

  chaseSound();
  const armed = Object.keys(listeners).filter((k) => listeners[k].length);

  // The guest presses something. Everything the page hears, it hears here.
  opts.gestureHappened = true;
  inGesture = true;
  (listeners["pointerdown"] || []).forEach((l) => l.fn({}));
  inGesture = false;

  return new Promise((done) => setImmediate(() => setImmediate(() => done({
    armed, unmuteHidden, hudShown, playing, playedInGesture,
    muted: global.video.muted, volume: global.video.volume,
    stillListening: Object.keys(listeners).some((k) => listeners[k].length),
  })))); 
}

(async () => {
  const out = {};
  out.ordinary = await run({ saved: 1, gestureHappened: false });
  out.silenced = await run({ saved: 0, gestureHappened: false });
  out.stubborn = await run({ saved: 1, gestureHappened: false,
                             neverAllows: true });
  console.log(JSON.stringify(out));
})();
""" % {"code": "\n\n".join([lift("soundWanted"), lift("bringSoundBack"),
                            lift("chaseSound"),
                            gestures.group(0) if gestures else ""])}

proc = subprocess.run([shutil.which("node"), "-e", harness],
                      capture_output=True, text=True)
if proc.returncode != 0:
    print("  FAIL  node could not run it:\n" + proc.stderr.strip()[-1500:])
    sys.exit(1)
out = json.loads(proc.stdout.strip().splitlines()[-1])

print("\na guest who resumes, and then presses something")
one = out["ordinary"]
check("pointerdown" in one["armed"], "it is waiting on the next press")
check(one["muted"] is False, "the sound is on again")
check(one["unmuteHidden"] is True, "and no button was ever shown for it")
check(one["hudShown"] is False, "nor were the controls opened over the game")
check(one["playedInGesture"] is True,
      "play() ran inside the gesture, which is the only reason it works")
check(one["stillListening"] is False, "it stops listening once it has won")

print("\nsomebody who turned their own volume down to nothing")
off = out["silenced"]
check(off["armed"] == [], "nothing is watched for; their choice is left alone")
check(off["muted"] is True, "and nothing unmutes behind their back")
check(off["volume"] == 0, "the volume they chose is still the volume")

print("\na browser that refuses even after a gesture")
hard = out["stubborn"]
check(hard["unmuteHidden"] is False,
      "the button appears, because now it is the only way through")
check(hard["muted"] is True, "and the picture keeps playing, muted")
check(hard["stillListening"] is False,
      "it stops chasing rather than retrying on every press forever")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_soundback: all ok")
