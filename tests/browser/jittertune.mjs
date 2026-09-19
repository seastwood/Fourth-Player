/* The browser's own buffer, sized by the link rather than by a setting.

   jitter_ms is a number somebody sets once, and a link is not one thing all
   evening. Set for the bad case it costs delay all night; set for the good
   one it stutters the moment anything goes wrong.

   This is moonlight-web's JitterController in shape, and the asymmetry is the
   whole of it: up fast, down slowly. A freeze has already been seen by
   somebody; coming down in a hurry means going back up again, and a buffer
   that oscillates is worse than one that is merely too big. */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
let bad = 0;
const check = (cond, what) => {
  console.log((cond ? "  ok   " : "  FAIL ") + what);
  if (!cond) bad += 1;
};

console.log("it exists and only for the browser's own drawing");
check(app.includes("function tuneTheBuffer"), "there is something that tunes it");
check(app.includes("if (painter || !picture || !before) return;"),
      "and it stands aside when this page is drawing, since that has its own "
      + "queue and two of them is twice the delay for one link's jitter");

/* Run the control law. */
const body = app.slice(app.indexOf("const JITTER = {"),
                       app.indexOf("function noteFreezes"));
const said = [];
const held = [];
const run = new Function("said", "held", `
  let painter = null;
  let streamNow = { jitter_ms: 60 };
  const report = (t) => said.push(t);
  const holdVideoBack = (ms) => held.push(ms);
  ${body}
  return {
    tune: (a, b, p) => tuneTheBuffer(a, b, p),
    target: () => jitterTarget,
    limits: JITTER,
  };
`)(said, held);

const stat = (n) => ({ freezeCount: n.froze || 0, packetsLost: n.lost || 0,
                       packetsReceived: n.got || 0, jitter: (n.jitter || 0) / 1000 });

console.log("\na quiet link comes down, slowly");
// Primed by a jittery spell first, because a clean link now starts at
// nothing: the target used to be seeded from the host's guess whether or not
// anything had gone wrong, which charged every guest twenty-odd milliseconds
// of delay for a link that was behaving. There has to be something to come
// down from before "comes down" can be tested.
let before = stat({ got: 0 });
for (let i = 1; i <= 6; i += 1) {
  const now = stat({ got: i * 100, jitter: 30 });
  run.tune(now, before, { currentRoundTripTime: 0.05 });
  before = now;
}
const raised = run.target();
check(raised > 60, `a jittery spell raises it: ${raised}ms`);
for (let i = 7; i <= 60; i += 1) {
  const now = stat({ got: i * 100, jitter: 2 });
  run.tune(now, before, { currentRoundTripTime: 0.01 });
  before = now;
}
check(run.target() < raised, `it came down from ${raised}: ${run.target()}ms`);
check(run.target() >= run.limits.FLOOR_MS,
      `and stopped at the floor: ${run.target()}ms`);
// The target moving and the target being pushed are two different things: a
// deadband wider than a decay step meant each window worked out a figure ten
// milliseconds lower, found the change too small to push, and returned
// without remembering it -- so a link that had once been jittery stayed at
// its worst figure for the rest of the session.
check(run.limits.DEADBAND_MS > run.limits.DOWN_MS,
      "the deadband is wider than a decay step, which is only safe because "
      + "the two are kept apart");
const downs = held.length;
check(downs > 0 && downs < 60,
      `in steps rather than every window: ${downs} changes in 60`);

console.log("\na freeze puts it up at once");
held.length = 0;
const was = run.target();
let now = stat({ got: 4100, froze: 1, jitter: 2 });
run.tune(now, before, { currentRoundTripTime: 0.01 });
check(run.target() > was, `up from ${was} to ${run.target()}ms on one freeze`);
check(held.length === 1, "and applied straight away");

console.log("\nloss raises it too, and the round trip sets the floor for it");
before = stat({ got: 4100 });
now = stat({ got: 4200, lost: 20, jitter: 2 });
const beforeLoss = run.target();
run.tune(now, before, { currentRoundTripTime: 0.2 });
check(run.target() > beforeLoss,
      `up from ${beforeLoss} to ${run.target()}ms on 17% loss`);

console.log("\nand it never runs away");
before = now;
for (let i = 0; i < 100; i += 1) {
  const step = stat({ got: 4300 + i * 100, froze: i + 2, lost: i * 10, jitter: 50 });
  run.tune(step, before, { currentRoundTripTime: 0.5 });
  before = step;
}
check(run.target() <= run.limits.CEILING_MS,
      `a link falling apart still stops at the ceiling: ${run.target()}ms`);

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
