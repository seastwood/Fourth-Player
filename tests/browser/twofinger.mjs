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

// -- the wiring it depends on --------------------------------------------
check(/if \(twoMode === "zoom"\) \{\s*\n\s*zoomAbout/.test(app),
      "only a settled pinch is allowed to zoom");
check(/twoMode = null;\s*\n\s*twoGapMoved = 0;/.test(app),
      "and every new two-finger gesture is asked afresh");
check(/if \(twoMode\) applyZoom\(\);/.test(app),
      "nothing is applied at all while the answer is still in doubt");

console.log("");
if (fails) { console.log(`twofinger: ${fails} FAILED`); process.exit(1); }
console.log("twofinger: all ok");
