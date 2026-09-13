/* Scrolling a window on the host from a touchscreen.
 *
 * Reported as: with the keyboard and mouse enabled from a phone, there is no
 * way to scroll in Files or the browser on the console. The host has always
 * been able to -- {"t":"w"} becomes REL_WHEEL on the virtual mouse -- but the
 * page only ever sent one from a real `wheel` event, and a touchscreen raises
 * none. So the pointer could be moved, clicked and typed at, and anything that
 * needed scrolling could not be scrolled at all.
 *
 * Two fingers dragged on the glass now send wheel notches, on both the pointer
 * path and the gesture path Safari uses.
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

const from = app.indexOf("const DESK_TOUCH_NOTCH");
const until = app.indexOf("function deskWheeled");
const NOTCH = Number(/const DESK_TOUCH_NOTCH = (\d+)/.exec(app)[1]);

function harness() {
  const pending = { dx: 0, dy: 0, wdx: 0, wdy: 0 };
  let flushes = 0;
  const fns = new Function("deskPending", "deskSoon",
    app.slice(from, until) + "; return { deskScrollBy };")(
      pending, () => { flushes++; });
  return { pending, fns, flushed: () => flushes };
}

// -- the signs, which are the easy thing to get backwards -----------------
//
// A wheel's own deltaY is positive when scrolling down, and deskWheeled
// SUBTRACTS it. A finger dragged up is a negative dy and means the same
// "scroll down", so this ADDS. End to end: finger up -> wdy negative ->
// REL_WHEEL negative -> the content moves up, which is what a touchscreen has
// taught everybody to expect.
let h = harness();
h.fns.deskScrollBy(0, -NOTCH);            // fingers dragged up one notch
check(h.pending.wdy < 0,
      `dragging up scrolls down, as a touchscreen does: wdy ${h.pending.wdy}`);
check(Math.abs(h.pending.wdy) === 1,
      `${NOTCH}px of finger is one notch: ${h.pending.wdy}`);

h = harness();
h.fns.deskScrollBy(0, NOTCH * 2);         // dragged down two notches
check(h.pending.wdy === 2, `dragging down scrolls up: ${h.pending.wdy}`);

// Sideways follows the finger too, and is the other sign again.
h = harness();
h.fns.deskScrollBy(NOTCH, 0);             // dragged right
check(h.pending.wdx === -1,
      `dragging right scrolls left, following the finger: ${h.pending.wdx}`);

// -- small movements are kept, not thrown away ----------------------------
h = harness();
for (let i = 0; i < 10; i++) h.fns.deskScrollBy(0, NOTCH / 10);
check(Math.abs(h.pending.wdy - 1) < 1e-9,
      `ten tenths of a notch add up to one: ${h.pending.wdy.toFixed(3)}`);
check(h.flushed() === 10, "and every one of them asks to be sent");

// -- and it is in a range that feels like scrolling ----------------------
//
// A notch is about three lines, call it 50px of content. Much above that and
// the content crawls behind the hand, which is what 100 did and what was
// reported; much below and a thumb twitch throws the page across.
check(NOTCH >= 20 && NOTCH <= 60,
      `a notch is ${NOTCH}px of finger, which moves content about `
      + `${(50 / NOTCH).toFixed(1)}x as fast as the hand`);

// -- it must not touch the pointer ----------------------------------------
check(h.pending.dx === 0 && h.pending.dy === 0,
      "scrolling moves no pointer, so the cursor stays where it was put");

// -- wired into both paths ------------------------------------------------
check(/if \(twoMode === "drag" && cursorDriving\(\)\) \{/.test(app),
      "the pointer path scrolls the host when two fingers drag while driving");
check(/if \(gestureMode === "drag" && cursorDriving\(\)\) \{/.test(app),
      "and so does Safari's gesture path, which is the one a phone takes");
// And only while driving: otherwise two fingers still move the picture.
check(/twoMode === "drag" && cursorDriving\(\)/.test(app)
      && !/twoMode === "drag"\)\s*\{\s*\n\s*deskScrollBy/.test(app),
      "but only while the keyboard and mouse are held, so the picture still"
      + " pans otherwise");

console.log("");
if (fails) { console.log(`deskscroll: ${fails} FAILED`); process.exit(1); }
console.log("deskscroll: all ok");
