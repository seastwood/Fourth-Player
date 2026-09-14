/* Buttons that latch: pressed once to hold down, pressed again to let go.
 *
 * Asked for as "the ability for the client to make certain buttons toggle" --
 * a run button held for a whole level, a trigger somebody cannot comfortably
 * keep down.
 *
 * It is entirely the page's business. The frame already says which buttons
 * are down, so a latched button is one this page keeps saying is down; the
 * host, the protocol and the game are unchanged and cannot tell. Which is why
 * this test is all here and none of it is on the host.
 *
 * The edge semantics are the part that goes subtly wrong, so most of this is
 * about them: a latch moves on the rising edge only, and the state may be
 * advanced exactly once per frame.
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const from = app.indexOf("function togglesKey");
const until = app.indexOf("function loadPadMap");
if (from < 0 || until < 0) {
  console.log("FAIL could not find the toggle helpers in app.js");
  process.exit(1);
}
const source = app.slice(from, until);

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

const STANDARD_KEYS = Array.from({ length: 17 }, (_, i) => "b" + i);

function build(store) {
  const localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
  };
  const body = source
    + "; return { togglesFor, saveToggles, newLatch, clearLatch, toggled };";
  return new Function("localStorage", "STANDARD_KEYS", body)(
    localStorage, STANDARD_KEYS);
}

/* A pad with exactly these button indices held. */
const pad = (...down) => ({
  index: 2, connected: true, id: "8BitDo Micro gamepad", mapping: "standard",
  buttons: Array.from({ length: 17 }, (_, i) => ({
    pressed: down.includes(i), value: down.includes(i) ? 1 : 0 })),
  axes: [0.1, 0.2, 0.3, 0.4, 0, 0],
});

const NAME = "8BitDo Micro gamepad";
const stored = (list) => ({ ["fp-padtoggle:" + NAME]: JSON.stringify(list) });

console.log("nothing configured");
let fp = build({});
let latch = fp.newLatch();
let out = fp.toggled(pad(0), NAME, latch, true);
check(out.buttons[0].pressed,
      "a pad with no latching buttons is passed straight through");

console.log("\none press holds it down, the next lets it go");
fp = build(stored([7]));                       // RT
latch = fp.newLatch();
out = fp.toggled(pad(7), NAME, latch, true);
check(out.buttons[7].pressed, "pressing it puts it down");
out = fp.toggled(pad(), NAME, latch, true);
check(out.buttons[7].pressed,
      "letting go of the controller leaves it down -- that is the whole point");
out = fp.toggled(pad(), NAME, latch, true);
check(out.buttons[7].pressed, "and it stays down while nothing happens");
out = fp.toggled(pad(7), NAME, latch, true);
check(!out.buttons[7].pressed, "pressing it again lets it go");
out = fp.toggled(pad(), NAME, latch, true);
check(!out.buttons[7].pressed, "and it stays up");

console.log("\nholding it is one press, not a stream of them");
// 125 frames a second: without an edge, a held button would flip 125 times a
// second and land on whichever side the thumb happened to come off.
fp = build(stored([7]));
latch = fp.newLatch();
let flips = 0, was = false;
for (let frame = 0; frame < 40; frame++) {
  const now = fp.toggled(pad(7), NAME, latch, true).buttons[7].pressed;
  if (now !== was) flips++;
  was = now;
}
check(flips === 1, `forty frames of holding it flip it once: ${flips}`);

console.log("\npainting the panel is not pressing");
// The panel paints the same pad it sends. Counting that as a press would flip
// every toggle twice, which reads as the feature simply not working.
fp = build(stored([7]));
latch = fp.newLatch();
fp.toggled(pad(7), NAME, latch, false);
fp.toggled(pad(7), NAME, latch, false);
check(!fp.toggled(pad(), NAME, latch, false).buttons[7].pressed,
      "advance:false never moves the latch, however often it is called");
check(fp.toggled(pad(7), NAME, latch, true).buttons[7].pressed,
      "and the send that follows still sees the press as the first one");

console.log("\nevery other button is untouched");
fp = build(stored([7]));
latch = fp.newLatch();
out = fp.toggled(pad(0, 7), NAME, latch, true);
check(out.buttons[0].pressed, "a button that does not latch is still held");
out = fp.toggled(pad(), NAME, latch, true);
check(!out.buttons[0].pressed && out.buttons[7].pressed,
      "and lets go normally while the latched one stays down");
check(out.axes[0] === 0.1 && out.axes[3] === 0.4, "the sticks are carried through");

console.log("\ntwo seats do not share a latch");
// Two people on one machine. A shared latch would have each of them lifting
// the other's button.
fp = build(stored([7]));
const mine = fp.newLatch(), theirs = fp.newLatch();
fp.toggled(pad(7), NAME, mine, true);
check(fp.toggled(pad(), NAME, mine, true).buttons[7].pressed,
      "mine is down");
check(!fp.toggled(pad(), NAME, theirs, true).buttons[7].pressed,
      "and theirs is not, having never been pressed");

console.log("\nturning it off lets go of what it was holding");
// A latched button that is forgotten while still held would stay down on the
// television with nothing left able to lift it.
const store = stored([7]);
fp = build(store);
latch = fp.newLatch();
check(fp.toggled(pad(7), NAME, latch, true).buttons[7].pressed, "down first");
fp.saveToggles(NAME, []);
out = fp.toggled(pad(), NAME, latch, true);
check(!out.buttons[7].pressed,
      "with the list emptied the button comes back up rather than sticking");
check(Object.keys(latch.on).length === 0, "and the latch is cleared, not merely ignored");

console.log("\nclearLatch releases everything");
fp = build(stored([4, 7]));
latch = fp.newLatch();
fp.toggled(pad(4, 7), NAME, latch, true);
fp.clearLatch(latch);
out = fp.toggled(pad(), NAME, latch, true);
check(!out.buttons[4].pressed && !out.buttons[7].pressed,
      "both come up, which is what leaving the page has to do");

console.log("\nwhat comes back from storage is not trusted");
fp = build({ ["fp-padtoggle:" + NAME]: JSON.stringify([7, 99, -1, "x", null]) });
check(JSON.stringify(fp.togglesFor(NAME)) === "[7]",
      "indices off the end of the button list are dropped: "
      + JSON.stringify(fp.togglesFor(NAME)));
fp = build({ ["fp-padtoggle:" + NAME]: "not json at all" });
check(fp.togglesFor(NAME).length === 0, "and unreadable storage latches nothing");
fp = build({ ["fp-padtoggle:" + NAME]: JSON.stringify({ a: 1 }) });
check(fp.togglesFor(NAME).length === 0, "nor does something that is not a list");

console.log("\nthe list is kept per controller");
// Two people on one machine hold different controllers, and the pad that
// wants a latched trigger is not necessarily the one this page sits on.
fp = build(stored([7]));
latch = fp.newLatch();
const other = fp.toggled(pad(7), "Some Other Pad", latch, true);
check(other.buttons[7].pressed,
      "a pad with no list of its own is held while the thumb is on it");
check(!fp.toggled(pad(), "Some Other Pad", latch, true).buttons[7].pressed,
      "and comes straight back up -- this controller's list does not latch it");

console.log("");
if (fails) {
  console.log(`padtoggle: ${fails} FAILED`);
  process.exit(1);
}
console.log("padtoggle: all ok");
