/* Drawing the page sideways, where the browser will not turn the screen.
 *
 * Asked for as: a way to lock iOS Safari into landscape -- it reverts to
 * portrait no matter what, even added as an app.
 *
 * There is no API for it. Safari 16.4 added screen.orientation's type, angle
 * and event and left lock() out, and it is still absent, installed web app or
 * not. So the page draws itself sideways instead: a quarter turn, with the
 * viewport's height for the stage's width.
 *
 * The part that can go quietly wrong is the touch directions. A finger still
 * moves in the screen's frame while everything it moves is in the stage's, so
 * a drag has to be turned with the page -- and if that is forgotten, dragging
 * moves the cursor at right angles to the finger.
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const css = readFileSync(new URL("../../web/style.css", import.meta.url), "utf8");

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

const from = app.indexOf("let turned = false;");
// Stopped before the real canTurn, which sits between this block and
// savedOrient. Slicing past it pulled the real one in, where it shadowed the
// fake being injected and answered from a window object the test does not
// have -- so "the browser can lock" could not be tested at all.
const until = app.indexOf("function canTurn");
check(from > 0, "the turning code was found");

function harness({ orient = "landscape", canLock = false, w = 400, h = 800 }) {
  const root = { classList: { toggled: null, toggle(n, on) { this.toggled = [n, on]; } } };
  return new Function(
    "savedOrient", "canTurn", "window", "document", "applyZoom", "fitStage",
    app.slice(from, until)
    + "; return { wantsTurning, paintTurned, turnedDelta,"
    + " isTurned: () => turned, root: document.documentElement };")(
      () => orient, () => canLock,
      { visualViewport: { width: w, height: h } },
      { documentElement: root }, () => {}, () => {});
}

console.log("it only happens where it is the only option left");
check(harness({ canLock: true }).wantsTurning() === false,
      "never when the browser can really lock -- a lock beats a transform in "
      + "every way");
check(harness({ orient: "any" }).wantsTurning() === false,
      "never when nobody asked for landscape");
check(harness({ orient: "portrait" }).wantsTurning() === false,
      "nor when they asked for portrait");
check(harness({ w: 800, h: 400 }).wantsTurning() === false,
      "and not when the screen is already landscape, which is the common case "
      + "and must cost nothing");
check(harness({}).wantsTurning() === true,
      "but yes on a portrait screen that cannot lock, when landscape was asked "
      + "for");

console.log("\nthe drag is turned with the page");
// rotate(90deg) turns the element clockwise, so the stage's own axes point
// elsewhere on the screen: the stage's +x (right) now points down the screen,
// and the stage's +y (down) now points left. Reading that backwards, a finger
// moving down the screen is moving right across the stage, and a finger
// moving right is moving *up* it.
//
// My first version of this test asserted the opposite for the second one, and
// the code was right. Worth keeping the derivation here rather than the
// answer: getting it wrong sends the cursor at right angles to the finger,
// which is obvious on a phone and invisible in a diff.
let h = harness({});
h.paintTurned();
check(h.isTurned() === true, "it is on");
let d = h.turnedDelta(0, 10);
check(d.dx === 10 && d.dy === 0,
      `a finger moving down the screen moves right across the stage: `
      + JSON.stringify(d));
d = h.turnedDelta(10, 0);
check(d.dx === 0 && d.dy === -10,
      `and a finger moving right moves up it: ${JSON.stringify(d)}`);

console.log("\nand left exactly alone when nothing is turned");
h = harness({ w: 800, h: 400 });
h.paintTurned();
check(h.isTurned() === false, "not turned");
d = h.turnedDelta(7, -3);
check(d.dx === 7 && d.dy === -3,
      `the identity, so the ordinary path costs a comparison: ${JSON.stringify(d)}`);

console.log("\nthe class is what the stylesheet hangs on");
h = harness({});
h.paintTurned();
check(h.root.classList.toggled[0] === "turned" && h.root.classList.toggled[1] === true,
      "the root element is marked");
check(/html\.turned #stage/.test(css), "and the stylesheet acts on it");
check(/transform: rotate\(90deg\)/.test(css), "with a quarter turn");
check(/width: 100dvh/.test(css) && /height: 100dvw/.test(css),
      "and the sides swapped, in dvh/dvw so the bars coming and going on a "
      + "phone are accounted for");

console.log("\nevery drag path goes through it");
for (const site of ["deskScrollBy(moved.dx, moved.dy)",
                    "panX += moved.dx", "const turn = turnedDelta(now.x - was.x",
                    "deskScrollBy(swipe.dx, swipe.dy)"]) {
  check(app.includes(site), `${site.slice(0, 34)}... is turned`);
}

process.exit(fails ? 1 : 0);
