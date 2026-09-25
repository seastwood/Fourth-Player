/* Motion staying on between visits.
 *
 * It did not, and the reason was mine: iOS will only take a permission
 * request from inside a gesture, so the first version refused to ask on load
 * and waited for somebody to find the switch again. Every visit.
 *
 * Safari remembers a grant per site, and once given, requestPermission()
 * resolves "granted" straight away -- usually with no prompt and no gesture.
 * So a remembered yes is honoured by asking again and expecting a yes.
 *
 * The three answers are different and were being collapsed into one:
 *
 *   * granted -> on, silently.
 *   * a rejected call -> "not from here", not "no". The next tap anywhere is
 *     used, so nobody hunts for the switch.
 *   * "denied" -> taken at its word. Safari remembers it, so asking on every
 *     load is a prompt nobody can say yes to; the preference is forgotten
 *     instead of left looking on.
 */
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
let bad = 0;
const check = (cond, what) => {
  console.log((cond ? "  ok   " : "  FAIL ") + what);
  if (!cond) bad += 1;
};

const cut = (from, to) => src.slice(src.indexOf(from), src.indexOf(to));
const body = cut("const GYRO_KEY", "/* The sample to put in the next frame");

function page({ answer, throws, remembered = "1", needsAsking = true }) {
  const state = { listening: false, stored: remembered, said: [], gestures: [] };
  const run = new Function("state", `
    const localStorage = {
      getItem: () => state.stored,
      setItem: (_k, v) => { state.stored = v; },
    };
    const window = {
      addEventListener: (name) => state.gestures.push(name),
      removeEventListener: () => {},
    };
    const DeviceMotionEvent = {
      requestPermission: ${throws
        ? 'async () => { throw new Error("requires a user gesture"); }'
        : `async () => ${JSON.stringify(answer)}`},
    };
    ${needsAsking ? "" : "delete DeviceMotionEvent.requestPermission;"}
    const FPFrame = { motionSample: () => [0,0,0,0,0,0], GYRO_PER_DEG_SEC: 16,
                      ACCEL_PER_G: 1000, screenAngle: () => 0 };
    const el = () => null;
    const report = (t) => state.said.push(t);
    function paintGyro() {}
    ${body}
    // startGyro adds the real listener; here it only has to be observable.
    startGyro = () => { state.listening = true; };
    return { resumeGyro, on: () => gyroOn, state };
  `)(state);
  return run;
}

console.log("a remembered yes comes back with no prompt and no tap");
let p = page({ answer: "granted" });
await p.resumeGyro();
check(p.on() === true, "it is on");
check(p.state.listening === true, "and listening");
check(p.state.gestures.length === 0,
      "without waiting for a gesture: " + p.state.gestures);

console.log("\na call refused because it was not a gesture waits for one");
p = page({ throws: true });
await p.resumeGyro();
check(p.on() === false, "not on yet");
check(p.state.gestures.includes("pointerdown")
      && p.state.gestures.includes("touchend"),
      "the next tap anywhere is armed, so nobody hunts for the switch: "
      + p.state.gestures);
check(p.state.stored === "1",
      "and the preference is kept -- this was never a refusal");
check(p.state.said.some((t) => /first tap/.test(t)),
      "with a line saying so: " + JSON.stringify(p.state.said));

console.log("\na real refusal is taken at its word, once");
p = page({ answer: "denied" });
await p.resumeGyro();
check(p.on() === false, "it stays off");
check(p.state.stored === "0",
      "and is forgotten, so it does not prompt on every load for ever: "
      + p.state.stored);
check(p.state.gestures.length === 0, "no gesture is armed for it");
check(p.state.said.some((t) => /Settings/.test(t)),
      "and it says where to undo that: " + JSON.stringify(p.state.said));

console.log("\na browser with nothing to ask just starts");
p = page({ needsAsking: false });
await p.resumeGyro();
check(p.on() === true && p.state.listening === true,
      "on, with no permission dance at all");

console.log("\nand nothing happens when it was never wanted");
p = page({ answer: "granted", remembered: "0" });
await p.resumeGyro();
check(p.on() === false && p.state.listening === false,
      "a guest who never turned it on is not asked");

/* And the reason it still had to be switched on by hand.
 *
 * Wiring the control and resuming motion were one function with a single
 * early return, so the whole of it -- including the resume -- ran only on the
 * first join a page ever made. Rejoining left motion off and the switch had
 * to be found and tapped again, every time. */
console.log("\nrejoining resumes it, not just the first join");
const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const watch = app.slice(app.indexOf("function watchGyro()"),
                        app.indexOf("function watchPadKind()"));
check(/dataset\.wired\) \{[\s\S]{0,200}?resumeGyro\(\)/.test(watch),
      "an already-wired control still resumes rather than returning early");
check((watch.match(/resumeGyro\(\)/g) || []).length >= 2,
      "on both paths through it -- the first join and every one after");
check(watch.indexOf("dataset.wired = \"1\"")
      > watch.indexOf("resumeGyro()"),
      "and the early path comes first, so wiring is what happens once rather "
      + "than resuming");

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
