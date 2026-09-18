/* What the browser draws, per second, against what the host says it sent.

   The host has measured its own end exhaustively. The other end was only ever
   reported as totals -- "620 frames decoded" -- which is a number with no
   clock beside it and cannot answer whether a stream sent at 60 a second is
   being drawn at 60 a second or at 45. */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
let bad = 0;
const check = (cond, what) => {
  console.log((cond ? "  ok   " : "  FAIL ") + what);
  if (!cond) bad += 1;
};

console.log("the page reports a rate, not a total");
check(app.includes("function tellAboutTheRate"), "there is such a report");
check(app.includes("frames a second"), "and it is said per second");
check(/RATE_EVERY_MS\s*=\s*(\d+)/.test(app), "over a named window");
const window = Number(app.match(/RATE_EVERY_MS\s*=\s*(\d+)/)[1]);
check(window >= 5000, `${window}ms is long enough that one hiccup does not decide it`);

console.log("all three counters, because the gaps between them differ");
for (const one of ["framesReceived", "framesDecoded", "framesDropped"]) {
  check(app.includes(one), `${one} is read`);
}

console.log("and it runs on the watchdog that already has the stats");
const watchFrom = app.indexOf("async function watchMedia()");
const watch = app.slice(watchFrom,
                        app.indexOf("\nfunction ", watchFrom));
check(watch.includes("tellAboutTheRate(picture)"),
      "called with the stats already fetched, not fetching its own");

/* Run it. */
console.log("\nrunning it against a stream drawn at 45 of a sent 60");
const body = app.slice(app.indexOf("const RATE_EVERY_MS"),
                       app.indexOf("async function watchMedia()"));
const said = [];
const make = new Function("said", `
  let Date_now = 0;
  const report = (t) => said.push(t);
  const Date = { now: () => Date_now };
  ${body}
  return {
    tick: (ms) => { Date_now += ms; },
    feed: (p) => tellAboutTheRate(p),
  };
`);
const run = make(said);

// 12 seconds of a 60fps stream arriving whole but drawn at 45.
run.feed({ framesReceived: 0, framesDecoded: 0, framesDropped: 0 });
run.tick(12000);
run.feed({ framesReceived: 720, framesDecoded: 540, framesDropped: 180 });
check(said.length === 1, "one line per window");
check(/drawing 45\.0 frames a second/.test(said[0]),
      "the drawn rate is what it says: " + said[0]);
check(/60\.0 arrived/.test(said[0]), "and the arrived rate beside it");
check(/15\.0 thrown away/.test(said[0]), "and what was discarded");

console.log("\nand it says nothing before it has a window to speak about");
said.length = 0;
run.tick(1000);
run.feed({ framesReceived: 780, framesDecoded: 585, framesDropped: 195 });
check(said.length === 0, "a second is not a sample");

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
