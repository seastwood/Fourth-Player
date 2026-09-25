/* The seat picker must not move somebody who did not ask to be moved.
 *
 * "I just created two controllers again from my iPhone", with the host's log
 * showing this, over and over, every few seconds:
 *
 *   unplugged Fourth Player 1   Guest 1 moved from pad 0 to pad 1
 *   plugged in Fourth Player 1  Guest 1 moved from pad 1 to pad 0
 *
 * Rebuilding a <select> whose native picker is open does not merely close it
 * -- which this file already knew -- on iOS it *commits* whatever is under the
 * finger, and that fires change. The change asks the host for that seat, the
 * host moves the guest and says so, saying so repaints the picker, and the
 * repaint commits again. Each move unplugs one controller and plugs in
 * another, which from the game's side is two controllers appearing.
 */
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
let bad = 0;
const check = (cond, what) => {
  console.log((cond ? "  ok   " : "  FAIL ") + what);
  if (!cond) bad += 1;
};

const paint = src.slice(src.indexOf("function paintSeats()"),
                        src.indexOf("function paintSeats()") + 3000);

console.log("the list is not rebuilt under somebody's finger");
check(/if \(document\.activeElement === pick\) \{/.test(paint),
      "a picker that has focus is left alone");
check(paint.indexOf("document.activeElement === pick")
      < paint.indexOf('pick.dataset.signature !== signature'),
      "before the rebuild, not after it -- after is too late, the commit has "
      + "already happened");
check(/addEventListener\("blur"[\s\S]{0,120}?paintSeats\(\)/.test(paint),
      "and it is redrawn when the picker closes, so it is never left saying "
      + "something out of date");
check(/\{ once: true \}/.test(paint),
      "with one listener rather than one per repaint");

console.log("\nand asking for the seat you are on is not a move");
const change = src.slice(src.indexOf('el("pads-seat").addEventListener("change"'),
                         src.indexOf('el("pads-seat").addEventListener("change"') + 700);
check(/if \(wanted === myPad\) return;/.test(change),
      "a request that cannot change anything is one more thing that can go "
      + "round in a circle");
check(/if \(isNaN\(wanted\)\) return;/.test(change),
      "and a value that is not a seat is still refused");

console.log("\nthe loop it closes is written down");
check(/commits|commit/.test(paint),
      "because 'do not rebuild a select' reads like tidiness until somebody "
      + "knows what it cost");

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
