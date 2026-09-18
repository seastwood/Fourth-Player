/* The decode-to-canvas path, run rather than read.

   This exists because of one line in the log:

     drawing here: 85 fed to the decoder, 84 came out, 0 painted, 0 refused

   Eighty-four frames came out of the decoder and none reached the canvas. The
   cause was a counter whose declaration had been removed while the line that
   incremented it stayed -- and reading an undeclared name throws, inside a
   decoder output callback, where WebCodecs swallows it. So every frame
   decoded, none was ever drawn, and nothing anywhere said why.

   Reading the file would not have caught that. Running it does. */
import { readFileSync } from "node:fs";

let bad = 0;
const check = (cond, what) => {
  console.log((cond ? "  ok   " : "  FAIL ") + what);
  if (!cond) bad += 1;
};

/* The smallest browser that can run paint.js. */
let clock = 1000;
const said = [];
let output = null, onError = null;
let painted = 0, closed = 0;

class FakeDecoder {
  constructor(o) { output = o.output; onError = o.error; this.state = "unconfigured"; }
  configure() { this.state = "configured"; }
  decode() {}
  close() { this.state = "closed"; }
}
FakeDecoder.isConfigSupported = async () => ({ supported: true });

class FakeFrame {
  constructor(timestamp) {
    this.timestamp = timestamp;
    this.displayWidth = 1280;
    this.displayHeight = 720;
  }
  close() { closed += 1; }
}

const world = {
  performance: { now: () => clock },
  VideoDecoder: FakeDecoder,
  VideoFrame: FakeFrame,
  EncodedVideoChunk: class { constructor(o) { Object.assign(this, o); } },
  RTCRtpScriptTransform: class { constructor() {} },
  Worker: class { constructor() { this.onmessage = null; } terminate() {} },
  setTimeout: (fn, ms) => globalThis.setTimeout(fn, 0),
  clearTimeout: (id) => globalThis.clearTimeout(id),
};

const src = readFileSync(new URL("../../web/paint.js", import.meta.url), "utf8");
const names = Object.keys(world);
const mod = { exports: {} };
new Function("module", "exports", ...names, src)(
  mod, mod.exports, ...names.map((n) => world[n]));
const paint = mod.exports;

const canvas = {
  width: 300, height: 150,
  getContext: () => ({ drawImage: () => { painted += 1; } }),
};

const painter = paint.makePainter(canvas, (t) => said.push(t));

console.log("a frame that comes out of the decoder reaches the canvas");
painter.start({ transform: null }, "avc1.42E01F");
check(output !== null, "the decoder was built and its output taken");
output(new FakeFrame(0));
check(painted === 1, `one frame out, ${painted} painted`);
check(painter.painted() === true, "and the painter knows it has painted");
check(closed === 1, "the frame was closed, so the decoder gets its buffer back");

console.log("and a run of them, with the canvas resized to the picture");
for (let i = 1; i <= 30; i += 1) {
  clock += 16.7;
  output(new FakeFrame(i * 16700));
}
check(painted === 31, `31 frames in, ${painted} painted`);
check(canvas.width === 1280 && canvas.height === 720,
      `the canvas took the picture's size: ${canvas.width}x${canvas.height}`);

console.log("the report names every stage");
const line = painter.report();
check(/painted/.test(line) && /came out/.test(line),
      "it says what came out and what was painted: " + line);

console.log("stopping leaves nothing behind");
painter.stop();
check(painter.painted() === false, "and forgets that it ever painted");
check(painter.running() === false, "and is not running");

console.log("a decoder that fails says so through the painter, not into the void");
said.length = 0;
const second = paint.makePainter(canvas, (t) => said.push(t));
second.start({ transform: null }, "avc1.42E01F");
onError(new Error("Decoder failure"));
check(said.some((t) => /decoder/i.test(t)),
      "the failure is reported: " + (said[0] || "nothing"));

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
