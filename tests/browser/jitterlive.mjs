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
check(paint.includes("holdBackAsNeeded"),
      "paintStream applies it, which is where a changed setting arrives");

console.log("and it is still applied when a connection is built");
// Through holdBackAsNeeded now, not holdVideoBack directly: what to apply
// depends on whether this page is taking the frames off the receiver itself,
// and that decision belongs in one place rather than at four call sites.
check(app.split("holdBackAsNeeded()").length - 1 >= 6,
      "every path that learns the number, and every change of mode, applies it");
check(app.split("holdVideoBack(").length - 1 === 2,
      "and only holdBackAsNeeded calls it, so no path can skip that decision");

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
let painter = null;
let paintMethod = "browser";
const report = (t) => said.push(t);
${body}
`;
const run = new Function("said", harness + `
  return {
    set: (receivers) => { pc = { getReceivers: () => receivers }; },
    hold: (ms) => holdVideoBack(ms),
    mode: (how, drawing) => { paintMethod = how; painter = drawing ? {} : null; },
    fromHost: (ms) => { jitterFromHost = ms; },
    asNeeded: () => holdBackAsNeeded(),
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

/* The reason any of this was touched.
 *
 * Reported from the sofa: joining with no sound gave a working picture, and a
 * refresh that brought the sound back brought the blacking out back with it.
 * Chrome aligns a video stream's playout to the audio it is played with, and
 * does it by delaying video -- so a page drawing from the media track was
 * handed its frames in bursts, hundreds of milliseconds apart, and its own
 * pacer discarded them as too late. Two buffers in series, the second throwing
 * away what the first made late. */
console.log("\na page drawing from the media track wants no hold at all");
said.length = 0;
const track = [{ track: { kind: "video" }, jitterBufferTarget: 999 }];
run.set(track);
run.fromHost(60);
run.mode("rtp", true);
run.asNeeded();
check(track[0].jitterBufferTarget === 0,
      "zero is applied, not the host's 60ms, got " + track[0].jitterBufferTarget);
check(said.some((t) => t.includes("0ms via")),
      "and said, so a browser that ignores it can be told apart from one that "
      + "took it: " + said[said.length - 1]);

console.log("\nbut only while it is really reading the receiver");
const chosen = [{ track: { kind: "video" }, jitterBufferTarget: 0 }];
run.set(chosen);
run.fromHost(60);
// The mode is chosen and nothing is painting yet -- between the track arriving
// and the decoder being built, and for ever if it never is.
run.mode("rtp", false);
run.asNeeded();
check(chosen[0].jitterBufferTarget === 60,
      "the host's hold stands until something is actually taking the frames, "
      + "got " + chosen[0].jitterBufferTarget);

console.log("\nand the hold comes back when the browser draws again");
const back = [{ track: { kind: "video" }, jitterBufferTarget: 0 }];
run.set(back);
run.fromHost(60);
run.mode("rtp", true);
run.asNeeded();
run.mode("browser", false);
run.asNeeded();
check(back[0].jitterBufferTarget === 60,
      "giving up on this mode must not leave the browser drawing with no "
      + "buffer at all, got " + back[0].jitterBufferTarget);

console.log("\nthe data-channel modes are not affected");
// They take frames off a data channel, so the media line's playout is the
// browser's business exactly as it always was.
for (const how of ["here", "flat"]) {
  const dc = [{ track: { kind: "video" }, jitterBufferTarget: 0 }];
  run.set(dc);
  run.fromHost(60);
  run.mode(how, true);
  run.asNeeded();
  check(dc[0].jitterBufferTarget === 60,
        how + " keeps the host's hold, got " + dc[0].jitterBufferTarget);
}

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
