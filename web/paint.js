/* Drawing the picture ourselves, instead of handing it to a <video> element.
 *
 * Why this exists. Everything this host sends was measured and found correct:
 * 60.0 frames a second out, 60.0 arriving, 60.0 decoded, none thrown away, on
 * a timeline with none of six hundred uneven -- and the picture still was not
 * smooth, while a native client streaming the same game from the same machine
 * was. The remaining difference is what happens after the frames arrive.
 *
 * A <video> element fed by WebRTC does not let a page decide when a frame is
 * drawn. The browser's own jitter buffer re-times playout continuously, and
 * every adjustment it makes skips or repeats a frame. That machinery is right
 * for a video call and wrong for a game, and there is no property that turns
 * it off.
 *
 * So: take the encoded frames before they reach it, decode them with
 * WebCodecs, and draw them on a canvas on a schedule of our own. This is the
 * design moonlight-web uses, and the pacing below is theirs in spirit -- hold
 * a frame against a small reserve sized from the late tail of recent arrivals,
 * and release it immediately if it is already late.
 *
 * Strictly optional and strictly per-viewer. It is a switch in the panel, it
 * falls back to the <video> element on any browser that cannot do it, and it
 * asks nothing of the host.
 */

/* How a frame is held. Times are milliseconds throughout.
 *
 * The idea, and it is not obvious: a frame that took the *fastest* path
 * through the network arrived early relative to the ones around it, and
 * drawing it the moment it lands is what makes motion jerk. So the fast ones
 * wait and the slow ones do not, which flattens arrival jitter into an even
 * cadence at the cost of a small fixed delay.
 *
 * The reserve is sized from the recent late tail rather than the average,
 * because the average is not what hurts -- one frame in twenty arriving 20ms
 * late is invisible in a mean and plainly visible on a screen. It is capped,
 * because a reserve is latency and this is a game.
 */
const PACE = {
  MAX_MS: 25,        // about a frame and a half at 60fps
  SLACK_MS: 2,       // present now rather than arm a timer for less than this
  QUANTILE: 0.95,    // cover the late tail, not the mean
  WINDOW: 120,       // arrivals remembered, about two seconds at 60fps
};

function makePacer(limits) {
  const c = limits || PACE;
  const seen = [];
  let base = null;                       // the fastest transit seen lately

  return {
    /* Given when a frame was captured and when it arrived, how long to hold
       it. Returns 0 to draw it now. */
    hold(captureMs, nowMs) {
      const transit = nowMs - captureMs;
      if (base === null || transit < base) base = transit;
      const excess = transit - base;
      seen.push(excess);
      if (seen.length > c.WINDOW) seen.shift();
      // Not enough to have an opinion yet: drawing immediately is the old
      // behaviour and the right default before anything is known.
      if (seen.length < 10) return 0;
      const sorted = seen.slice().sort((a, b) => a - b);
      const at = Math.min(sorted.length - 1,
                          Math.floor(sorted.length * c.QUANTILE));
      const reserve = Math.min(c.MAX_MS, sorted[at]);
      const wait = reserve - excess;
      return wait > c.SLACK_MS ? wait : 0;
    },
    /* The fastest path can only improve as the link settles, and a baseline
       that only ever falls would make every later frame look late for ever.
       Forgetting it periodically is what keeps the reserve honest. */
    forget() { base = null; seen.length = 0; },
    reserve() {
      if (seen.length < 10) return 0;
      const sorted = seen.slice().sort((a, b) => a - b);
      const at = Math.min(sorted.length - 1,
                          Math.floor(sorted.length * c.QUANTILE));
      return Math.min(c.MAX_MS, sorted[at]);
    },
  };
}

/* What to tell WebCodecs this stream is.
 *
 * The codec string has to match the stream or the decoder refuses to
 * configure, and none of it is guessable: the profile and level are in the
 * fmtp line the host and the browser agreed on, and the *spelling* of them
 * differs per codec in ways that are easy to get subtly wrong.
 *
 * H.264 is the one convenient accident in this area: profile-level-id is
 * already the three bytes avc1 wants, in the same order.
 *
 * H.265 is not. Its string is assembled from profile space, profile, a
 * compatibility mask, a tier letter, a level and a constraint byte, in an
 * order that is not the order they appear in the fmtp, and the mask is a
 * bit-reversal of a field nobody writes down. It was left out for that
 * reason, and leaving it out was the wrong call: a wrong string is a decoder
 * that will not configure, which is *reported* rather than silent, and
 * asking the browser is free.
 *
 * So instead of constructing one string and hoping, this offers a short
 * ordered list of plausible spellings and asks the browser which it will
 * take. Nothing is attempted that the browser has not already agreed to.
 * AV1 is here for the same reason, ready for a host that sends it. */
function codecCandidates(mime, fmtp) {
  const kind = String(mime || "").toLowerCase();
  const line = String(fmtp || "");
  const field = (name, fallback) => {
    const found = new RegExp(name + "=([0-9a-fA-F]+)").exec(line);
    return found ? found[1] : fallback;
  };

  if (kind.indexOf("h264") >= 0) {
    const id = field("profile-level-id", "42E01F").toUpperCase();
    // The negotiated one first, then constrained baseline at 3.1 -- what a
    // browser means when it says nothing.
    return id === "42E01F" ? ["avc1.42E01F"] : ["avc1." + id, "avc1.42E01F"];
  }

  if (kind.indexOf("h265") >= 0 || kind.indexOf("hevc") >= 0) {
    const profile = parseInt(field("profile-id", "1"), 10) || 1;
    const tier = (parseInt(field("tier-flag", "0"), 10) || 0) ? "H" : "L";
    const level = parseInt(field("level-id", "93"), 10) || 93;
    // The compatibility mask is a bit-reversed field and in practice only two
    // values are ever seen: 6 for Main, 4 for Main 10. Both are offered
    // rather than reasoned about, and both spellings of the container with
    // them -- hvc1 is what Safari documents, hev1 is what some builds take.
    const masks = profile === 2 ? ["4", "6"] : ["6", "4"];
    const out = [];
    for (const box of ["hvc1", "hev1"]) {
      for (const mask of masks) {
        out.push(box + "." + profile + "." + mask + "." + tier + level + ".B0");
      }
    }
    return out;
  }

  if (kind.indexOf("av1") >= 0) {
    // av01.<profile>.<level><tier>.<depth>. The fmtp carries profile and
    // level-idx as decimals; the level is two digits and the tier is M or H.
    const profile = parseInt(field("profile", "0"), 10) || 0;
    const level = parseInt(field("level-idx", "8"), 10) || 8;
    const tier = (parseInt(field("tier", "0"), 10) || 0) ? "H" : "M";
    const two = (level < 10 ? "0" : "") + level;
    return ["av01." + profile + "." + two + tier + ".08",
            "av01.0.08M.08"];
  }

  return [];
}

/* The first spelling this browser will actually accept, or "".
 *
 * isConfigSupported is the whole point: it turns a list of guesses into one
 * answer from the only authority there is, before a decoder is built and
 * before anything can go black. */
async function pickCodec(mime, fmtp) {
  if (typeof VideoDecoder === "undefined"
      || typeof VideoDecoder.isConfigSupported !== "function") {
    // No way to ask. H.264 is the one that can be spelled from the fmtp
    // without inference, so it is the only one attempted blind.
    const blind = codecCandidates(mime, fmtp);
    return (blind.length && blind[0].indexOf("avc1.") === 0) ? blind[0] : "";
  }
  for (const codec of codecCandidates(mime, fmtp)) {
    try {
      const answer = await VideoDecoder.isConfigSupported({
        codec, optimizeForLatency: true,
      });
      if (answer && answer.supported) return codec;
    } catch (_) { /* not this one */ }
  }
  return "";
}

/* Encoded H.264 and H.265 come in two shapes and only one of them is what a
 * decoder configured without a `description` expects.
 *
 * Annex B separates NAL units with start codes -- 00 00 01, or 00 00 00 01.
 * The other shape prefixes each unit with its length, which is what an mp4
 * carries and what a decoder wants a `description` for. WebRTC is supposed to
 * hand out the first; not every browser does, and the symptom when it does
 * not is one frame fed and "Decoder failure" with nothing else to go on.
 *
 * So rather than believing either end, this looks. A start code is passed
 * through untouched. Otherwise the lengths are walked, and they either add up
 * exactly -- which is proof, not a guess -- or the bytes go through unchanged
 * for the decoder to reject with its own opinion.
 */
function looksAnnexB(view) {
  if (view.length < 4) return false;
  if (view[0] === 0 && view[1] === 0 && view[2] === 1) return true;
  return view[0] === 0 && view[1] === 0 && view[2] === 0 && view[3] === 1;
}

function toAnnexB(buffer) {
  const view = new Uint8Array(buffer);
  if (looksAnnexB(view)) return { data: buffer, shape: "annex-b" };
  // Walk it as 4-byte lengths first. Anything that does not land exactly on
  // the end is not this format.
  let at = 0, count = 0;
  while (at + 4 <= view.length) {
    const size = (view[at] << 24 | view[at + 1] << 16
                  | view[at + 2] << 8 | view[at + 3]) >>> 0;
    if (size === 0 || at + 4 + size > view.length) { count = -1; break; }
    at += 4 + size;
    count += 1;
  }
  if (count < 1 || at !== view.length) {
    return { data: buffer, shape: "unknown" };
  }
  // Same length: a four-byte length becomes a four-byte start code.
  const out = new Uint8Array(view.length);
  at = 0;
  while (at + 4 <= view.length) {
    const size = (view[at] << 24 | view[at + 1] << 16
                  | view[at + 2] << 8 | view[at + 3]) >>> 0;
    out[at] = 0; out[at + 1] = 0; out[at + 2] = 0; out[at + 3] = 1;
    out.set(view.subarray(at + 4, at + 4 + size), at + 4);
    at += 4 + size;
  }
  return { data: out.buffer, shape: "length-prefixed" };
}

function canPaintDirectly() {
  return typeof VideoDecoder !== "undefined"
    && typeof VideoFrame !== "undefined"
    && (typeof RTCRtpScriptTransform !== "undefined"
        || (typeof RTCRtpReceiver !== "undefined"
            && RTCRtpReceiver.prototype
            && "createEncodedStreams" in RTCRtpReceiver.prototype));
}

/* Everything with a lifetime: the worker, the decoder, the queue and the
   timers. One at a time, and stop() has to leave nothing running -- a decoder
   left open holds a hardware decode session, and a page that renegotiates
   four times would run out of them. */
function makePainter(canvas, say) {
  let worker = null, decoder = null, pacer = makePacer(PACE);
  let waiting = [];                      // decoded frames not yet drawn
  let timer = 0, running = false;
  // Four counters, because four different things go wrong and they look
  // identical from a chair: nothing arriving, nothing decoding, nothing
  // drawing, or nothing visible. The first attempt at this was a black
  // screen with a cheerful "drawing the picture here" in the log and no way
  // to tell which of the four it was.
  let fed = 0, out = 0, drawn = 0, refused = 0, skipped = 0;
  let started = false;                   // a keyframe has been seen
  let shape = "";                        // what the bitstream turned out to be
  const context = canvas.getContext("2d", { alpha: false,
                                            desynchronized: true });

  function draw(frame) {
    if (canvas.width !== frame.displayWidth
        || canvas.height !== frame.displayHeight) {
      canvas.width = frame.displayWidth;
      canvas.height = frame.displayHeight;
    }
    try {
      context.drawImage(frame, 0, 0);
      drawn += 1;
    } catch (_) { /* the canvas went away with the page */ }
    frame.close();
  }

  function pump() {
    timer = 0;
    while (waiting.length) {
      const next = waiting[0];
      const wait = next.due - performance.now();
      if (wait > PACE.SLACK_MS) {
        timer = setTimeout(pump, wait);
        return;
      }
      waiting.shift();
      draw(next.frame);
    }
  }

  function decoded(frame) {
    out += 1;
    // The capture moment, in milliseconds. A VideoFrame's timestamp comes
    // from the RTP timestamp, which is the host's own capture clock at 90kHz
    // -- so the difference between two of them is real elapsed time at the
    // host even though the two machines' clocks are not synchronised. A
    // difference is all the pacing needs.
    const captured = (frame.timestamp || 0) / 1000;
    const now = performance.now();
    const wait = pacer.hold(captured, now);
    if (wait <= 0) {
      held += 1;
      if (waiting.length) { waiting.push({ frame, due: now }); pump(); }
      else draw(frame);
      return;
    }
    waiting.push({ frame, due: now + wait });
    if (!timer) pump();
  }

  return {
    /* One encoded frame, from whichever of the two routes brought it.
     *
     * Everything before the first keyframe is thrown away rather than fed.
     * A decoder handed a delta frame with nothing to apply it to raises on
     * the spot, and the exception was being swallowed -- so the first second
     * of every connection was a stream of errors nobody could see, and
     * whether the decoder ever recovered was luck. Waiting is correct and it
     * is also what makes the counters mean something. */
    take(type, timestamp, data) {
      if (!decoder || decoder.state !== "configured") return;
      const key = type === "key";
      if (!started) {
        if (!key) { skipped += 1; return; }
        started = true;
      }
      let bytes = data;
      try {
        const shaped = toAnnexB(data instanceof ArrayBuffer ? data
                                : data.buffer || data);
        bytes = shaped.data;
        if (shape === "") {
          shape = shaped.shape;
          say("the encoded frames are " + shape);
        }
      } catch (_) { /* pass it through as it came */ }
      try {
        decoder.decode(new EncodedVideoChunk({
          type: key ? "key" : "delta",
          timestamp: timestamp,
          data: bytes,
        }));
        fed += 1;
      } catch (err) {
        refused += 1;
        // A decoder that has given up stays given up, and there is no point
        // feeding it for the rest of the session.
        if (decoder.state !== "configured") {
          say("the decoder stopped accepting frames: "
              + (err && err.message ? err.message : "no reason given"));
          this.stop();
        }
      }
    },

    start(receiver, codec) {
      if (running) return false;
      try {
        decoder = new VideoDecoder({
          output: decoded,
          error: (err) => {
            say("the direct decoder stopped: " + (err && err.message));
            this.stop();
          },
        });
        // No description, which means Annex B -- the format WebRTC hands out
        // for H.264. A description here would mean AVCC and every frame would
        // be rejected as malformed.
        decoder.configure({ codec, optimizeForLatency: true });
      } catch (err) {
        say("this browser would not start a decoder for " + codec);
        return false;
      }
      worker = new Worker("/static/frames.js");
      const mine = worker;
      worker.onmessage = (event) => {
        const m = event.data;
        // The worker says when its handler is in place. Attaching the
        // transform before that is a race whose losing side is silence: the
        // event fires, nothing is listening, and no frame ever arrives.
        if (m && m.ready) {
          try {
            if (typeof RTCRtpScriptTransform !== "undefined") {
              receiver.transform = new RTCRtpScriptTransform(mine, {});
            }
          } catch (err) {
            say("this browser would not take the transform: "
                + (err && err.message ? err.message : "no reason given"));
            this.stop();
          }
          return;
        }
        this.take(m.type, m.timestamp, m.bytes);
      };
      try {
        if (typeof RTCRtpScriptTransform === "undefined") {
          const streams = receiver.createEncodedStreams();
          // The older shape: no worker involved, so the frames are read here
          // and handed to the same decoder.
          const reader = streams.readable.getReader();
          const pull = () => reader.read().then(({ done, value }) => {
            if (done) return;
            this.take(value.type, value.timestamp, value.data);
            pull();
          }).catch(() => {});
          pull();
        }
      } catch (err) {
        say("this browser would not hand over the encoded frames");
        this.stop();
        return false;
      }
      running = true;
      return true;
    },
    stop() {
      running = false;
      if (timer) { clearTimeout(timer); timer = 0; }
      for (const one of waiting) { try { one.frame.close(); } catch (_) {} }
      waiting = [];
      if (decoder) {
        try { if (decoder.state !== "closed") decoder.close(); } catch (_) {}
        decoder = null;
      }
      if (worker) { try { worker.terminate(); } catch (_) {} worker = null; }
      pacer.forget();
      started = false;
      shape = "";
    },
    running() { return running; },
    /* Counted rather than guessed at, in the same spirit as everything else
       here: if this is not better, the numbers should say so. */
    /* Said out loud rather than kept, because a black screen with no numbers
       beside it is exactly what this cost the first time. */
    report() {
      const said = ("drawing here: " + fed + " fed to the decoder, " + out
                    + " came out, " + drawn + " painted, " + refused
                    + " refused, " + skipped + " before the first keyframe, "
                    + Math.round(pacer.reserve()) + "ms reserve");
      fed = 0; out = 0; drawn = 0; refused = 0; skipped = 0;
      return said;
    },
    /* The canvas as it stands, for whoever wants to know whether anything has
       ever been painted on it at all. */
    painted() { return canvas.width > 16 && canvas.height > 16; },
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { makePacer, codecCandidates, pickCodec,
                     toAnnexB, looksAnnexB, PACE };
}
