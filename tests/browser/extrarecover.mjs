/* A dropped extra seat letting itself back in.
 *
 * `retrySoon` is cut out of app.js and driven with a hand-held clock. The rule:
 * a seat whose socket or pad channel dropped comes back on its own, because
 * the person holding that controller should not have to operate a menu with
 * it to make it work again. Reported as "it says disconnected and I have to
 * manually remove it then reconnect it".
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const from = app.indexOf("  retrySoon() {");
const until = app.indexOf("  seatName() {");
if (from < 0 || until < 0 || until < from) {
  console.log("FAIL could not find retrySoon in app.js");
  process.exit(1);
}
const maxTries = Number(/const EXTRA_MAX_TRIES = (\d+)/.exec(app)[1]);
const body = app.slice(from, until).replace(/^\s*retrySoon\(\)\s*\{/, "").trim();
const source = body.replace(/\}\s*$/, "");

let fails = 0;
function check(cond, msg) {
  console.log((cond ? "  ok   " : "  FAIL ") + msg);
  if (!cond) fails++;
}

function make({ closed = false, refused = false, tries = 0 } = {}) {
  const log = { opens: 0, paints: 0, closes: 0, deleted: [] };
  let pending = null;
  const self = {
    index: 3, closed, refused, tries, retryTimer: null,
    state: "failed", error: "disconnected", seq: 41,
    pc: null, socket: null, input: null,
    open() { log.opens++; },
    close() { log.closes++; },
  };
  const scope = {
    EXTRA_MAX_TRIES: maxTries,
    extras: { delete: (i) => log.deleted.push(i) },
    paintControllers: () => { log.paints++; },
    setTimeout: (fn, ms) => { pending = { fn, ms }; return 9; },
    clearTimeout: () => { pending = null; },
    Math,
  };
  const fn = new Function(...Object.keys(scope),
                          `return function () { ${source} };`)(
                          ...Object.values(scope));
  return { self, log, call: () => fn.call(self),
           fire: () => { const p = pending; pending = null; p.fn.call(self); },
           waited: () => (pending ? pending.ms : null) };
}

// -- it comes back by itself ----------------------------------------------
let h = make();
h.call();
check(h.waited() !== null, "a dropped seat schedules its own return");
check(h.self.tries === 1, "and counts the attempt");
h.fire();
check(h.log.opens === 1, "the attempt reopens the connection");
check(h.self.seq === 0, "with its sequence numbers restarted, so frames are not stale");

// -- backoff, and it does not pile up -------------------------------------
h = make({ tries: 0 });
h.call();
const first = h.waited();
h.call();
check(h.waited() === first, "a second call while one is pending changes nothing");
h = make({ tries: 3 });
h.call();
check(h.waited() > first, "later attempts wait longer than the first");
check(h.waited() <= 8000, "and the wait is capped");

// -- it gives up cleanly, leaving the pad free to be pressed again --------
h = make({ tries: maxTries });
h.call();
check(h.log.deleted.length === 1 && h.log.deleted[0] === 3,
      `after ${maxTries} tries the seat leaves the list instead of sitting there dead`);
check(h.waited() === null, "and schedules nothing further");

// -- what must not retry ---------------------------------------------------
h = make({ closed: true });
h.call();
check(h.waited() === null && h.log.opens === 0,
      "a seat this page closed on purpose stays closed");

h = make({ refused: true });
h.call();
check(h.waited() === null && h.log.opens === 0,
      "a seat the host refused for a real reason is not retried into the ground");

// -- the source rules the page relies on ----------------------------------
check(/if \(message\.reason === "full"\) this\.retrySoon\(\)/.test(app),
      "a full session is waited out, since somebody leaving makes room");
check(/this\.tries = 0;/.test(app.slice(app.indexOf("this.state = \"playing\""))),
      "and landing resets the allowance, so it is spent per outage");
check(/declining is what a person does/.test(app),
      "giving up does not decline the pad -- only a person pressing Remove does");

console.log("");
if (fails) { console.log(`extrarecover: ${fails} FAILED`); process.exit(1); }
console.log("extrarecover: all ok");
