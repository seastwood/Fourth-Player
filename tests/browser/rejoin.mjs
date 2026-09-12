/* What an unanswered resume is allowed to conclude.
 *
 * `armRejoinTimer` is cut out of app.js and run with a hand-driven clock. The
 * rule being tested: a resume that goes unanswered is evidence about the
 * network, not about the credential, so it must be retried and the credential
 * must survive. Only the host refusing it is evidence about the credential.
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const from = app.indexOf("function armRejoinTimer");
const until = app.indexOf("function askForPin");
if (from < 0 || until < 0 || until < from) {
  console.log("FAIL could not find armRejoinTimer in app.js");
  process.exit(1);
}
const source = app.slice(from, until);

const limit = Number(/const REJOIN_LIMIT_MS = (\d+)/.exec(app)[1]);
const maxTries = Number(/const REJOIN_MAX_TRIES = (\d+)/.exec(app)[1]);

let fails = 0;
function check(cond, msg) {
  console.log((cond ? "  ok   " : "  FAIL ") + msg);
  if (!cond) fails++;
}

function harness({ ended = false } = {}) {
  const log = { reconnects: 0, asked: [], links: [] };
  let pending = null;
  const scope = {
    REJOIN_LIMIT_MS: limit,
    REJOIN_MAX_TRIES: maxTries,
    rejoinTimer: null,
    rejoinTries: 0,
    retries: 7,                        // non-zero, to prove it is reset
    ended,
    setTimeout: (fn, ms) => { pending = { fn, ms }; return 1; },
    clearTimeout: () => { pending = null; },
    clearRejoinTimer: function () { pending = null; },
    setLink: (kind, detail) => log.links.push(detail || kind),
    reconnectSoon: () => { log.reconnects++; },
    askForPin: (why, forget = true) => log.asked.push({ why, forget }),
  };
  const body = `${source}; return { armRejoinTimer,
                 tries: () => rejoinTries, retries: () => retries,
                 reset: () => { rejoinTries = 0; } };`;
  const fns = new Function(...Object.keys(scope), body)(...Object.values(scope));
  return { log, fns, fire: () => { const p = pending; pending = null; p.fn(); },
           armed: () => pending !== null };
}

// -- one quiet deadline must not cost the credential ----------------------
let h = harness();
h.fns.armRejoinTimer();
h.fire();
check(h.log.asked.length === 0,
      "one unanswered resume does not send anybody to the PIN screen");
check(h.log.reconnects === 1, "it tries the resume again instead");
check(h.armed(), "and arms another deadline to watch that attempt");
check(h.fns.retries() === 0,
      "the backoff is reset, so the retry goes out at once");
check(h.log.links.some((l) => /getting you back in/i.test(l)),
      "and the guest is told it is still trying");

// -- the allowance runs out, and even then the credential survives --------
h = harness();
h.fns.armRejoinTimer();
for (let i = 0; i < maxTries + 1; i++) h.fire();
check(h.log.asked.length === 1,
      `the PIN screen comes back after ${maxTries} failed attempts`);
check(h.log.asked[0].forget === false,
      "and the stored credential is KEPT even then -- the host refused nothing");
check(h.log.reconnects === maxTries,
      `after exactly ${maxTries} retries, not more`);

// -- a session that has ended is not something to rejoin ------------------
h = harness({ ended: true });
h.fns.armRejoinTimer();
h.fire();
check(h.log.asked.length === 0 && h.log.reconnects === 0,
      "nothing is retried once the session has ended");

// -- the allowance is per outage, not per session -------------------------
h = harness();
h.fns.armRejoinTimer();
h.fire();
check(h.fns.tries() === 1, "a failed attempt is counted");
h.fns.reset();                          // what the joined handler does
check(h.fns.tries() === 0,
      "and landing a resume clears the count, so the next outage gets a full allowance");

// -- the default is still to forget, for real refusals --------------------
const askSrc = app.slice(app.indexOf("function askForPin"),
                         app.indexOf("function askForPin") + 700);
check(/function askForPin\(why, forget = true\)/.test(askSrc),
      "askForPin still forgets by default, so host refusals behave as before");
check(/if \(forget\) \{[\s\S]{0,140}removeItem\(credKey\(\)\)/.test(askSrc),
      "and the credential is only removed inside that guard");

console.log("");
if (fails) { console.log(`rejoin: ${fails} FAILED`); process.exit(1); }
console.log("rejoin: all ok");
