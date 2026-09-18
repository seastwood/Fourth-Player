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
  // No webgl2 here, so the worker falls back to the flat painter -- which is
  // itself worth checking: a browser without WebGL still gets a picture.
  getContext: (kind) => (kind === "2d"
    ? { drawImage: () => { painted += 1; } }
    : null),
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
let framedSeq = 0;
const framed = (bytes, key, stamp, first, last, index = 0, pieces = 1) => {
  const out = new Uint8Array(17 + bytes.byteLength);
  const view = new DataView(out.buffer);
  view.setUint8(0, (key ? 1 : 0) | (first ? 2 : 0) | (last ? 4 : 0));
  view.setBigUint64(1, BigInt(stamp), true);
  view.setUint16(9, index, true);
  view.setUint16(11, pieces, true);
  // The host's own number for the frame. Every piece of one frame carries the
  // same number, which is what lets the far end report progress in numbers
  // the host assigned rather than in a count of its own.
  if (first) framedSeq += 1;
  view.setUint32(13, framedSeq, true);
  out.set(new Uint8Array(bytes), 17);
  return out.buffer;
};
self_.onmessage({ data: { chunk: framed(keyframe, true, 0, true, true) } });
check(built[0].chunks.length === 1, "the keyframe was fed to the decoder");
/* The page drives the painting with animation frames now, so the harness
   has to be the page: advance the clock a refresh and tell the worker. */
const refresh = async (times) => {
  for (let i = 0; i < times; i += 1) {
    clock += 1000 / 60;
    self_.onmessage({ data: { tick: true } });
    await new Promise((go) => globalThis.setTimeout(go, 0));
  }
};

output(new FakeFrame(0));
await refresh(6);
check(painted === 1, `one frame out, ${painted} painted`);
check(closed === 1, "and the frame was closed, so its buffer goes back");

console.log("a run of them, one paint per turn of the event loop");
// A transferred OffscreenCanvas shows what was drawn when the task that drew
// it ends. Draining a queue in one go therefore shows the last frame of the
// batch and loses the rest invisibly -- 361 painted in the counters and one
// frozen picture on the screen.
for (let i = 1; i <= 30; i += 1) {
  output(new FakeFrame(i * 16700));
  await refresh(1);
}
await refresh(6);
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
check(paintSrc.includes("function makePacer("),
      "the schedule is built by a named thing that can be read");
// Both of the faults this has been through, so neither comes back. First an
// absolute schedule built by mapping the capture clock onto this one, which
// drifted until every frame was late on arrival and the queue drained as fast
// as the event loop allowed: "painted every 10ms typical, worst 176ms, 224 of
// 475 off the beat", on a stream sending an even sixty. Then an interval
// accumulator, which was open-loop and drifted the other way: 51 of 350
// refreshes with nothing to paint. The pacer is the third answer and the
// first one with a control loop in it -- the baseline tracks the clock
// difference instead of assuming it away.
check(paintSrc.includes("const delay = nowMs - captureMs;"),
      "from the difference between the clocks, so their origins cancel and "
      + "neither has to be known");
check(paintSrc.includes("nowMs + wait : nowMs"),
      "and a deadline is never in the past: a frame that has already spent "
      + "the reserve goes out now rather than being held longer");
// A worker cannot see the display's refresh, and a paint landing at an
// arbitrary point in the refresh cycle is shown on the next one -- so two
// paints either side of a boundary are a refresh apart and two inside one
// are shown together. The picture sticks and catches up while every paint is
// perfectly spaced, which is what "17ms typical, worst 21ms, 0 off the beat"
// and still not smooth means.
check(workerSrc.includes("function tick()"),
      "the worker paints one frame per animation frame");
// A frame is shown for one refresh or two; there is no showing one for one
// and a half. A schedule in fractions of a refresh drifts through the
// boundary, and each crossing shows one frame twice and skips the next --
// 45 paints in 670 off the beat on a game, none on a test pattern whose
// capture intervals are exact.
// drawImage of a VideoFrame onto a 2D context blocks when the swap chain is
// full, and a paint that blocks lands late -- one frame shown for two
// refreshes and the next skipped, which is "worst 60ms" in an otherwise even
// report. moonlight-web names the same thing: "the synchronous drawImage
// blocking on a full swap chain, the signature of presentation
// back-pressure", and prefers a GL renderer for it.
check(workerSrc.includes("function makeGlPainter"),
      "there is a GL painter, which does not block that way");
// texImage2D does not upload into a texture, it *reallocates* one. At
// 2560x1600 that is sixteen megabytes of GPU memory allocated and released
// sixty times a second -- a gigabyte a second through the driver's allocator,
// for a picture whose size has not changed since it started. Allocators
// answer that by fragmenting and then compacting, and a compaction is a
// stall: the bad hang every twenty seconds that the media path never shows,
// because a <video> element is composited and never goes near WebGL.
check(workerSrc.includes("gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0"),
      "and it writes into the texture it already has, every frame");
const painting = workerSrc.slice(workerSrc.indexOf('what: "webgl"'),
                                 workerSrc.indexOf('what: "webgl"') + 700);
check(!painting.includes("gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA"),
      "rather than allocating a new one for each");
check(workerSrc.includes("if (w !== texW || h !== texH)"),
      "allocating only when the picture's size actually changes");
check(workerSrc.includes("gl.getContext") === false
      && workerSrc.includes('getContext("webgl2"'),
      "asked for first");
check(workerSrc.includes("function makeFlatPainter"),
      "and a 2D one behind it, so a browser without WebGL still gets a picture");
check(workerSrc.includes("makeGlPainter(state.canvas) || makeFlatPainter"),
      "in that order");
check(workerSrc.includes("gl_VertexID"),
      "the triangle comes from the vertex shader, so there is nothing to bind "
      + "and nothing to keep in step with the canvas size");

// The presentation clock, which is moonlight-web's FramePacer. What was here
// was an interval accumulator -- `nextAt += gap` from the capture deltas,
// rounded to whole refreshes, nudged by the queue depth. Open-loop, so it
// drifted against the arrivals (the host's sixty a second and the screen's
// sixty a second are not the same sixty), and the clamp that stopped it
// scheduling into the past collapsed the schedule when it did. Measured on a
// game: 51 of 350 refreshes with nothing to paint and 22 frames thrown away
// for being too late, out of a stream the host had sent perfectly.
check(!workerSrc.includes("state.nextAt")
      && !workerSrc.includes("state.lastPts"),
      "no interval accumulator is left in the worker (the note saying what "
      + "it was stays, which is why this looks for the state and not the word)");
check(workerSrc.includes("state.pacer.schedule(captured, performance.now())"),
      "a frame's deadline comes from the pacer, and it is used rather than "
      + "worked out and thrown away, which is what the old code did with it");
check(paintSrc.includes("baseline = Math.min(delay, baseline + c.DRIFT)"),
      "the baseline is the best transit seen lately: down at once, up only by "
      + "a drift, so a rate difference between the clocks is tracked instead "
      + "of accumulated -- which is the objection to an absolute schedule, "
      + "answered rather than avoided");
check(paintSrc.includes("Math.abs(delay - baseline) > c.RESYNC_MS"),
      "and a discontinuity rebases outright, which is what a pipeline restart "
      + "or a throttled tab looks like from here");
check(paintSrc.includes("targetMs = want;") && paintSrc.includes("DECAY_STEP"),
      "the reserve rises at once and decays slowly: the judder has already "
      + "happened, and one calm second is not evidence");
check(paintSrc.includes("if (targetMs < c.DEADBAND) targetMs = c.MIN;"),
      "a negligible reserve snaps to nothing, so a clean link is the "
      + "immediate path and adds no latency at all");
// The trap moonlight-web documents from having measured it: bumping on every
// late frame compounds, because decay sheds 8ms a second while each outlier
// adds 8ms. They measured 24ms of reserve held against a p95 tail of 2.7ms.
check(paintSrc.includes("Math.min(c.MAX, targetMs + c.BUMP, justified)"),
      "and a bump is capped by the same tail estimate, or a link with a few "
      + "percent of late frames ratchets to the cap and pins there");
check(paintSrc.includes("function maxFor(frames)"),
      "the Smoothing setting moves the cap on the reserve, which is the one "
      + "real trade and the guest's to make");
check(workerSrc.includes("function refreshEvery()"),
      "the refresh is still measured, since 60Hz, 120Hz and 59.94 are all real");
check(paintSrc.includes("requestAnimationFrame(beat)"),
      "and the page is what tells it, because only the page can see a refresh");
check(workerSrc.includes("if (!state.timer) pump();"),
      "with a timer armed whatever the beats are doing, a whole refresh "
      + "behind them, so a page that stops sending them loses a refresh "
      + "rather than the picture");

// Presentation is FIFO. A queued frame is early, not stale: the queue *is*
// the reserve. Painting "the newest frame that is due" and closing the rest
// throws away good pictures whenever two land in one refresh -- and two
// landing in one refresh is what jitter looks like.
check(!workerSrc.includes("state.waiting[1].due - now <= LIMITS.SLACK_MS"),
      "nothing overtakes the head of the queue any more");
check(workerSrc.includes("draw(state.waiting.shift().frame)"),
      "the head is what is painted, in order");
check(/QUEUE_CAP = (\d+)/.test(workerSrc), "with a named memory bound");
const cap = Number(workerSrc.match(/QUEUE_CAP = (\d+)/)[1]);
// A memory bound and nothing else. Six -- moonlight-web's -- is right for a
// renderer driven by decoder output, where the queue never grows. Here the
// queue *is* the reserve and a burst after any hiccup fills it, so six was
// throwing away eleven percent of the picture beside a host sending a
// flawless sixty a second. Latency is bounded by time in tick() instead,
// which is the right rule and does not care how deep the queue is.
check(cap >= 20,
      cap + " frames: a memory bound rather than a latency one, since the "
      + "latency is bounded by how overdue the head is");
check(workerSrc.includes("now - state.waiting[0].due > slipped"),
      "which is the rule that actually keeps it current");
check(workerSrc.includes("state.waiting.length > QUEUE_CAP"),
      "and that is the only reason a frame is dropped");
check(workerSrc.includes("state.early += 1")
      && workerSrc.includes("state.starved += 1"),
      "a refresh with nothing due yet and a refresh with nothing at all are "
      + "counted apart: the first is the reserve working and the second is a "
      + "hole in the picture, and they want opposite fixes");
check(workerSrc.includes("state.pacer.ranDry()"),
      "and only the second widens the reserve");
// FIFO with only a count for a bound lets latency creep: 60.00 from the host
// against 59.94 from the screen is one frame every sixteen seconds, and the
// queue grows until it reaches the memory bound a minute and a half later
// with the picture a hundred milliseconds behind for no visible reason.
check(workerSrc.includes("now - state.waiting[0].due > slipped"),
      "the queue is bounded by time as well, so the delay stays the reserve "
      + "whatever the two frame rates are");
check(workerSrc.includes("(refreshEvery() || 1000 / 60) * 1.5"),
      "at more than a whole refresh, because the head is legitimately a "
      + "little overdue at equilibrium -- anything less throws away one of "
      + "every pair on a link that delivers in pairs");
check(workerSrc.includes("state.waiting.length > 1 && now -"),
      "and never the last frame in hand");

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
self_.onmessage({ data: { chunk: framed(head, true, 5, true, false, 0, 2) } });
check(built[0].chunks.length === fedBefore,
      "a first piece on its own decodes nothing");
self_.onmessage({ data: { chunk: framed(tail, true, 5, false, true, 1, 2) } });
check(built[0].chunks.length === fedBefore + 1,
      "and the last piece completes it");
const rebuilt = new Uint8Array(built[0].chunks[fedBefore].data);
check(rebuilt.length === half.length,
      `put back to its full length: ${rebuilt.length} of ${half.length}`);

console.log("a frame with a piece missing is dropped, not decoded");
// The channel gives each piece a lifetime now rather than retransmitting it
// for ever while everything behind it waits, so pieces can go missing. A head
// and a tail concatenated is not a frame: feeding one to the decoder turns a
// stall into corruption that lasts until the next keyframe, and with an
// infinite GOP there is no next one unless somebody asks.
const beforeHole = built[0].chunks.length;
const askedBefore = sent.filter((m) => m.ask === "key").length;
self_.onmessage({ data: { chunk: framed(head, true, 7, true, false, 0, 3) } });
// Piece 1 never arrives; piece 2 does.
self_.onmessage({ data: { chunk: framed(tail, true, 7, false, true, 2, 3) } });
check(built[0].chunks.length === beforeHole,
      "nothing was decoded from the pieces that did arrive");
check(sent.filter((m) => m.ask === "key").length === askedBefore + 1,
      "and a keyframe was asked for");

console.log("a frame that is never finished does not swallow the next one");
const beforeOrphan = built[0].chunks.length;
self_.onmessage({ data: { chunk: framed(head, true, 8, true, false, 0, 2) } });
self_.onmessage({ data: { chunk: framed(keyframe, true, 9, true, true) } });
check(built[0].chunks.length === beforeOrphan + 1,
      "the whole frame behind it was decoded");

console.log("and what arrived is reported back once a second");
// The host's own send queue read empty while this end was receiving
// thirty-six of every sixty frames sent. An empty queue proves the bytes were
// handed to SCTP, not that they turned up; only this end knows that.
const tallies = () => sent.filter((m) => m.tally).length;
const talliedBefore = tallies();
await refresh(2);
check(tallies() === talliedBefore, "not on every refresh");
clock += 1200;
self_.onmessage({ data: { tick: true } });
await new Promise((go) => globalThis.setTimeout(go, 0));
const tally = sent.filter((m) => m.tally).pop();
check(tallies() === talliedBefore + 1 && tally, "but once the second is up");
check(typeof tally.tally.got === "number"
      && typeof tally.tally.shown === "number"
      && typeof tally.tally.seq === "number",
      "saying how many arrived, how many were painted, and which of the "
      + "host's frames was the last to turn up");

console.log("a gap in the host's numbering waits for a keyframe");
// The host drops to the next keyframe under congestion rather than punching a
// hole -- a WebCodecs decoder answers a missing reference with `Decoding
// error` and stops, which tears the whole picture down. This is the half that
// does not depend on the host having remembered to: the frame numbers are on
// the wire, so a gap is visible from here.
const beforeGap = built[0].chunks.length;
const askedGap = sent.filter((m) => m.ask === "key").length;
// Past the rate limit on asking, which an earlier case in this file has just
// spent. Asking is deliberately throttled: a host answering every request
// spends the whole bitrate on recovery.
clock += 1000;
framedSeq += 5;                        // five frames that never arrived
self_.onmessage({ data: { chunk: framed(deltaFrame, false, 20, true, true) } });
check(built[0].chunks.length === beforeGap,
      "the frame after the gap is not fed to the decoder");
check(sent.filter((m) => m.ask === "key").length > askedGap,
      "and a keyframe is asked for");
self_.onmessage({ data: { chunk: framed(deltaFrame, false, 21, true, true) } });
check(built[0].chunks.length === beforeGap,
      "nor is the one after that, until a keyframe comes");
self_.onmessage({ data: { chunk: framed(keyframe, true, 22, true, true) } });
check(built[0].chunks.length === beforeGap + 1,
      "and the keyframe restarts it");

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
