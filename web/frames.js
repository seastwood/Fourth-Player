/* Everything about the picture, on a thread of its own.
 *
 * This started as a courier: it read the encoded frames and posted them to
 * the page, which decoded and drew them. That was the wrong shape and the
 * counters said so -- around twenty frames a second reached the canvas out of
 * sixty sent, with the decoder and the painting both reporting themselves
 * perfectly healthy, because the work they were doing was queued behind
 * everything else the page does: pad polling, pointer handling, the desk,
 * layout. A 1440p frame drawn on the main thread sixty times a second is not
 * something to ask of it while a game is being played through it.
 *
 * moonlight-web, which this is modelled on, decodes and renders in the worker
 * and never posts a frame anywhere. Here that is better still: the transform
 * already delivers into this worker, so with the decoder and the canvas here
 * too, a frame is never copied out of this thread at all -- it arrives,
 * decodes and is painted without the page being involved.
 *
 * The page keeps what only it can do: choosing the codec, deciding what to do
 * when this cannot work, and putting the numbers on screen.
 */
importScripts("/static/paint.js");

const LIMITS = PACE;

/* How deep the decoder's own queue may get before frames are thrown away.
 *
 * A decoder that has fallen behind does not catch up by being given more:
 * every frame handed to a saturated decoder is latency that somebody watching
 * will feel and never see the end of. Keyframes are never dropped -- dropping
 * one costs everything until the next. */
const QUEUE_MAX = 6;

const state = {
  decoder: null,
  canvas: null,
  context: null,
  pacer: null,
  codec: "",
  feedAs: "annexb",
  lastKey: null,
  started: false,
  shape: "",
  waiting: [],
  timer: 0,
  rung: 0,
  handed: 0, fed: 0, out: 0, drawn: 0, refused: 0, skipped: 0, stale: 0,
  lost: 0,
  gotAll: 0, shownAll: 0, toldAt: 0, toldGot: 0, toldShown: 0,
  ever: false,
  saidDraw: false,
  saidFirst: false,
  drawFails: 0,
  drew: false,
  lastDraw: 0,
  shown: [],
  lastPts: null,
  nextAt: null,
  ticked: false,
  ticks: 0,
  starved: 0,
  beats: [],
  lastBeat: 0,
};

function say(text) { self.postMessage({ note: text }); }

/* What the gaps between paints looked like, in one small object. */
function shownSpread() {
  const gaps = state.shown.slice().sort((a, b) => a - b);
  state.shown = [];
  if (gaps.length < 10) return null;
  const nominal = gaps[Math.floor(gaps.length / 2)];
  return {
    typical: Math.round(nominal * 10) / 10,
    worst: Math.round(gaps[gaps.length - 1]),
    // Painted more than half a frame away from the middle of the pack, in
    // either direction: too close together is as visible as too far apart.
    off: gaps.filter((g) => Math.abs(g - nominal) > nominal * 0.5).length,
    of: gaps.length,
  };
}

/* Two ways to put a frame on the canvas, and the first is worth the code.
 *
 * drawImage of a VideoFrame onto a 2D context is the obvious way and it has
 * a property that matters here: it blocks when the swap chain is full. A
 * paint that blocks is a paint that lands late, and one late paint is a
 * frame shown for two refreshes and the next one skipped -- which is what
 * "worst 60ms" in an otherwise even report has been all along. moonlight-web
 * says the same thing about it in as many words: "the synchronous drawImage
 * blocking on a full swap chain, the signature of presentation
 * back-pressure", and prefers a GL renderer for exactly this reason.
 *
 * Uploading the frame as a texture and drawing one triangle over the canvas
 * does not block that way. The triangle is generated in the vertex shader
 * from gl_VertexID, so there are no buffers to bind and nothing to keep in
 * step with the canvas size.
 */
function makeGlPainter(canvas) {
  let gl = null;
  try {
    gl = canvas.getContext("webgl2", {
      alpha: false, antialias: false, depth: false, stencil: false,
      desynchronized: true, preserveDrawingBuffer: false,
      powerPreference: "high-performance",
    });
  } catch (_) { return null; }
  if (!gl) return null;

  const build = (kind, text) => {
    const part = gl.createShader(kind);
    gl.shaderSource(part, text);
    gl.compileShader(part);
    if (!gl.getShaderParameter(part, gl.COMPILE_STATUS)) return null;
    return part;
  };
  const vertex = build(gl.VERTEX_SHADER, `#version 300 es
out vec2 uv;
void main() {
  vec2 corner = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
  uv = vec2(corner.x, 1.0 - corner.y);
  gl_Position = vec4(corner * 2.0 - 1.0, 0.0, 1.0);
}`);
  const fragment = build(gl.FRAGMENT_SHADER, `#version 300 es
precision mediump float;
in vec2 uv;
uniform sampler2D picture;
out vec4 colour;
void main() { colour = texture(picture, uv); }`);
  if (!vertex || !fragment) return null;
  const program = gl.createProgram();
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return null;
  gl.useProgram(program);

  const texture = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.uniform1i(gl.getUniformLocation(program, "picture"), 0);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);

  return {
    what: "webgl",
    paint(frame) {
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE,
                    frame);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    },
  };
}

function makeFlatPainter(canvas) {
  let context = null;
  try {
    context = canvas.getContext("2d", { alpha: false });
  } catch (_) { return null; }
  if (!context) return null;
  return {
    what: "2d",
    paint(frame) { context.drawImage(frame, 0, 0); },
  };
}

function draw(frame) {
  const canvas = state.canvas;
  if (!canvas) { frame.close(); return; }
  if (canvas.width !== frame.displayWidth
      || canvas.height !== frame.displayHeight) {
    canvas.width = frame.displayWidth;
    canvas.height = frame.displayHeight;
  }
  try {
    if (!state.context) throw new Error("no way to paint");
    state.context.paint(frame);
    state.drawn += 1;
    state.shownAll += 1;
    // How evenly the picture is actually painted, which is the only thing
    // anybody watching can see. Every other number in this report describes
    // something upstream of the eye: frames handed over, fed, decoded. A
    // stream can be perfect at all of those and still be painted unevenly,
    // and that is what "it feels like it is skipping" means.
    const at = performance.now();
    if (state.lastDraw) state.shown.push(at - state.lastDraw);
    state.lastDraw = at;
    if (!state.ever) {
      state.ever = true;
      // Said the moment it happens rather than waiting to be asked. Whether
      // anything has been painted is what decides the fallback, and a
      // deadline that arrives before the first report has nothing to read.
      self.postMessage({ painted: true });
    }
  } catch (err) {
    // Said once. A paint that throws every frame is a black screen with a
    // decoder reporting itself perfectly healthy, and nothing about the
    // counters distinguishes it from a decoder producing nothing.
    state.drawFails += 1;
    if (!state.saidDraw) {
      state.saidDraw = true;
      say("the canvas would not take a frame: "
          + ((err && err.message) || "no reason given"));
    }
  }
  frame.close();
}

/* Paint at most one frame, if one is due.
 *
 * Driven by the page's animation frames rather than by a timer of its own.
 * A worker has no way to see the display's refresh, and a paint that lands
 * at an arbitrary moment in the refresh cycle is shown on the next one --
 * so two paints either side of a boundary are shown one refresh apart while
 * two in the middle of one are shown together, and the picture appears to
 * stick and catch up even when every paint is perfectly spaced. Which is
 * exactly what the numbers said: "17ms typical, worst 21ms, 0 of 601 off the
 * beat", and still not smooth to look at.
 *
 * An animation frame happens just after a refresh. Painting there puts every
 * frame on the display's own cadence instead of near it.
 */
/* Tell the host what actually arrived, once a second.
 *
 * The host's own send queue read empty while this end was receiving
 * thirty-six of every sixty frames sent -- an empty queue only proves the
 * bytes were handed to SCTP, not that they turned up. So this end counts, and
 * says so, and the host steers the encoder by the difference. It is the one
 * measurement of the link that cannot be fooled.
 */
const TELL_EVERY = 1000;

function tell(at) {
  if (!state.toldAt) { state.toldAt = at; return; }
  if (at - state.toldAt < TELL_EVERY) return;
  state.toldAt = at;
  const got = state.gotAll - state.toldGot;
  const shown = state.shownAll - state.toldShown;
  state.toldGot = state.gotAll;
  state.toldShown = state.shownAll;
  self.postMessage({ tally: {
    got, shown,
    late: Math.round(state.pacer ? -state.pacer.reserve() : 0),
  } });
}

function tick() {
  state.ticks += 1;
  if (!state.waiting.length) {
    // Nothing to paint. A refresh with an empty queue and a refresh where
    // the next frame is simply not due yet are different things, and only
    // the first is a hole in the picture: it means nothing arrived in time,
    // which is the network or the host rather than anything here. Counted
    // because a 209ms gap between paints has two possible causes and they
    // want opposite fixes.
    state.starved += 1;
    return;
  }
  while (state.waiting.length > DEPTH_WANT * 3) {
    state.waiting.shift().frame.close();
    state.stale += 1;
  }
  // The newest frame that is due, not the oldest.
  //
  // A refresh can show one frame. When more than one is due -- which is
  // every refresh when the stream sends more frames a second than the screen
  // can show -- the oldest of them is the wrong choice: it is the most stale
  // picture available, and picking it every time means running a fixed
  // number of frames behind until something drops the backlog in one go.
  // Showing the newest and letting the rest go keeps the picture current and
  // spreads the dropping evenly, which is what ninety frames into sixty
  // refreshes has to do anyway.
  const now = performance.now();
  if (state.waiting[0].due - now > LIMITS.SLACK_MS) return;
  while (state.waiting.length > 1
         && state.waiting[1].due - now <= LIMITS.SLACK_MS) {
    state.waiting.shift().frame.close();
    state.stale += 1;
  }
  const next = state.waiting.shift();
  draw(next.frame);
}

function pump() {
  state.timer = 0;
  state.drew = false;
  while (state.waiting.length) {
    // Drop to the latest. A frame that was due while the thread was busy is
    // not worth drawing once a newer one exists: it costs a paint and shows
    // somebody a picture they have already been shown the successor of.
    // Only when genuinely behind. Dropping whenever the next frame happened
    // to be due threw away one of every pair on a link that delivers in
    // pairs, which is most of them, and reads as skipping.
    while (state.waiting.length > DEPTH_WANT * 3) {
      state.waiting.shift().frame.close();
      state.stale += 1;
    }
    const next = state.waiting[0];
    const wait = next.due - performance.now();
    if (wait > LIMITS.SLACK_MS) {
      state.timer = setTimeout(pump, wait);
      return;
    }
    state.waiting.shift();
    draw(next.frame);
    // One paint per turn of the event loop, always.
    //
    // A transferred OffscreenCanvas shows what was drawn on it when the task
    // that drew ends. Draining the whole queue in one go therefore shows the
    // last frame of the batch and throws the rest away invisibly, and
    // painting straight out of the decoder's callback can leave several in
    // one turn. Yielding after each is what makes a painted frame a shown
    // frame -- which is the difference between "361 painted" in the counters
    // and one frozen picture on the screen.
    if (state.waiting.length) {
      state.timer = setTimeout(pump, 0);
    }
    return;
  }
}

/* When to paint the next frame.
 *
 * Built from the *differences* between capture times and nothing else, which
 * is the whole point. Mapping a capture clock onto this one needs the two to
 * agree about where zero is and how fast a second passes, and they do not:
 * the capture clock restarts whenever the pipeline does, and neither runs at
 * exactly the rate of the other. An absolute schedule built on that mapping
 * drifts until every frame is already late on arrival, and a queue of frames
 * that are all late is drained as fast as the event loop allows -- which was
 * measured as "painted every 10ms typical, worst 176ms, 224 of 475 off the
 * beat" on a stream sending an even sixty a second.
 *
 * Differences need no agreement about anything. The gap between two capture
 * timestamps is how far apart the pictures are, so painting them that far
 * apart is right whatever either clock thinks the time is.
 *
 * The queue depth is the only correction: too many waiting means this end is
 * behind and should hurry slightly; none waiting means it may relax. Both
 * are gentle, because a renderer that lurches is the thing being fixed.
 */
const GAP_MIN = 4, GAP_MAX = 250;       // a sane frame interval, in ms
/* How many frames to keep in hand. Set by the page -- see the Smoothing
   setting -- because it is the one real trade here and different rooms want
   different answers: every frame held is a frame of delay, and every frame
   held is a hiccup absorbed. Moonlight calls the same choice frame pacing. */
let DEPTH_WANT = 2;
let START_DEPTH = 3;

/* The display's own interval, learnt from the ticks. */
function refreshEvery() {
  if (state.beats.length < 12) return 0;
  const sorted = state.beats.slice().sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

function schedule(captureMs) {
  const now = performance.now();
  let gap = 1000 / 60;
  if (state.lastPts !== null) {
    gap = Math.min(GAP_MAX, Math.max(GAP_MIN, captureMs - state.lastPts));
  }
  state.lastPts = captureMs;
  // Rounded to whole refreshes, which is what frame pacing means.
  //
  // A frame is shown for one refresh or two or three; there is no such thing
  // as showing one for one and a half. So a schedule in fractions of a
  // refresh is a schedule that cannot be kept: it drifts through the
  // boundary, and each time it crosses, one frame is shown twice and the
  // next is skipped. Measured on a game, with nothing starving and every
  // frame in hand: 45 paints in 670 off the beat. The test pattern, whose
  // capture intervals are exact, had none.
  //
  // Rounding also settles the correction below. Nudging a fractional gap by
  // a tenth of a frame moved the phase a little every time; nudging a whole
  // refresh moves it once and stops.
  const refresh = refreshEvery();
  if (refresh > 0 && gap >= refresh * 0.9) {
    // Only when a frame can have a refresh of its own. Rounding a gap that
    // is already shorter than a refresh *up* to one makes the schedule
    // advance more slowly than the frames arrive -- ninety a second into a
    // sixty hertz screen, and the queue grows until something dumps it. The
    // measurement: "956 came out, 931 painted, 23 too late to matter", which
    // is a fifth of a second of stale picture and then a jump.
    //
    // Below a refresh the true gap is kept, so the schedule tracks the rate
    // the frames are really coming at, and the tick below shows the newest
    // one that is due and lets the others go.
    const steps = Math.max(1, Math.round(gap / refresh));
    gap = steps * refresh;
  }
  if (state.nextAt === null) {
    // The first frame waits, and everything after it inherits that slack.
    //
    // Painting the first one the instant it arrives leaves the renderer with
    // nothing in hand: the next frame to be late is a gap on the screen,
    // because there is no frame behind it to show meanwhile. Measured with
    // no slack at all: sixteen milliseconds typical and a worst of fifty-five,
    // with eight in every hundred paints off the beat -- about five a second,
    // which is what a stutter is.
    //
    // A couple of frames of slack costs a couple of frames of delay and
    // absorbs every hiccup smaller than itself. The queue then sits at that
    // depth on its own, because the depth is what the nudging below keeps.
    state.nextAt = now + gap * START_DEPTH;
    return state.nextAt;
  }
  // Behind or ahead, by a whole refresh or not at all.
  const deep = state.waiting.length;
  const step = refresh > 0 ? refresh : gap * 0.1;
  if (deep > DEPTH_WANT * 2) gap = Math.max(GAP_MIN, gap - step);
  else if (deep === 0) gap += step;
  state.nextAt += gap;
  // Never schedule into the past, or everything after it arrives already
  // late and the queue drains in one burst -- which is the fault this
  // replaces.
  if (state.nextAt < now) state.nextAt = now;
  return state.nextAt;
}

function decoded(frame) {
  state.out += 1;
  const captured = (frame.timestamp || 0) / 1000;
  // Kept for the report, which is where the reserve comes from.
  state.pacer.due(captured, performance.now());
  state.waiting.push({ frame, due: schedule(captured) });
  // The page's animation frames do the painting. The timer below is only
  // for a page that is not sending them -- a backgrounded tab, or a browser
  // without requestAnimationFrame -- where a picture that keeps moving beats
  // one that stops.
  if (!state.ticked && !state.timer) pump();
}

function buildDecoder(codec, description, latency) {
  const config = { codec };
  if (description) config.description = description;
  // optimizeForLatency is a hint and a browser may refuse the whole config
  // over it. It is worth asking for -- it is the difference between a
  // decoder that outputs a frame as soon as it can and one that buffers to
  // be tidy -- but it is not worth failing over, so it is one of the rungs
  // below rather than a fixed part of every attempt.
  if (latency) config.optimizeForLatency = true;
  const decoder = new VideoDecoder({
    output: decoded,
    error: (err) => {
      if (nextRung()) return;
      self.postMessage({
        failed: "the decoder stopped: "
                + ((err && err.message) || "no reason given"),
      });
      close();
    },
  });
  decoder.configure(config);
  return decoder;
}

/* Everything worth asking a decoder, in the order worth asking it.
 *
 * iOS Safari refused both of the first two shapes -- start codes, and the
 * parameter sets up front -- with nothing but "Decoder failure" either time.
 * A decoder that will not say which part of a config it dislikes leaves only
 * one honest method: offer the combinations one at a time and watch. There
 * are four and they cost nothing to walk.
 *
 * The two axes are the bitstream shape (start codes, as WebRTC delivers, or
 * the mp4 shape with a description) and whether the latency hint is asked
 * for. Everything else -- the codec string, the profile, the level -- is read
 * out of the stream by then and is not a guess. */
const RUNGS = [
  { avcc: false, latency: true },
  { avcc: true, latency: true },
  { avcc: true, latency: false },
  { avcc: false, latency: false },
];

function describeKey() {
  if (!state.lastKey) return null;
  try { return avcDescription(state.lastKey); } catch (_) { return null; }
}

/* Bytes 1 to 3 of an avcC box are the profile, compatibility flags and level
   as the *encoder* wrote them. The SDP says what the two ends agreed to send,
   which is not always the same thing, and a decoder handed a description
   compares the two. */
function exactCodec(description) {
  return "avc1." + [description[1], description[2], description[3]]
    .map((b) => (b < 16 ? "0" : "") + b.toString(16).toUpperCase()).join("");
}

function nextRung() {
  const isAvc = state.codec.indexOf("avc1.") === 0;
  while (state.rung + 1 < RUNGS.length) {
    state.rung += 1;
    const want = RUNGS[state.rung];
    let description = null;
    if (want.avcc) {
      if (!isAvc) continue;              // an hvcC is a different box
      description = describeKey();
      if (!description) continue;        // no parameter sets seen yet
    }
    const codec = description ? exactCodec(description) : state.codec;
    const keyframe = state.lastKey;
    try {
      if (state.decoder && state.decoder.state !== "closed") {
        state.decoder.close();
      }
    } catch (_) {}
    try {
      state.decoder = buildDecoder(codec, description, want.latency);
    } catch (err) {
      continue;                          // this rung will not even configure
    }
    state.codec = codec;
    state.feedAs = want.avcc ? "avcc" : "annexb";
    state.started = false;
    say("trying " + codec + (want.avcc ? " with the parameter sets up front"
                                       : " with start codes")
        + (want.latency ? "" : " and no latency hint"));
    if (keyframe) take("key", 0, keyframe);
    return true;
  }
  return false;
}

function take(type, timestamp, data) {
  const decoder = state.decoder;
  if (!decoder || decoder.state !== "configured") return;
  const key = type === "key";
  if (!state.started) {
    if (!key) { state.skipped += 1; return; }
    state.started = true;
  }
  let bytes = data;
  try {
    const shaped = toAnnexB(data instanceof ArrayBuffer ? data
                            : data.buffer || data);
    bytes = shaped.data;
    if (state.shape === "") {
      state.shape = shaped.shape;
      say("the encoded frames are " + state.shape);
    }
    if (key) state.lastKey = bytes;
    if (state.feedAs === "avcc") bytes = toLengthPrefixed(bytes);
  } catch (_) { /* hand it over as it came */ }
  // A saturated decoder is not helped by more. Keyframes always go in:
  // dropping one costs every frame until the next.
  if (!key && decoder.decodeQueueSize >= QUEUE_MAX) {
    state.refused += 1;
    return;
  }
  try {
    decoder.decode(new EncodedVideoChunk({
      type: key ? "key" : "delta", timestamp, data: bytes,
    }));
    state.fed += 1;
  } catch (err) {
    state.refused += 1;
  }
}

function close() {
  if (state.timer) { clearTimeout(state.timer); state.timer = 0; }
  for (const one of state.waiting) { try { one.frame.close(); } catch (_) {} }
  state.waiting = [];
  try {
    if (state.decoder && state.decoder.state !== "closed") state.decoder.close();
  } catch (_) {}
  state.decoder = null;
  state.lastPts = null;
  state.nextAt = null;
}

/* Putting a frame back together.
 *
 * SCTP will not carry an arbitrarily large message and a keyframe is easily
 * larger than a browser's limit, so the host sends each frame in pieces.
 * Thirteen bytes in front of each say whether it starts a frame, whether it
 * ends one, whether the frame is a keyframe, when it was captured, which
 * piece this is and how many there are.
 *
 * The last two are why this is not just a concatenation. The channel is
 * ordered but no longer endlessly reliable: the host gives each piece a
 * lifetime, so a piece that cannot be delivered in time is abandoned rather
 * than retransmitted for ever while everything behind it waits. That trade
 * buys back the stalls -- one loss used to stop the picture dead and then
 * deliver a pile of frames all far too old to show -- and the price is that
 * pieces can go missing. A frame with a hole in it is not a frame, so the
 * numbers make the hole plain, the frame is dropped whole, and the host is
 * asked for a keyframe because everything after a dropped frame decodes
 * against something that never arrived.
 */
const FIRST = 2, LAST = 4;
let building = null;

/* Ask for a keyframe, but not once per lost frame: on a link losing pieces
   steadily that is a request per frame, and a host answering all of them
   spends the whole bitrate on recovery, which makes a struggling link worse.
   The host rate-limits too; this keeps the asking off the wire in the first
   place. */
let askedKeyAt = 0;
const ASK_KEY_EVERY = 400;

function lostFrame(why) {
  building = null;
  state.lost += 1;
  const at = (typeof performance !== "undefined") ? performance.now() : Date.now();
  if (at - askedKeyAt < ASK_KEY_EVERY) return;
  askedKeyAt = at;
  self.postMessage({ ask: "key" });
  if (state.lost <= 3 || state.lost % 200 === 0) {
    say("a frame arrived with a hole in it (" + why + "), so it was dropped "
        + "and a keyframe asked for; " + state.lost + " so far");
  }
}

function chunk(buffer) {
  const view = new DataView(buffer);
  const flags = view.getUint8(0);
  const stamp = Number(view.getBigUint64(1, true));
  const index = view.getUint16(9, true);
  const pieces = view.getUint16(11, true);
  const body = new Uint8Array(buffer, 13);
  if (flags & FIRST) {
    // A frame already under construction when the next one starts means the
    // tail of that one never arrived.
    if (building) lostFrame("its last piece never came");
    building = { key: (flags & 1) !== 0, stamp, parts: [], size: 0,
                 next: 0, pieces };
  }
  if (!building) return;                 // a tail with no head: wait for one
  if (index !== building.next || stamp !== building.stamp) {
    lostFrame("piece " + building.next + " of " + building.pieces
              + " never came");
    return;
  }
  building.next += 1;
  building.parts.push(body);
  building.size += body.length;
  if (!(flags & LAST)) return;
  if (building.next !== building.pieces) {
    lostFrame("it ended after " + building.next + " of " + building.pieces);
    return;
  }
  const whole = new Uint8Array(building.size);
  let at = 0;
  for (const part of building.parts) { whole.set(part, at); at += part.length; }
  const made = building;
  building = null;
  state.handed += 1;
  state.gotAll += 1;
  // Once per connection, not once per report: the counters are zeroed every
  // window, so this was announcing a first frame every twelve seconds.
  if (!state.saidFirst) {
    state.saidFirst = true;
    say("the first encoded frame arrived here");
  }
  take(made.key ? "key" : "delta", made.stamp, whole.buffer);
}

self.onmessage = (event) => {
  const m = event.data || {};
  if (m.start) {
    state.canvas = m.start.canvas;
    // No `desynchronized` here, and no assuming a context came back.
    //
    // That hint is for a canvas on a page, where it lets the browser skip a
    // compositing step; on an OffscreenCanvas it is at best ignored and at
    // worst the reason getContext returns null. A null context is a black
    // screen with every other number looking perfect, because the only thing
    // that fails is a drawImage inside a try -- which is exactly the shape of
    // failure this has produced twice already, so it is said out loud rather
    // than caught and swallowed.
    state.context = makeGlPainter(state.canvas) || makeFlatPainter(state.canvas);
    if (!state.context) {
      self.postMessage({ failed: "this browser gave the worker no way to "
                                 + "draw on the canvas" });
      return;
    }
    say("painting with " + state.context.what);
    state.pacer = makePacer(LIMITS);
    state.codec = m.start.codec;
    try {
      state.rung = 0;
      state.decoder = buildDecoder(state.codec, null, true);
    } catch (err) {
      self.postMessage({
        failed: "no decoder would start for " + state.codec,
      });
      return;
    }
    self.postMessage({ started: true });
    return;
  }
  if (m.codec) {
    // Another spelling, on the transform that is already delivering.
    close();
    state.codec = m.codec;
    state.feedAs = "annexb";
    state.started = false;
    state.rung = 0;
    try {
      state.decoder = buildDecoder(state.codec, null, true);
    } catch (err) {
      self.postMessage({ failed: "no decoder would start for " + m.codec });
      return;
    }
    self.postMessage({ started: true });
    return;
  }
  if (m.report || m.peek) {
    self.postMessage({
      stats: {
        handed: state.handed, fed: state.fed, out: state.out,
        drawn: state.drawn, refused: state.refused,
        skipped: state.skipped, stale: state.stale,
        lost: state.lost,
        // Whether a keyframe has been seen at all. Frames arriving and
        // nothing being painted is two different situations: a decoder that
        // will not work, and a decoder that has not been given anything it
        // can start from. They want opposite answers.
        keyed: state.started,
        reserve: Math.round(state.pacer ? state.pacer.reserve() : 0),
        ever: state.ever,
        // The surface itself, because every counter can read perfectly while
        // nothing reaches the screen.
        size: state.canvas ? (state.canvas.width + "x" + state.canvas.height)
                           : "none",
        drawFails: state.drawFails,
        shown: m.report ? shownSpread() : null,
        ticks: state.ticks,
        starved: state.starved,
        refresh: Math.round(refreshEvery() * 10) / 10,
        how: state.context ? state.context.what : "none",
      },
    });
    // Only a report empties them. A peek is somebody checking whether
    // anything is being painted at all, and two callers sharing one counter
    // that empties when it is read means whichever asks second is told
    // nothing happened -- which is how "Drawing 0 of 60 frames a second"
    // came to be said about a picture that was playing perfectly.
    if (m.report) {
      state.handed = state.fed = state.out = state.drawn = 0;
      state.refused = state.skipped = state.stale = 0;
      state.lost = 0;
      state.drawFails = 0;
      state.ticks = state.starved = 0;
    }
    return;
  }
  if (m.smoothing) {
    START_DEPTH = Math.max(1, Math.min(10, m.smoothing | 0));
    DEPTH_WANT = Math.max(1, START_DEPTH - 1);
    return;
  }
  if (m.tick) {
    state.ticked = true;
    // The interval between refreshes, measured rather than assumed: 60Hz,
    // 120Hz and 59.94 are all in the field and only the display knows.
    const at = performance.now();
    if (state.lastBeat) {
      const beat = at - state.lastBeat;
      if (beat > 2 && beat < 80) {
        state.beats.push(beat);
        if (state.beats.length > 60) state.beats.shift();
      }
    }
    state.lastBeat = at;
    tick();
    tell(at);
    return;
  }
  if (m.chunk) { chunk(m.chunk); return; }
  if (m.stop) { close(); building = null; }
};

// Last, so nothing can be sent before the handlers above exist.
self.postMessage({ ready: true });
