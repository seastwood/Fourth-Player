/* Holding a mouse button down through the stream.
 *
 * Reported on a MacBook: in Safari, holding left click does not hold -- a
 * shooter fires once instead of continuously -- while a double click holds
 * perfectly, and Chrome on the same machine is flawless. So mousedown and
 * mouseup were not arriving in the pairs the page assumed.
 *
 * Rather than work out which browser drops which event, the page now compares
 * what it believes is held with what the browser says is held. Every mouse
 * event carries `buttons`, a bitmask of what is actually down at that
 * instant, and under a pointer lock move events arrive constantly while
 * somebody is aiming -- so a release that never happened is undone on the
 * next movement, and a press that was missed is made good the same way.
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

const from = app.indexOf("const deskDown = new Set();");
const until = app.indexOf("function deskCapture() {");
check(from > 0 && until > from, "the button code was found in app.js");

function harness() {
  const sent = [];
  const fns = new Function("deskSend", "deskCaptured",
    "let deskHeld = true;" + app.slice(from, until)
    + "; return { deskButton, deskButtonsCheck, deskButtonsAllUp,"
    + " down: () => [...deskDown].sort() };")(
      (list) => sent.push(...list), () => true);
  const ev = (type, button, buttons) => ({
    type, button, buttons, preventDefault() {},
  });
  return { sent, fns, ev };
}

console.log("an ordinary press and release");
let h = harness();
h.fns.deskButton(h.ev("mousedown", 0, 1));
check(JSON.stringify(h.sent) === '[{"t":"b","b":0,"d":1}]',
      `left goes down: ${JSON.stringify(h.sent)}`);
check(JSON.stringify(h.fns.down()) === "[0]", "and is remembered as held");
h.sent.length = 0;
h.fns.deskButton(h.ev("mouseup", 0, 0));
check(JSON.stringify(h.sent) === '[{"t":"b","b":0,"d":0}]',
      `and comes back up: ${JSON.stringify(h.sent)}`);
check(h.fns.down().length === 0, "and is forgotten");

console.log("\na hold stays held across the movement of aiming");
h = harness();
h.fns.deskButton(h.ev("mousedown", 0, 1));
h.sent.length = 0;
for (let i = 0; i < 20; i += 1) h.fns.deskButtonsCheck(h.ev("mousemove", -1, 1));
check(h.sent.length === 0,
      `twenty moves with the button still down send nothing: ${h.sent.length} `
      + "-- re-pressing a held button every frame would be its own bug");
check(JSON.stringify(h.fns.down()) === "[0]", "and it is still held");

console.log("\nthe reported fault: a release that did not happen");
// Safari delivering a mouseup while the button is physically still down. The
// mask on that event still says 1, so the page can tell it is not real.
h = harness();
h.fns.deskButton(h.ev("mousedown", 0, 1));
h.sent.length = 0;
h.fns.deskButton(h.ev("mouseup", 0, 1));
check(h.sent.length === 0,
      `a mouseup whose own mask says the button is still down is ignored: `
      + JSON.stringify(h.sent));
check(JSON.stringify(h.fns.down()) === "[0]", "so the button stays held");

console.log("\nand the same fault where the browser clears the mask too");
// The harder shape: the up looks entirely real, so the button is released --
// and then the very next movement shows it is still physically down.
h = harness();
h.fns.deskButton(h.ev("mousedown", 0, 1));
h.fns.deskButton(h.ev("mouseup", 0, 0));
check(h.fns.down().length === 0, "it is released, as the event asked");
h.sent.length = 0;
h.fns.deskButtonsCheck(h.ev("mousemove", -1, 1));
check(JSON.stringify(h.sent) === '[{"t":"b","b":0,"d":1}]',
      `the next movement puts it back down: ${JSON.stringify(h.sent)}`);

console.log("\na press that was missed altogether is made good");
h = harness();
h.fns.deskButtonsCheck(h.ev("mousemove", -1, 1));
check(JSON.stringify(h.sent) === '[{"t":"b","b":0,"d":1}]',
      "a move with the button down presses it");

console.log("\na release that was missed is made good too");
h = harness();
h.fns.deskButton(h.ev("mousedown", 0, 1));
h.sent.length = 0;
h.fns.deskButtonsCheck(h.ev("mousemove", -1, 0));
check(JSON.stringify(h.sent) === '[{"t":"b","b":0,"d":0}]',
      "a move with nothing down releases it -- which is what stops a button "
      + "being left held on somebody else's computer");

console.log("\nthe bits are the ones the browser actually uses");
// Left is 1, right is 2, middle is 4 -- not the order of the button numbers,
// which is exactly the sort of thing to get backwards once and never notice.
h = harness();
h.fns.deskButtonsCheck(h.ev("mousemove", -1, 2));
check(JSON.stringify(h.sent) === '[{"t":"b","b":2,"d":1}]',
      `mask 2 is the right button, not the middle: ${JSON.stringify(h.sent)}`);
h = harness();
h.fns.deskButtonsCheck(h.ev("mousemove", -1, 4));
check(JSON.stringify(h.sent) === '[{"t":"b","b":1,"d":1}]',
      `mask 4 is the middle button: ${JSON.stringify(h.sent)}`);
h = harness();
h.fns.deskButtonsCheck(h.ev("mousemove", -1, 5));
check(JSON.stringify(h.fns.down()) === "[0,1]",
      "and two at once are both held");

console.log("\nnothing is left held when the pointer goes away");
h = harness();
h.fns.deskButtonsCheck(h.ev("mousemove", -1, 5));
h.sent.length = 0;
h.fns.deskButtonsAllUp();
check(h.sent.length === 2 && h.sent.every((m) => m.d === 0),
      `both are released: ${JSON.stringify(h.sent)}`);
check(h.fns.down().length === 0, "and nothing is remembered as held");
h.sent.length = 0;
h.fns.deskButtonsAllUp();
check(h.sent.length === 0, "and doing it twice sends nothing the second time");

console.log("\na browser with no `buttons` at all still works");
h = harness();
h.fns.deskButton({ type: "mousedown", button: 0, preventDefault() {} });
check(JSON.stringify(h.sent) === '[{"t":"b","b":0,"d":1}]',
      "it falls back to the event's own button");

process.exit(fails ? 1 : 0);
