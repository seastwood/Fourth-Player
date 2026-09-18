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
  ever: false,
};

function say(text) { self.postMessage({ note: text }); }

function draw(frame) {
  const canvas = state.canvas;
  if (!canvas) { frame.close(); return; }
  if (canvas.width !== frame.displayWidth
      || canvas.height !== frame.displayHeight) {
    canvas.width = frame.displayWidth;
    canvas.height = frame.displayHeight;
  }
  try {
    state.context.drawImage(frame, 0, 0);
    state.drawn += 1;
    state.ever = true;
  } catch (_) { /* the canvas went away */ }
  frame.close();
}

function pump() {
  state.timer = 0;
  while (state.waiting.length) {
    // Drop to the latest. A frame that was due while the thread was busy is
    // not worth drawing once a newer one exists: it costs a paint and shows
    // somebody a picture they have already been shown the successor of.
    while (state.waiting.length > 1
           && state.waiting[1].due <= performance.now()) {
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
  }
}

function decoded(frame) {
  state.out += 1;
  const captured = (frame.timestamp || 0) / 1000;
  const now = performance.now();
  const wait = state.pacer.hold(captured, now);
  state.waiting.push({ frame, due: now + (wait > 0 ? wait : 0) });
  if (!state.timer) pump();
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
}

self.onrtctransform = (event) => {
  const reader = event.transformer.readable.getReader();
  const pull = () => reader.read().then(({ done, value }) => {
    if (done) return;
    state.handed += 1;
    if (state.handed === 1) say("the first encoded frame arrived here");
    const data = value.data;
    // Copied because the frame is recycled the moment this returns.
    const bytes = new Uint8Array(data.byteLength);
    bytes.set(new Uint8Array(data));
    take(value.type || "delta", value.timestamp, bytes.buffer);
    pull();
  }).catch(() => { /* the connection went away */ });
  pull();
};

self.onmessage = (event) => {
  const m = event.data || {};
  if (m.start) {
    state.canvas = m.start.canvas;
    state.context = state.canvas.getContext("2d", { alpha: false,
                                                    desynchronized: true });
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
  if (m.report) {
    self.postMessage({
      stats: {
        handed: state.handed, fed: state.fed, out: state.out,
        drawn: state.drawn, refused: state.refused,
        skipped: state.skipped, stale: state.stale,
        reserve: Math.round(state.pacer ? state.pacer.reserve() : 0),
        ever: state.ever,
      },
    });
    state.handed = state.fed = state.out = state.drawn = 0;
    state.refused = state.skipped = state.stale = 0;
    return;
  }
  if (m.stop) { close(); }
};

// Last, so nothing can be sent before the handlers above exist.
self.postMessage({ ready: true });
