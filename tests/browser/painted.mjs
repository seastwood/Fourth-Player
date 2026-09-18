/* The decode-to-canvas path, run rather than read.

   It lives in a worker now (see web/frames.js for why: about twenty frames a
   second reached the canvas out of sixty while the decoder and the painting
   both reported themselves healthy, because their work was queued behind
   everything else the page does). So this drives the worker, with a fake
   decoder and a fake canvas, through the real code.

   This test exists because of one line in the log -- "84 came out, 0
   painted" -- caused by a counter whose declaration had been removed while
   the line incrementing it stayed. Reading the file would not have caught
   that. Running it does. */
import { readFileSync } from "node:fs";

let bad = 0;
const check = (cond, what) => {
  console.log((cond ? "  ok   " : "  FAIL ") + what);
  if (!cond) bad += 1;
};

const paintSrc = readFileSync(new URL("../../web/paint.js", import.meta.url), "utf8");
const workerSrc = readFileSync(new URL("../../web/frames.js", import.meta.url), "utf8");

/* The smallest worker that can run frames.js. */
let clock = 1000;
const sent = [];              // what the worker posted to the page
let output = null, onError = null;
let painted = 0, closed = 0;
const built = [];

class FakeDecoder {
  constructor(o) {
    output = o.output; onError = o.error;
    this.state = "unconfigured"; this.config = null; this.chunks = [];
    this.decodeQueueSize = 0;
    built.push(this);
  }
  configure(c) { this.state = "configured"; this.config = c; }
  decode(chunk) { this.chunks.push(chunk); }
  close() { this.state = "closed"; }
}

class FakeFrame {
  constructor(timestamp) {
    this.timestamp = timestamp;
    this.displayWidth = 1280;
    this.displayHeight = 720;
  }
  close() { closed += 1; }
}

const canvas = {
  width: 300, height: 150,
  getContext: () => ({ drawImage: () => { painted += 1; } }),
};

const world = {
  performance: { now: () => clock },
  VideoDecoder: FakeDecoder,
  VideoFrame: FakeFrame,
  EncodedVideoChunk: class { constructor(o) { Object.assign(this, o); } },
  setTimeout: (fn, ms) => globalThis.setTimeout(() => {
    clock += Math.max(0, ms || 0);
    fn();
  }, 0),
  clearTimeout: (id) => globalThis.clearTimeout(id),
};

const self_ = {
  postMessage: (m) => sent.push(m),
  importScripts: () => {},
  onmessage: null,
  onrtctransform: null,
};
// paint.js first, as importScripts does in the worker, so makePacer and the
// bitstream helpers are in scope exactly as they are there.
const names = Object.keys(world);
const boot = new Function("self", ...names, `
  ${paintSrc}
  self.importScripts = () => {};
  const PACE_ = PACE;
  ${workerSrc.replace('importScripts("/static/paint.js");', "")}
  return { PACE: PACE_ };
`);
boot(self_, ...names.map((n) => world[n]));

console.log("the worker says it is ready before anything else");
check(sent.length === 1 && sent[0].ready === true,
      "one ready message and nothing before it");

console.log("and builds a decoder when the page hands over a canvas");
sent.length = 0;
self_.onmessage({ data: { start: { canvas, codec: "avc1.42E01F" } } });
check(built.length === 1, "a decoder was built");
check(built[0].config && built[0].config.description === undefined,
      "with no description, which means start codes");
check(sent.some((m) => m.started), "and it says it started");

console.log("a frame fed in reaches the canvas");
const sps = [0x67, 0x42, 0xe0, 0x28, 0xaa, 0xbb];
const pps = [0x68, 0xce, 0x3c, 0x80];
const idr = [0x65, 0x88, 0x84, 0x21];
const keyframe = new Uint8Array([0, 0, 0, 1, ...sps, 0, 0, 0, 1, ...pps,
                                 0, 0, 1, ...idr]).buffer;
const framed = (bytes, key, stamp, first, last) => {
  const out = new Uint8Array(9 + bytes.byteLength);
  const view = new DataView(out.buffer);
  view.setUint8(0, (key ? 1 : 0) | (first ? 2 : 0) | (last ? 4 : 0));
  view.setBigUint64(1, BigInt(stamp), true);
  out.set(new Uint8Array(bytes), 9);
  return out.buffer;
};
self_.onmessage({ data: { chunk: framed(keyframe, true, 0, true, true) } });
check(built[0].chunks.length === 1, "the keyframe was fed to the decoder");
output(new FakeFrame(0));
check(painted === 1, `one frame out, ${painted} painted`);
check(closed === 1, "and the frame was closed, so its buffer goes back");

console.log("a run of them, one paint per turn of the event loop");
// A transferred OffscreenCanvas shows what was drawn when the task that drew
// it ends. Draining a queue in one go therefore shows the last frame of the
// batch and loses the rest invisibly -- 361 painted in the counters and one
// frozen picture on the screen.
for (let i = 1; i <= 30; i += 1) {
  clock += 16.7;
  output(new FakeFrame(i * 16700));
  // Let the worker's own timers run between frames, as they would.
  await new Promise((go) => globalThis.setTimeout(go, 0));
}
await new Promise((go) => globalThis.setTimeout(go, 60));
check(painted === 31, `31 out, ${painted} painted`);
check(workerSrc.includes("setTimeout(pump, 0)"),
      "and the queue yields between paints rather than draining in one turn");
check(canvas.width === 1280 && canvas.height === 720,
      `the canvas took the picture's size: ${canvas.width}x${canvas.height}`);

console.log("frames that arrive in a burst are still painted evenly");
// The fault this replaced: an absolute schedule built by mapping the capture
// clock onto this one drifts until every frame is late on arrival, and a
// queue of late frames is drained as fast as the event loop allows. Measured
// on the real thing: "painted every 10ms typical, worst 176ms, 224 of 475
// off the beat", on a stream sending an even sixty a second.
check(workerSrc.includes("function schedule(captureMs)"),
      "the schedule is built by a named thing that can be read");
check(workerSrc.includes("captureMs - state.lastPts"),
      "from the differences between capture times, which need no agreement "
      + "about where zero is or how fast a second passes");
check(workerSrc.includes("if (state.nextAt < now) state.nextAt = now;"),
      "and never into the past, which is what let a queue drain in one turn");
// Painting the first frame the instant it arrives leaves nothing in hand, so
// the next frame to be late is a gap on the screen. Measured with no slack:
// 16ms typical and a worst of 55, eight paints in a hundred off the beat.
check(workerSrc.includes("state.nextAt = now + gap * START_DEPTH;"),
      "the first frame waits, and the rest inherit that slack");
check(/START_DEPTH = (\d+)/.test(workerSrc), "by a named number of frames");
const slack = Number(workerSrc.match(/START_DEPTH = (\d+)/)[1]);
check(slack >= 2 && slack <= 5,
      `${slack} frames: enough to absorb a hiccup, few enough to stay a game`);
check(workerSrc.includes("state.waiting.length > DEPTH_WANT * 3"),
      "frames are dropped only when genuinely behind, not whenever the next "
      + "one happens to be due");
check(!workerSrc.includes("state.waiting[1].due <= performance.now()"),
      "which threw away one of every pair on a link that delivers in pairs");

console.log("the counters name every stage, including the one before us");
sent.length = 0;
self_.onmessage({ data: { report: true } });
const stats = (sent.find((m) => m.stats) || {}).stats;
check(stats, "a report came back");
check(stats.handed === 1, `what the transform handed over: ${stats.handed}`);
check(stats.drawn === 31, `and what was painted: ${stats.drawn}`);
check(stats.ever === true, "and that something has been painted at all");
// The only number in the whole report that describes what an eye can see.
// Everything else -- handed over, fed, decoded, painted -- counts things
// upstream of the picture, and a stream can be perfect at all of them and
// still be painted unevenly. "It feels like it is skipping" is a statement
// about this and nothing else.
check(stats.shown && typeof stats.shown.typical === "number",
      "and how evenly it was painted: " + JSON.stringify(stats.shown));
check(stats.shown.of >= 10, "over enough paints to mean something");

console.log("a saturated decoder is not given more, but keyframes still go in");
built[0].decodeQueueSize = 99;
const before = built[0].chunks.length;
self_.onmessage({ data: { report: true } });        // clear the counters
// A delta is refused...
const deltaFrame = new Uint8Array([0, 0, 0, 1, 0x41, 1, 2, 3]).buffer;
self_.onmessage({ data: { chunk: framed(deltaFrame, false, 1, true, true) } });
check(built[0].chunks.length === before,
      "a delta is dropped while the decoder is behind");

console.log("a frame in pieces is put back together before it is decoded");
// SCTP will not carry an arbitrarily large message and a keyframe is easily
// larger than a browser's limit, so the host sends each frame in pieces.
const half = new Uint8Array(keyframe);
const head = half.slice(0, 8).buffer, tail = half.slice(8).buffer;
const fedBefore = built[0].chunks.length;
self_.onmessage({ data: { chunk: framed(head, true, 5, true, false) } });
check(built[0].chunks.length === fedBefore,
      "a first piece on its own decodes nothing");
self_.onmessage({ data: { chunk: framed(tail, true, 5, false, true) } });
check(built[0].chunks.length === fedBefore + 1,
      "and the last piece completes it");
const rebuilt = new Uint8Array(built[0].chunks[fedBefore].data);
check(rebuilt.length === half.length,
      `put back to its full length: ${rebuilt.length} of ${half.length}`);

console.log("every combination worth asking is asked, one at a time");
// iOS Safari refused both start codes and the parameter sets with nothing
// but "Decoder failure" either time. A decoder that will not say which part
// of a config it dislikes leaves one honest method: offer the combinations
// and watch. The latency hint is one of them -- worth asking for, not worth
// failing over.
check(workerSrc.includes("const RUNGS = ["), "the combinations are a list");
const rungs = workerSrc.slice(workerSrc.indexOf("const RUNGS = ["),
                              workerSrc.indexOf("];", workerSrc.indexOf("const RUNGS = [")));
check((rungs.match(/avcc:/g) || []).length === 4, "four of them");
check(rungs.includes("latency: false"),
      "including the same shapes without the latency hint");
check(workerSrc.includes("if (!isAvc) continue"),
      "and an hvcC is never guessed at, because it is a different box");

console.log("a decoder that fails is given the parameter sets instead");
sent.length = 0;
onError(new Error("Decoder failure"));
check(built.length === 2, "a second decoder was built rather than giving up");
const now = built[1].config;
check(now && now.description, "with a description this time");
const desc = Array.from(new Uint8Array(now.description));
check(desc[0] === 1 && desc[4] === 0xff && desc[5] === 0xe1,
      "a well-formed avcC: " + desc.slice(0, 6).map((b) => b.toString(16)).join(" "));
check(now.codec === "avc1.42E028",
      "and a codec string read out of the SPS, not the SDP: " + now.codec);
check(built[1].chunks.length === 1,
      "the keyframe in hand is replayed, so it need not wait for the next");
const replayed = new Uint8Array(built[1].chunks[0].data);
check(replayed[3] === 6 && replayed[0] === 0,
      "length-prefixed now, not start-coded: "
      + Array.from(replayed.slice(0, 5)).join(","));
check(sent.some((m) => m.note && /parameter sets up front/.test(m.note)),
      "and it says which rung it is on: "
      + (sent.filter((m) => m.note).map((m) => m.note).join(" | ") || "nothing"));

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
