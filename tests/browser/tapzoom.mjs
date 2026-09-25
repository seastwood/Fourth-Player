/* Double-tapping the picture to zoom into what was tapped.
 *
 * A phone shows a television about as wide as two fingers, and the part
 * somebody wants is often a corner of it. The zoom control exists, but
 * reaching for it means stopping playing; a tap on the thing itself does not.
 *
 * Three things here are easy to get wrong and awkward to find by tapping a
 * phone, which is why they are a decision over plain numbers:
 *
 *   * Two taps close in time, but not so close that they are one press
 *     arriving twice -- the trap the on-screen sticks fell into, where iOS's
 *     compatibility mouse event turned every single tap into a pair.
 *   * Near enough in space to be one gesture. Two deliberate taps at opposite
 *     corners are two taps.
 *   * Not while anything is being driven: with the keyboard or pointer live, a
 *     double tap is a double *click* somebody is sending to the machine, and
 *     swallowing it would take away the gesture that opens everything on a
 *     desktop.
 */
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
let bad = 0;
const check = (cond, what) => {
  console.log((cond ? "  ok   " : "  FAIL ") + what);
  if (!cond) bad += 1;
};

const body = src.slice(src.indexOf("const TAP_ZOOM_MS"),
                       src.indexOf('el("screen").addEventListener'));
const F = new Function(body + `; return {
  isPictureDoubleTap, MS: TAP_ZOOM_MS, MIN: TAP_ZOOM_MIN_MS,
  SLOP: TAP_ZOOM_SLOP, TO: TAP_ZOOM_TO };`)();
const tap = (x, y, at, last) => F.isPictureDoubleTap(x, y, at, last);
const first = { x: 100, y: 100, at: 1000 };

console.log("two quick taps in the same place");
check(tap(100, 100, 1000 + 150, first) === true, "are a double tap");
check(tap(100, 100, 1000, null) === false,
      "and the first one alone is not -- there is nothing to pair it with");

console.log("\nbut not two taps somebody meant separately");
check(tap(100, 100, 1000 + F.MS + 1, first) === false,
      "past " + F.MS + "ms they are two taps");
check(tap(400, 300, 1000 + 150, first) === false,
      "and two far apart are two taps, however quick");

console.log("\nnor one press arriving twice");
// The fault the on-screen sticks had: iOS sends a compatibility mouse event
// after a touch, and every single tap became a pair.
check(tap(100, 100, 1000 + 10, first) === false,
      "ten milliseconds apart is a duplicate, not a gesture");
check(tap(100, 100, 1000 + F.MIN + 1, first) === true,
      "while just past the minimum is real");

console.log("\nthe slop is generous but bounded");
// A thumb does not land twice in the same place; two things worth zooming at
// are much further apart than this.
check(tap(100 + F.SLOP, 100, 1000 + 150, first) === true, "just inside counts");
check(tap(100 + F.SLOP + 1, 100, 1000 + 150, first) === false,
      "just outside does not");
check(tap(100, 100 + F.SLOP, 1000 + 150, first) === true,
      "and it is a radius rather than one axis");

console.log("\nwhat the gesture does");
const handler = src.slice(src.indexOf('el("screen").addEventListener'),
                          src.indexOf('el("screen").addEventListener') + 1800);
check(/!cursorDriving\(\) && isPictureDoubleTap/.test(handler),
      "nothing happens while the keyboard or pointer is live");
check(/zoom > ZOOM_MIN\) zoomAbout\(ZOOM_MIN/.test(handler),
      "zoomed in, a double tap goes all the way back out");
check(/else zoomAbout\(TAP_ZOOM_TO, x, y\)/.test(handler),
      "and zoomed out, it goes in towards the point that was tapped");
check(F.TO > 1 && F.TO < 4,
      "to somewhere with room left to pinch further: " + F.TO);
check(/lastPictureTap = null;\s*\/\/ spent/.test(handler),
      "and the pair is spent, so three taps are one zoom and not two");

console.log("\na drag is not a tap");
check(/if \(dragged\) \{ dragged = false; lastPictureTap = null; return; \}/
      .test(handler),
      "dragging the picture clears the pairing too -- otherwise letting go "
      + "after a drag would pair with the tap that started it");

console.log("\nand the hud is left as it was found");
// The first tap toggled it and the second toggles it back, so returning early
// on the zoom is what stops the picture being left with it inside out.
check(/back where it started/.test(handler),
      "which is written down, because it is only true by arithmetic");

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
