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
  saidDraw: false,
  saidFirst: false,
  drawFails: 0,
  drew: false,
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
    if (!state.context) throw new Error("no context");
    state.context.drawImage(frame, 0, 0);
    state.drawn += 1;
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

function pump() {
  state.timer = 0;
  state.drew = false;
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

function decoded(frame) {
  state.out += 1;
  state.drew = state.drew || false;
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

/* Putting a frame back together.
 *
 * SCTP will not carry an arbitrarily large message and a keyframe is easily
 * larger than a browser's limit, so the host sends each frame in pieces. Nine
 * bytes in front of each say whether it starts a frame, whether it ends one,
 * whether the frame is a keyframe, and when it was captured. Nothing else is
 * needed: the pieces of one frame arrive in order and no frame is begun
 * before the one before it has ended, because the channel is ordered.
 */
const FIRST = 2, LAST = 4;
let building = null;

function chunk(buffer) {
  const view = new DataView(buffer);
  const flags = view.getUint8(0);
  const stamp = Number(view.getBigUint64(1, true));
  const body = new Uint8Array(buffer, 9);
  if (flags & FIRST) {
    building = { key: (flags & 1) !== 0, stamp, parts: [], size: 0 };
  }
  if (!building) return;                 // a tail with no head: wait for one
  building.parts.push(body);
  building.size += body.length;
  if (!(flags & LAST)) return;
  const whole = new Uint8Array(building.size);
  let at = 0;
  for (const part of building.parts) { whole.set(part, at); at += part.length; }
  const made = building;
  building = null;
  state.handed += 1;
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
    try {
      state.context = state.canvas.getContext("2d", { alpha: false });
    } catch (err) {
      state.context = null;
    }
    if (!state.context) {
      self.postMessage({ failed: "this browser gave the worker no way to "
                                 + "draw on the canvas" });
      return;
    }
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
      },
    });
    state.handed = state.fed = state.out = state.drawn = 0;
    state.refused = state.skipped = state.stale = 0;
    state.drawFails = 0;
    return;
  }
  if (m.chunk) { chunk(m.chunk); return; }
  if (m.stop) { close(); building = null; }
};

// Last, so nothing can be sent before the handlers above exist.
self.postMessage({ ready: true });
