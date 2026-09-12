/* The auto-seating rules, exercised without a browser.
 *
 * `seatPressedPads` and `padIsPressed` are cut out of app.js and run against
 * stubs, so the rules can be checked without Chrome and without a host. What
 * is being tested is the judgement, not the plumbing: which controllers get a
 * seat, which are left alone, and what happens after somebody removes one.
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const from = app.indexOf("function seatPressedPads");
const until = app.indexOf("function dropExtra");
if (from < 0 || until < 0 || until < from) {
  console.log("FAIL could not find seatPressedPads/padIsPressed in app.js");
  process.exit(1);
}
const source = app.slice(from, until);

let fails = 0;
function check(cond, msg) {
  console.log((cond ? "  ok   " : "  FAIL ") + msg);
  if (!cond) fails++;
}

/* One scenario: which pads exist, which are pressed, and the page's state. */
function run({ pads, padIndex = null, seated = [], declinedList = [],
               gateHidden = true, ended = false }) {
  const added = [];
  const extras = new Map(seated.map((i) => [i, {}]));
  const declined = new Set(declinedList);
  const gate = { hidden: gateHidden };
  const scope = {
    ended, gate, padIndex, extras, declined,
    addExtra: (i) => { declined.delete(i); added.push(i); extras.set(i, {}); },
  };
  const body = `${source}; return { seatPressedPads, padIsPressed };`;
  const make = new Function(...Object.keys(scope), body);
  const fns = make(...Object.values(scope));
  fns.seatPressedPads(pads);
  return { added, declined };
}

const pad = (index, pressed = [], connected = true) => ({
  index, connected,
  buttons: [0, 1, 2, 3].map((i) => ({ pressed: pressed.includes(i), value: 0 })),
});

// -- the case this was built for ------------------------------------------
check(run({ pads: [pad(0), pad(1, [0])], padIndex: 0 }).added.join() === "1",
      "a second controller with a button pressed is seated");

check(run({ pads: [pad(0), pad(1)], padIndex: 0 }).added.length === 0,
      "a second controller nobody is touching is left alone");

check(run({ pads: [pad(0), pad(1, [0]), pad(2, [3]), pad(3, [1])],
            padIndex: 0 }).added.join() === "1,2,3",
      "a third and fourth are seated the same way");

check(run({ pads: [pad(0), pad(1, [0]), pad(2), pad(3, [2])],
            padIndex: 0 }).added.join() === "1,3",
      "and the spare between them is still left alone");

// -- what must never be seated -------------------------------------------
check(run({ pads: [pad(0, [0]), pad(1, [0])], padIndex: 0 }).added.join() === "1",
      "this page's own pad is never added as an extra");

check(run({ pads: [pad(0), pad(1, [0])], padIndex: 0,
            seated: [1] }).added.length === 0,
      "a pad already seated is not seated twice");

check(run({ pads: [pad(0), pad(1, [0])], padIndex: 0,
            declinedList: [1] }).added.length === 0,
      "a pad removed by hand stays out, however often it is pressed");

check(run({ pads: [pad(0), pad(1, [0])], padIndex: 0,
            gateHidden: false }).added.length === 0,
      "nothing is seated while the join screen is still up");

check(run({ pads: [pad(0), pad(1, [0])], padIndex: 0,
            ended: true }).added.length === 0,
      "nothing is seated once the session has ended");

check(run({ pads: [pad(0), pad(1, [0], false)], padIndex: 0 }).added.length === 0,
      "a disconnected pad is not seated even if it reports a press");

check(run({ pads: [null, pad(1, [0])], padIndex: 0 }).added.join() === "1",
      "an empty slot in the browser's list is skipped safely");

// -- analogue triggers, and sticks that must not count --------------------
const trigger = { index: 1, connected: true,
                  buttons: [{ pressed: false, value: 0.9 }] };
check(run({ pads: [pad(0), trigger], padIndex: 0 }).added.join() === "1",
      "an analogue trigger that reports a value but no flag still counts");

const resting = { index: 1, connected: true,
                  buttons: [{ pressed: false, value: 0.2 }],
                  axes: [0.8, -0.7] };
check(run({ pads: [pad(0), resting], padIndex: 0 }).added.length === 0,
      "a stick resting off centre does not seat a pad on the sofa");

// An explicit add clears a previous removal.
const after = run({ pads: [pad(0), pad(1, [0])], padIndex: 0,
                    declinedList: [1] });
check(after.declined.has(1),
      "and the removal is still remembered when nothing re-adds it");

console.log("");
if (fails) { console.log(`autoseat: ${fails} FAILED`); process.exit(1); }
console.log("autoseat: all ok");
