/* Correcting a controller that is not this page's own.
 *
 * "Fix my buttons" has always written its result under the pad's name, and
 * ExtraPlayer.send has always ignored it: the second and third seats built
 * their frames from the raw pad. So the seats hardest to reach from the sofa
 * were the only ones whose buttons could not be corrected, and a guest whose
 * second controller reported its buttons in a strange order had no way to say
 * so.
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const from = app.indexOf("function mapForPad");
const until = app.indexOf("function loadPadMap");
if (from < 0 || until < 0) {
  console.log("FAIL could not find mapForPad/correctedPad in app.js");
  process.exit(1);
}
const source = app.slice(from, until);

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

const STANDARD_KEYS = Array.from({ length: 17 }, (_, i) => "b" + i);

function build(store) {
  const localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
  };
  const body = source + "; return { correctedPad, mapForPad, sticksSwappedFor };";
  return new Function("localStorage", "STANDARD_KEYS", body)(
    localStorage, STANDARD_KEYS);
}

const pad = (pressedIndex) => ({
  index: 2, connected: true, id: "8BitDo Micro gamepad",
  buttons: Array.from({ length: 17 }, (_, i) => ({
    pressed: i === pressedIndex, value: i === pressedIndex ? 1 : 0 })),
  axes: [0.1, 0.2, 0.3, 0.4, 0, 0],
});

// -- no corrections stored: the pad passes through untouched ---------------
let fns = build({});
check(fns.correctedPad(pad(0), "8BitDo Micro gamepad").buttons[0].pressed,
      "with nothing stored the pad is passed straight through");

// -- a map for THAT pad's name is applied -----------------------------------
// Button 0 should read from the pad's button 3.
const map = Array.from({ length: 17 }, (_, i) => (i === 0 ? 3 : null));
fns = build({ "fp-padmap:8BitDo Micro gamepad": JSON.stringify(map) });
let out = fns.correctedPad(pad(3), "8BitDo Micro gamepad");
check(out.buttons[0].pressed,
      "a map stored for that controller's name is applied to it");
check(!out.buttons[3].pressed,
      "and the button it was taken from is not left pressed as well");

// -- a map belonging to a DIFFERENT controller is not borrowed -------------
fns = build({ "fp-padmap:Some Other Pad": JSON.stringify(map) });
out = fns.correctedPad(pad(3), "8BitDo Micro gamepad");
check(!out.buttons[0].pressed,
      "a map belonging to another controller is not applied to this one");
check(out.buttons[3].pressed,
      "which leaves this one reporting what it actually reported");

// -- sticks swap, per controller ------------------------------------------
fns = build({ "fp-sticks:8BitDo Micro gamepad": "1" });
out = fns.correctedPad(pad(0), "8BitDo Micro gamepad");
check(out.axes[0] === 0.3 && out.axes[1] === 0.4
      && out.axes[2] === 0.1 && out.axes[3] === 0.2,
      "swapped sticks are swapped for that controller");
check(out.axes[4] === 0 && out.axes[5] === 0,
      "and the triggers are left where they are");

fns = build({ "fp-sticks:Some Other Pad": "1" });
out = fns.correctedPad(pad(0), "8BitDo Micro gamepad");
check(out.axes[0] === 0.1 && out.axes[2] === 0.3,
      "another controller's stick swap is not borrowed either");

// -- it must not invent a pad ---------------------------------------------
check(fns.correctedPad(null, "whatever") === null,
      "no pad in, no pad out");

// -- and the seat must actually use it ------------------------------------
check(/correctedPad\(raw, this\.name\)/.test(app),
      "ExtraPlayer.send corrects its own pad by its own name");
check(/let fixingIndex = null/.test(app) && /function fixPad/.test(app),
      "and the panel can be pointed at a controller that is not this page's");

console.log("");
if (fails) { console.log(`padfix: ${fails} FAILED`); process.exit(1); }
console.log("padfix: all ok");
