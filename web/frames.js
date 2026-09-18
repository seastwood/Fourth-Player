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
  early: 0,
  behind: 0,
  everPainted: false,
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
    // Said out loud, because the page cannot see it.
    //
    // The canvas belongs to this worker now, and the <video> element the page
    // measures its geometry from has had the picture taken off it -- it is
    // holding the sound and nothing else while this path draws. So the page's
    // idea of the picture's shape went to zero the moment WebCodecs started,
    // and everything worked out from it -- how far the picture may be
    // dragged, how far a finger moves the pointer, where the pointer is on
    // the screen -- fell back to the shape of the *element*, which on an
    // upright phone is four times too tall. This is the only place that
    // actually knows.
    self.postMessage({ shape: { width: canvas.width, height: canvas.height } });
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
    // The running total is what the host compares against, not `got`: the
    // two ends' seconds are not the same second, and comparing a window here
    // against a window there read as 69% arriving on a LAN carrying
    // everything. Totals cancel whatever the windows do with their edges.
    total: state.gotAll,
    got, shown,
    reserve: Math.round(state.pacer ? state.pacer.reserve() : 0),
  } });
}

function tick() {
  state.ticks += 1;
  // Only to the memory bound, oldest first.
  //
  // A queued frame is early, not stale: the queue *is* the reserve. This used
  // to trim to a target depth and then paint "the newest frame that is due",
  // closing the rest -- which throws away perfectly good pictures whenever
  // two land in one refresh, and two landing in one refresh is exactly what
  // jitter looks like. That was the 22 frames "too late to matter" in a run
  // where the host had sent every one of them on time. Presentation is FIFO
  // now, as moonlight-web's is: nothing is dropped except to bound memory,
  // and a frame that is early waits its turn instead of being overtaken.
  while (state.waiting.length > QUEUE_CAP) {
    state.waiting.shift().frame.close();
    state.stale += 1;
  }
  if (!state.waiting.length) {
    // Nothing arrived in time. This is the real underrun -- a hole in the
    // picture, and the network or the host rather than anything here -- and
    // the pacer is told, because widening the reserve is what covers it.
    // Only once something has been painted: the queue is empty before the
    // first frame too, and that is not evidence about the link.
    state.starved += 1;
    if (state.everPainted && state.pacer) state.pacer.ranDry();
    return;
  }
  const now = performance.now();
  // Catching up, when genuinely behind.
  //
  // FIFO with only a count for a bound lets latency creep. Sixty a second
  // from the host and 59.94 from the screen is a tenth of a percent, which is
  // one frame every sixteen seconds -- the queue grows by one, then another,
  // until it reaches the memory bound a minute and a half later and the
  // picture is a hundred milliseconds behind for no reason anybody can see.
  // Bounding by *time* instead pins the delay to the reserve whatever the two
  // rates are: while the head should already have been shown, drop it.
  //
  // The threshold is more than one refresh because the head is legitimately a
  // little overdue at equilibrium -- this only looks once per refresh, so a
  // frame due just after the last look is up to a refresh old by this one.
  // Dropping at anything less throws away one of every pair on a link that
  // delivers in pairs, which is most of them, and reads as skipping.
  const slipped = (refreshEvery() || 1000 / 60) * 1.5;
  while (state.waiting.length > 1 && now - state.waiting[0].due > slipped) {
    state.waiting.shift().frame.close();
    state.behind += 1;
  }
  if (state.waiting[0].due - now > LIMITS.SLACK_MS) {
    // Early, not starved. Counted apart because the two want opposite fixes:
    // this one means the reserve is doing its job.
    state.early += 1;
    return;
  }
  state.everPainted = true;
  draw(state.waiting.shift().frame);
}

function pump() {
  state.timer = 0;
  state.drew = false;
  // Only to the memory bound, and then the head's turn -- the same rule as
  // tick(), because a page that has stopped sending animation frames should
  // see the same picture, only timed by a timer rather than by a refresh.
  while (state.waiting.length > QUEUE_CAP) {
    state.waiting.shift().frame.close();
    state.stale += 1;
  }
  if (!state.waiting.length) return;
  const next = state.waiting[0];
  const wait = next.due - performance.now();
  if (wait > LIMITS.SLACK_MS) {
    state.timer = setTimeout(pump, wait);
    return;
  }
  state.waiting.shift();
  state.everPainted = true;
  draw(next.frame);
  // One paint per turn of the event loop, always.
  //
  // A transferred OffscreenCanvas shows what was drawn on it when the task
  // that drew ends. Draining the whole queue in one go therefore shows the
  // last frame of the batch and throws the rest away invisibly, and painting
  // straight out of the decoder's callback can leave several in one turn.
  // Yielding after each is what makes a painted frame a shown frame -- which
  // is the difference between "361 painted" in the counters and one frozen
  // picture on the screen.
  if (state.waiting.length) state.timer = setTimeout(pump, 0);
}

/* How deep the queue of frames waiting to be shown may get.
 *
 * With a reserve, frames legitimately wait their turn, so this has to clear
 * the deepest reserve plus whatever bunching the link delivers -- or it drops
 * exactly what it just decided to hold. Six is moonlight-web's figure and is
 * about a hundred milliseconds at 60fps, which is well past any reserve the
 * pacer will ask for.
 *
 * The scheduling that used to live here was an interval accumulator --
 * `nextAt += gap`, from the capture deltas, rounded to whole refreshes and
 * nudged by the queue depth. It is gone; see the note on makePacer in
 * paint.js for what it was doing wrong and what replaces it. The short of it
 * is that an open-loop clock drifts against the arrivals, and the clamp that
 * stopped it scheduling into the past collapsed the schedule when it did.
 */
const QUEUE_CAP = 6;

/* The Smoothing setting, in frames, as the page last said. Three is the
   default and gives moonlight-web's 25ms cap. */
let SMOOTHING = 3;

/* The display's own interval, learnt from the ticks. Kept for the report:
   a paint cadence means nothing without knowing what the screen can show. */
function refreshEvery() {
  if (state.beats.length < 12) return 0;
  const sorted = state.beats.slice().sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

function decoded(frame) {
  state.out += 1;
  const captured = (frame.timestamp || 0) / 1000;
  // Timed from the decoder's output rather than from the frame's arrival, as
  // moonlight-web does and for their reason: decode time varies too, and it
  // is the cadence of what reaches the screen that has to be even, so one
  // mechanism absorbs both sources of variance.
  const due = state.pacer.schedule(captured, performance.now());
  state.waiting.push({ frame, due });
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
  state.everPainted = false;
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
    if (m.start.smoothing) {
      SMOOTHING = Math.max(1, Math.min(10, m.start.smoothing | 0));
    }
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
    state.pacer.cap(SMOOTHING);
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
        // What the pacer is actually seeing, so a jittery link can be told
        // from a reserve that is too small for it. `early` is a refresh where
        // the next frame was simply not due yet, which is the reserve working
        // -- the opposite of `starved`, and the two used to be one number.
        early: state.early,
        behind: state.behind,
        pace: state.pacer ? state.pacer.stats() : null,
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
      state.early = state.behind = 0;
      state.lost = 0;
      state.drawFails = 0;
      state.ticks = state.starved = 0;
    }
    return;
  }
  if (m.smoothing) {
    // The one real trade, and the guest's to make: every millisecond of
    // reserve is a millisecond of delay and a hiccup absorbed. The setting
    // moves the *cap* on the reserve; what is actually held inside it is
    // whatever the link turns out to need, which on a clean one is nothing.
    SMOOTHING = Math.max(1, Math.min(10, m.smoothing | 0));
    if (state.pacer) state.pacer.cap(SMOOTHING);
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
