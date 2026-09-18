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
 * configure, and the profile is not guessable: it is in the fmtp line the
 * host and the browser agreed on. H.264's profile-level-id is already the
 * three bytes avc1 wants, in the same order, which is the one convenient
 * accident in this area.
 *
 * H.265 is not attempted. Its codec string is assembled from half a dozen
 * fields in a different order and getting it wrong is a decoder that will not
 * start, which here means a black picture rather than a message. The <video>
 * element handles H.265 perfectly well and that is where it stays. */
function codecFrom(mime, fmtp) {
  const kind = String(mime || "").toLowerCase();
  if (kind.indexOf("h264") < 0) return "";
  const found = /profile-level-id=([0-9a-fA-F]{6})/.exec(String(fmtp || ""));
  // Constrained baseline at level 3.1 is what a browser offers when it says
  // nothing, and a stream with no fmtp at all is that by convention.
  return "avc1." + (found ? found[1].toUpperCase() : "42E01F");
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
  let timer = 0, running = false, drawn = 0, late = 0, held = 0;
  const context = canvas.getContext("2d", { alpha: false,
                                            desynchronized: true });

  function draw(frame) {
    if (canvas.width !== frame.displayWidth
        || canvas.height !== frame.displayHeight) {
      canvas.width = frame.displayWidth;
      canvas.height = frame.displayHeight;
    }
    try { context.drawImage(frame, 0, 0); } catch (_) { /* gone */ }
    frame.close();
    drawn += 1;
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
      worker.onmessage = (event) => {
        const m = event.data;
        if (!decoder || decoder.state !== "configured") return;
        try {
          decoder.decode(new EncodedVideoChunk({
            type: m.type === "key" ? "key" : "delta",
            timestamp: m.timestamp,
            data: m.bytes,
          }));
        } catch (_) { late += 1; }
      };
      try {
        if (typeof RTCRtpScriptTransform !== "undefined") {
          receiver.transform = new RTCRtpScriptTransform(worker, {});
        } else {
          const streams = receiver.createEncodedStreams();
          // The older shape: no worker involved, so the frames are read here
          // and handed to the same decoder.
          const reader = streams.readable.getReader();
          const pull = () => reader.read().then(({ done, value }) => {
            if (done || !decoder || decoder.state !== "configured") return;
            try {
              decoder.decode(new EncodedVideoChunk({
                type: value.type === "key" ? "key" : "delta",
                timestamp: value.timestamp,
                data: value.data,
              }));
            } catch (_) { late += 1; }
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
    },
    running() { return running; },
    /* Counted rather than guessed at, in the same spirit as everything else
       here: if this is not better, the numbers should say so. */
    report() {
      const out = { drawn, undecodable: late, reserve: Math.round(pacer.reserve()) };
      drawn = 0; late = 0; held = 0;
      return out;
    },
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { makePacer, codecFrom, PACE };
}
