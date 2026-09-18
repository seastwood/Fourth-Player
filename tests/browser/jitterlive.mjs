/* Raising the buffer has to reach the receiver that is already playing.

   jitterBufferTarget is a live property of an RTCRtpReceiver. The page set it
   in two places, both of which run while a media connection is being built,
   and nowhere that runs when somebody changes the setting. So raising it
   mid-session did nothing at all until the next renegotiation -- and from the
   outside that is indistinguishable from a buffer that does not help, which
   is the opposite diagnosis and the opposite next move. */
import { readFileSync } from "node:fs";
import { strict as assert } from "node:assert";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
let bad = 0;
const check = (cond, what) => {
  console.log((cond ? "  ok   " : "  FAIL ") + what);
  if (!cond) bad += 1;
};

console.log("the setting reaches a connection that is already up");
const paint = app.slice(app.indexOf("function paintStream(state)"),
                        app.indexOf("function paintStream(state)") + 900);
check(paint.includes("holdVideoBack"),
      "paintStream applies it, which is where a changed setting arrives");

console.log("and it is still applied when a connection is built");
check(app.split("holdVideoBack(").length - 1 >= 4,
      "every path that learns the number applies it");

console.log("the page says whether the browser took it");
const hold = app.slice(app.indexOf("function holdVideoBack"),
                       app.indexOf("function holdVideoBack") + 2000);
check(hold.includes("report("), "it reports what happened");
check(hold.includes("jitterBufferTarget") && hold.includes("playoutDelayHint"),
      "naming both mechanisms, since which one exists decides what is possible");
check(hold.includes("no control over it"),
      "and says so plainly when the browser offers neither");

console.log("a hint that was clamped is not reported as applied");
check(hold.includes("clamped"),
      "the value is read back rather than assumed");

console.log("it does not shout the same line every frame");
check(hold.includes("jitterSaid"),
      "the same note is said once, not on every application");

/* And the behaviour itself, run rather than read. */
console.log("\nrunning holdVideoBack against fake receivers");
const body = app.slice(app.indexOf("let jitterWanted = 0;"),
                       app.indexOf("/* ---- the pad ---- */"));
const said = [];
const harness = `
let pc = null;
const report = (t) => said.push(t);
${body}
`;
const run = new Function("said", harness + `
  return {
    set: (receivers) => { pc = { getReceivers: () => receivers }; },
    hold: (ms) => holdVideoBack(ms),
  };
`)(said);

// A browser with the modern property, honouring it.
const modern = [{ track: { kind: "video" }, jitterBufferTarget: 0 }];
run.set(modern);
run.hold(60);
check(modern[0].jitterBufferTarget === 60, "the modern property is set");
check(said.some((t) => t.includes("60ms via jitterBufferTarget")),
      "and reported: " + said[said.length - 1]);

// One that clamps it, which must not be reported as a clean success.
said.length = 0;
const clamping = [{
  track: { kind: "video" },
  _v: 0,
  get jitterBufferTarget() { return this._v; },
  set jitterBufferTarget(v) { this._v = Math.min(v, 40); },
}];
run.set(clamping);
run.hold(80);
check(said.some((t) => t.includes("clamped to 40ms")),
      "a clamp is named: " + said[said.length - 1]);

// And one with neither, which is the case worth knowing about.
said.length = 0;
const bare = [{ track: { kind: "video" } }];
run.set(bare);
run.hold(60);
check(said.some((t) => t.includes("no control over it")),
      "a browser with neither says so: " + said[said.length - 1]);

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
