/* Telling a pinch from a two-finger drag.
 *
 * Reported from a phone: "when I try to scroll by keeping one finger
 * stationary while sliding the other it zooms. Same if I slide with two
 * fingers. Currently I have no way to scroll with a mobile touchscreen
 * device."
 *
 * Every two-finger move used to zoom by the ratio of the finger gap and pan by
 * the middle, both at once, so there was no way to move a zoomed picture with
 * two fingers without also resizing it.
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const from = app.indexOf("const TWO_FINGER_SURE");
const until = app.indexOf("}", app.indexOf("function twoFingerIntent")) + 1;
if (from < 0 || until < 0) {
  console.log("FAIL could not find twoFingerIntent in app.js");
  process.exit(1);
}
const { twoFingerIntent, TWO_FINGER_SURE, PINCH_BIAS } =
  new Function(app.slice(from, until)
    + "; return { twoFingerIntent, TWO_FINGER_SURE, PINCH_BIAS };")();

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

/* A gesture, described the way a thumb makes it. `d` is how far the moving
   finger or fingers travel. */
const bothApart  = (d) => ({ gap: 2 * d, pan: 0 });      // a real pinch
const oneStill   = (d) => ({ gap: d,     pan: d / 2 });  // the reported case
const bothTogether = (d) => ({ gap: 0,   pan: d });      // an ordinary drag

const settle = (g) => twoFingerIntent(g.gap, g.pan, null);

// -- the question is not answered too early -------------------------------
check(settle(bothApart(1)) === null,
      "a gesture of a pixel or two is not called either way yet");
check(settle(bothTogether(TWO_FINGER_SURE / 4)) === null,
      "nor is any gesture below the certainty threshold");
check(TWO_FINGER_SURE > 0 && TWO_FINGER_SURE < 40,
      "and that threshold is small enough to feel immediate");

// -- the three gestures, once there is enough of them ---------------------
check(settle(bothApart(40)) === "zoom",
      "fingers moving apart is a pinch");
// Coming together is the same gesture to this rule: the caller accumulates
// |gap - lastGap|, so the direction is already gone by the time it gets here.
// Said with the same numbers rather than a fake negative, which would only be
// testing that Math.abs had been called somewhere else.
check(settle({ gap: 80, pan: 0 }) === "zoom",
      "and so is fingers coming together -- the distances are the same");
check(settle(oneStill(40)) === "drag",
      "one finger still while the other slides is a DRAG -- the reported case");
check(settle(bothTogether(40)) === "drag",
      "two fingers moving together is a drag");

// -- it stays what it was ------------------------------------------------
check(twoFingerIntent(999, 0, "drag") === "drag",
      "a gesture that started as a drag stays a drag, however the gap wobbles");
check(twoFingerIntent(0, 999, "zoom") === "zoom",
      "and one that started as a pinch stays a pinch");

// -- the bias is what decides the awkward middle case ---------------------
check(PINCH_BIAS >= 2,
      "the bias puts the one-finger-still case on the drag side");
const marginal = { gap: 30, pan: 30 / PINCH_BIAS };
check(twoFingerIntent(marginal.gap, marginal.pan, null) === "drag",
      "exactly on the line counts as a drag, not a zoom");

// -- a real thumb is not steady -------------------------------------------
//
// The first version of this added up |gap - lastGap| frame by frame, which
// sounds equivalent to measuring from the start and is not: a finger resting
// on glass tremors, so that sum climbs even when the fingers never actually
// change their distance apart.
//
// It fails on *slow* gestures specifically, which is why the first round of
// tests here missed it -- they all moved briskly. Measured over these frames:
// 60px in 30 frames accumulates 51 against 60 and is called a drag correctly,
// but 20px in 40 frames accumulates 92 against 20 and is called a pinch. A
// careful scroll is exactly the slow case, and it was reported back as "it is
// still just doing pinch zoom".
//
// Both models are run over the same frames, so the difference is measured
// here rather than asserted.
function dragFrames({ travel = 60, frames = 30, tremor = 2 }) {
  const out = [];
  let seed = 7;
  const noise = () => {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;   // repeatable
    return ((seed / 0x7fffffff) * 2 - 1) * tremor;
  };
  const startGap = 140;
  for (let i = 1; i <= frames; i++) {
    out.push({
      gap: startGap + noise(),              // never actually spreads
      at: { x: 0, y: (travel * i) / frames }, // moves steadily
    });
  }
  return { startGap, startAt: { x: 0, y: 0 }, frames: out };
}

// Slow and deliberate, which is how somebody scrolls when they mean it.
const run = dragFrames({ travel: 20, frames: 40, tremor: 3 });

// How it is done now: measured from where the gesture began.
let net = null;
for (const f of run.frames) {
  net = twoFingerIntent(Math.abs(f.gap - run.startGap),
                        Math.hypot(f.at.x - run.startAt.x,
                                   f.at.y - run.startAt.y), net);
}
check(net === "drag",
      "a slow, tremory two-finger drag is still a drag, measured from where"
      + " it started");

// And the brisk case both models always agreed on, so the slow one above is
// shown to be the difference rather than the whole story.
const brisk = dragFrames({ travel: 60, frames: 30, tremor: 2 });
let fast = null;
for (const f of brisk.frames) {
  fast = twoFingerIntent(Math.abs(f.gap - brisk.startGap),
                         Math.hypot(f.at.x - brisk.startAt.x,
                                    f.at.y - brisk.startAt.y), fast);
}
check(fast === "drag", "so is a brisk one, which never was the problem");

// How it was done before: accumulated frame by frame.
let acc = null, gapSum = 0, panSum = 0, lastGap = run.startGap,
    lastAt = run.startAt;
for (const f of run.frames) {
  gapSum += Math.abs(f.gap - lastGap);
  panSum += Math.hypot(f.at.x - lastAt.x, f.at.y - lastAt.y);
  lastGap = f.gap; lastAt = f.at;
  acc = twoFingerIntent(gapSum, panSum, acc);
}
check(acc === "zoom",
      "while the accumulating version calls that same slow drag a pinch,"
      + " which is why it is not done that way");

// -- the wiring it depends on --------------------------------------------
check(/if \(twoMode === "zoom"\) \{\s*\n\s*zoomAbout/.test(app),
      "only a settled pinch is allowed to zoom");
check(/twoMode = null;\s*\n\s*twoStartGap = pinchGap;/.test(app),
      "and every new two-finger gesture is measured from its own start");
check(/if \(twoMode\) applyZoom\(\);/.test(app),
      "nothing is applied at all while the answer is still in doubt");

console.log("");
if (fails) { console.log(`twofinger: ${fails} FAILED`); process.exit(1); }
console.log("twofinger: all ok");
