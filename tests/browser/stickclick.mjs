/* Pressing an on-screen stick in, which on glass cannot be pressed.
 *
 * L3 and R3 are the two buttons a drawn stick has nowhere to put: the well is
 * already the stick, and a button beside it is one more thing to find with a
 * thumb that is busy steering. So it is a quick double tap in the middle of
 * the well -- the one part that means nothing while steering, because a thumb
 * resting there is inside the dead zone anyway.
 *
 * The rule is all timing and two positions, and every one of them is easy to
 * get wrong in a way only a finger notices. So it is a decision over plain
 * numbers, tested here rather than by tapping a phone:
 *
 *   * A tap out at the rim is steering, and must not count as the first half
 *     of anything -- a flick to the edge followed by a tap in the middle is
 *     two deliberate movements, not a click.
 *   * Two taps in the middle, far apart in time, are two taps.
 *   * Three taps are not one and a half clicks.
 */
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
let bad = 0;
const check = (cond, what) => {
  console.log((cond ? "  ok   " : "  FAIL ") + what);
  if (!cond) bad += 1;
};

const body = src.slice(src.indexOf("const STICK_CLICK_CENTRE"),
                       src.indexOf("function stickOffset"))
  + src.slice(src.indexOf("function isStickClick"),
              src.indexOf("function moveStick"));
const { isStickClick, CENTRE, MS } = new Function(`
  ${body}
  return { isStickClick, CENTRE: STICK_CLICK_CENTRE, MS: STICK_CLICK_MS };
`)();

const touch = (id, offset, at, taps) => isStickClick(id, offset, at, taps, "touch");

console.log("two quick taps in the middle are a click");
let taps = {};
check(touch("L", 0.1, 1000, taps) === false, "the first tap is not");
check(touch("L", 0.1, 1100, taps) === true, "the second one is");

console.log("\nbut one press arriving twice is not two presses");
// iOS sends a compatibility *mouse* pointerdown a few hundred milliseconds
// after a touch, for pages written before pointer events existed. That is
// inside the window, so every single tap arrived as a pair and pressed the
// stick in -- which is exactly what was reported.
taps = {};
check(touch("L", 0.1, 0, taps) === false, "the touch is the first tap");
check(isStickClick("L", 0.1, 300, taps, "mouse") === false,
      "and the compatibility mouse event that follows it is not the second: "
      + "a finger coming back would be a touch again");
// A real mouse can still double-click, on a desktop with no touch at all.
taps = {};
check(isStickClick("L", 0.1, 0, taps, "mouse") === false, "a mouse first tap");
check(isStickClick("L", 0.1, 150, taps, "mouse") === true,
      "and a mouse second tap still clicks");

console.log("\nand two events too close together are one press, not two");
// No finger leaves the glass and returns in forty milliseconds.
taps = {};
touch("L", 0.1, 0, taps);
check(touch("L", 0.1, 20, taps) === false,
      "twenty milliseconds apart is a duplicate, not a double tap");
check(touch("L", 0.1, 100, taps) === true,
      "while a real one just after it still counts");

console.log("\nand a third tap is not another one");
check(touch("L", 0.1, 1150, taps) === false,
      "the pair is spent, so three fast taps are one click and not two");

console.log("\nslow taps are two taps");
taps = {};
touch("L", 0.1, 0, taps);
check(touch("L", 0.1, MS + 1, taps) === false,
      "past " + MS + "ms it is a fresh first tap");
// ...and that fresh first tap still counts as one, or a slow double tap
// followed by a quick one would never fire.
check(touch("L", 0.1, MS + 100, taps) === true,
      "which the next quick one completes");

console.log("\na tap at the rim is steering, not half a gesture");
taps = {};
check(touch("L", 0.9, 0, taps) === false, "the rim tap is not a click");
check(touch("L", 0.1, 50, taps) === false,
      "and a middle tap straight after it is not either -- a flick to the "
      + "edge and back is two movements somebody meant");

console.log("\neach stick counts its own taps");
taps = {};
touch("L", 0.1, 0, taps);
check(touch("R", 0.1, 50, taps) === false,
      "a tap on the left then the right is not a click on either");
check(touch("L", 0.1, 100, taps) === true,
      "while the left one's own pair still completes");

console.log("\nthe boundary is where it says it is");
taps = {};
touch("L", CENTRE - 0.01, 0, taps);
check(touch("L", CENTRE - 0.01, 100, taps) === true,
      "just inside counts");
taps = {};
touch("L", CENTRE + 0.01, 0, taps);
check(touch("L", CENTRE + 0.01, 100, taps) === false,
      "just outside does not");

console.log("\nan unmeasurable well is never the middle");
// stickOffset answers 2 for a well with no width -- laid out but not painted,
// or hidden. Treating that as the centre would fire a click on any tap.
check(touch("L", 2, 0, {}) === false, "a width of nothing is not a tap");

console.log("\nand the page holds it rather than pulsing it");
check(src.includes("stickClicked[event.pointerId] = bit"),
      "the bit is held against the pointer that started it");
check(src.includes("setBit(stickClicked[event.pointerId], false)"),
      "and let go when that finger leaves -- click-to-sprint is useless as a "
      + "pulse, and the stick keeps moving under a held click");
const release = src.slice(src.indexOf("function releaseAllSticks"),
                          src.indexOf("function wireSticks"));
check(release.includes("stickClicked"),
      "letting go of everything lets go of this too: a held L3 outliving that "
      + "is a character stuck crouching with nothing to explain it");

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
