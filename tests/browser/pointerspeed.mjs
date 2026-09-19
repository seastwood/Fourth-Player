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
  // Movements are queued in the order the mouse made them now, rather than
  // summed into one, so the harness holds the queue deskMoved pushes onto.
  const moves = [];
  const store = new Map();
  if (stored !== undefined) store.set("fp:pointer-speed", stored);
  const localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, v),
  };
  let flushes = 0;
  const fns = new Function(
    "deskPending", "deskSoon", "pictureBox", "zoom", "localStorage",
    "deskMoves",
    "let cursorU = 0.5, cursorV = 0.5;"
    + app.slice(from, until)
    + "; return { deskMoved, setDeskSpeed, speed: () => deskSpeed,"
    + " cursor: () => [cursorU, cursorV] };")(
      pending, () => { flushes++; }, () => ({ width: 1000, height: 1000 }), 1,
      localStorage, moves);
  return { pending, fns, store, moves, flushed: () => flushes };
}

console.log("the default is one to one");
// Movements queue in the order the mouse made them rather than summing into
// one pending pair: the browser had already summed them once, and undoing
// that is the point -- an acceleration curve applied to one lump of eight
// pixels does not give what it gives applied to eight movements of one.
let h = harness();
check(h.fns.speed() === 1, "nothing stored means normal speed");
h.fns.deskMoved(10, -4);
check(h.moves[0].dx === 10 && h.moves[0].dy === -4,
      `unscaled motion passes through: ${h.moves[0].dx},${h.moves[0].dy}`);

console.log("\na faster setting moves the console pointer further");
h = harness("2");
check(h.fns.speed() === 2, "the stored value is read back");
h.fns.deskMoved(10, -4);
check(h.moves[0].dx === 20 && h.moves[0].dy === -8,
      `10 becomes 20: ${h.moves[0].dx},${h.moves[0].dy}`);

console.log("\nand a slower one moves it less");
h = harness("0.5");
h.fns.deskMoved(10, 10);
check(h.moves[0].dx === 5 && h.moves[0].dy === 5,
      `10 becomes 5: ${h.moves[0].dx},${h.moves[0].dy}`);

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

console.log("\nand it is somewhere a person with a mouse would look");
// It was first put in the controller panel, among the haptics -- which is the
// one place somebody with a mouse and keyboard would never think to open.
// Asked as: how do I edit sensitivity for a regular connected mouse/keyboard
// setup?
const html = readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");
const deskPanel = html.slice(html.indexOf('id="session-desk"'),
                             html.indexOf('id="session-reshare"'));
check(/id="desk-speed"/.test(deskPanel),
      "the control is inside the panel headed Keyboard and mouse");
check(/Keyboard and mouse/.test(deskPanel), "which is what that panel is");
const padPanel = html.slice(html.indexOf('<div id="pads"'),
                            html.indexOf('id="session-desk"'));
check(!/id="desk-speed"/.test(padPanel),
      "and not in the controller panel, where it was and where nobody with a "
      + "mouse would look");

console.log("\nthe movements a mouse made are kept apart, not summed");
// The browser had already summed them: Chrome delivers mousemove once per
// animation frame with everything since the last one folded in, whatever rate
// the mouse reports at. The host was measured receiving them every 16.6ms --
// the refresh, not the mouse -- so a hand moving steadily arrived as sixty
// jumps a second, and Windows applied its acceleration curve once to each
// lump instead of to each real movement. A pointer whose gain depends on how
// the browser chopped the motion up is a pointer that does not track the
// hand, which is what "cursor control feels a little jittery" is.
h = harness();
h.fns.deskMoved(3, 0);
h.fns.deskMoved(4, 0);
h.fns.deskMoved(5, 0);
check(h.moves.length === 3,
      `three movements stay three: ${h.moves.length}`);
check(h.moves.map((m) => m.dx).join() === "3,4,5",
      `and in the order they were made: ${h.moves.map((m) => m.dx).join()}`);
// The page asks the browser for them rather than taking the summary.
check(app.includes("getCoalescedEvents"),
      "and the page asks the browser for the ones it merged");
check(app.includes("const DESK_MOVE_RUN"),
      "with a bound on how many share one message, so a fast mouse does not "
      + "make an unbounded one");


process.exit(fails ? 1 : 0);
