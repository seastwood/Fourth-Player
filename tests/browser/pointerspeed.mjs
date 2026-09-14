/* How far the console's pointer moves for a given movement here.
 *
 * Asked for as "can we make it possible to set the client mouse sensitivity".
 * The number is a client setting because it is about this person's mouse and
 * this person's hand; two guests on one session would not agree on it, and the
 * host cannot know either of their trackpads.
 *
 * What is worth testing is not that a multiply happens, but *where*. Every
 * path that moves the pointer -- a mouse under a pointer lock, a finger
 * dragging the cursor -- arrives at deskMoved as relative motion, and the
 * estimate of where the pointer has reached is kept from the same numbers a
 * few lines below. Scale in one place and not the other and the pointer and
 * the estimate drift apart, which is the bug that made zoom follow a cursor
 * that was not there.
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

const from = app.indexOf("const SPEED_KEY");
const until = app.indexOf("/* Where that guess is on the screen");
check(from > 0 && until > from, "the pointer-speed code was found in app.js");

function harness(stored) {
  const pending = { dx: 0, dy: 0 };
  const store = new Map();
  if (stored !== undefined) store.set("fp:pointer-speed", stored);
  const localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, v),
  };
  let flushes = 0;
  const fns = new Function(
    "deskPending", "deskSoon", "pictureBox", "zoom", "localStorage",
    "let cursorU = 0.5, cursorV = 0.5;"
    + app.slice(from, until)
    + "; return { deskMoved, setDeskSpeed, speed: () => deskSpeed,"
    + " cursor: () => [cursorU, cursorV] };")(
      pending, () => { flushes++; }, () => ({ width: 1000, height: 1000 }), 1,
      localStorage);
  return { pending, fns, store, flushed: () => flushes };
}

console.log("the default is one to one");
let h = harness();
check(h.fns.speed() === 1, "nothing stored means normal speed");
h.fns.deskMoved(10, -4);
check(h.pending.dx === 10 && h.pending.dy === -4,
      `unscaled motion passes through: ${h.pending.dx},${h.pending.dy}`);

console.log("\na faster setting moves the console pointer further");
h = harness("2");
check(h.fns.speed() === 2, "the stored value is read back");
h.fns.deskMoved(10, -4);
check(h.pending.dx === 20 && h.pending.dy === -8,
      `10 becomes 20: ${h.pending.dx},${h.pending.dy}`);

console.log("\nand a slower one moves it less");
h = harness("0.5");
h.fns.deskMoved(10, 10);
check(h.pending.dx === 5 && h.pending.dy === 5,
      `10 becomes 5: ${h.pending.dx},${h.pending.dy}`);

console.log("\nthe estimate of where the pointer is scales with it");
// The whole reason the multiply lives at the top of deskMoved. At double
// speed a 100px movement across a 1000px picture must move the estimate by
// 0.2, not 0.1 -- or the zoom follows a pointer the host does not have.
h = harness("2");
h.fns.deskMoved(100, 0);
const [u] = h.fns.cursor();
check(Math.abs(u - 0.7) < 1e-9,
      `0.5 + (100*2)/1000 = 0.7, got ${u}`);
h = harness("0.5");
h.fns.deskMoved(100, 0);
check(Math.abs(h.fns.cursor()[0] - 0.55) < 1e-9,
      `and at half speed 0.55, got ${h.fns.cursor()[0]}`);

console.log("\nnonsense stored in the browser does not break the pointer");
for (const bad of ["", "0", "-3", "abc", "1e400", "99"]) {
  const bh = harness(bad);
  check(bh.fns.speed() === 1,
        `"${bad}" falls back to normal, not ${bh.fns.speed()}`);
}

console.log("\nand a setting that is chosen is remembered");
h = harness();
h.fns.setDeskSpeed("1.5");
check(h.fns.speed() === 1.5, "it takes a string from a <select>");
check(h.store.get("fp:pointer-speed") === "1.5", "and writes it down");
h.fns.setDeskSpeed("0");
check(h.fns.speed() === 1.5,
      "a refused value leaves the working one alone, rather than stopping "
      + "the pointer dead");

console.log("\nevery movement still asks for a flush");
h = harness("2");
h.fns.deskMoved(1, 1);
check(h.flushed() === 1, "scaling did not swallow the send");

process.exit(fails ? 1 : 0);
