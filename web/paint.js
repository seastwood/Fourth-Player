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
  EASE: 0.05,        // how fast the playout clock follows the target
};

function makePacer(limits) {
  const c = limits || PACE;
  // Recent transits -- when a frame arrived, against when it was captured.
  // The two clocks have unrelated origins and that is fine: only the
  // differences are ever used.
  const seen = [];
  // The one number that decides smoothness: how far behind the capture clock
  // the picture is shown. Presentation time is capture time plus this, so an
  // evenly captured stream is an evenly shown one however unevenly it
  // arrived -- which is the whole job, and is not what asking "how long
  // should I hold this particular frame" achieves. That produced a schedule
  // as ragged as the arrivals it was meant to smooth.
  let offset = null;

  function target() {
    const sorted = seen.slice().sort((a, b) => a - b);
    const at = Math.min(sorted.length - 1,
                        Math.floor(sorted.length * c.QUANTILE));
    // The fastest path, plus enough to cover the late tail: a frame that
    // takes the quickest route waits, one that took the slowest does not.
    return sorted[0] + Math.min(c.MAX_MS, sorted[at] - sorted[0]);
  }

  return {
    /* When this frame should be shown, on the same clock `nowMs` is on. */
    due(captureMs, nowMs) {
      const transit = nowMs - captureMs;
      seen.push(transit);
      if (seen.length > c.WINDOW) seen.shift();
      if (offset === null) offset = transit;
      if (seen.length >= 10) {
        // Eased rather than snapped. A playout clock that jumps is a stutter
        // of its own, and nothing here is urgent: the target moves slowly
        // because it is a quantile over a couple of seconds of arrivals.
        offset += (target() - offset) * c.EASE;
      }
      return captureMs + offset;
    },
    forget() { seen.length = 0; offset = null; },
    reserve() {
      if (seen.length < 10) return 0;
      const sorted = seen.slice().sort((a, b) => a - b);
      return Math.round(offset - sorted[0]);
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
    const out = ["avc1." + id];
    // The same profile at a much higher level, second.
    //
    // This is the fix for a decoder that accepts a keyframe and then reports
    // "Decoder failure": the level in a codec string is a *ceiling*, and a
    // decoder told 3.1 and handed 1440p60 is being handed something outside
    // what it agreed to. A decoder configured above the stream decodes it
    // perfectly well, so asking for 5.2 costs nothing and covers every size
    // this host can send. 0x34 is level 5.2.
    const high = id.slice(0, 4) + "34";
    if (high !== id) out.push("avc1." + high);
    if (out.indexOf("avc1.42E01F") < 0) out.push("avc1.42E01F");
    return out;
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

/* The other way of handing H.264 to a decoder, for the browser that will not
 * take the first.
 *
 * iOS Safari configures a decoder for Annex B, accepts one frame and reports
 * "Decoder failure" -- measured against all three spellings of the codec, so
 * it is not the profile or the level. WebKit wants what an mp4 carries
 * instead: the parameter sets handed over once, up front, as a `description`,
 * and every frame length-prefixed rather than separated by start codes.
 *
 * Both of those are derivable from the stream itself. The parameter sets
 * arrive in front of every keyframe (the host sets config-interval=-1 for
 * exactly this reason), so the first keyframe carries everything needed to
 * build the description, and the conversion of the frames is the inverse of
 * toAnnexB.
 */
function splitAnnexB(view) {
  const units = [];
  let at = 0;
  // Skip to the first start code.
  while (at + 3 <= view.length) {
    if (view[at] === 0 && view[at + 1] === 0
        && (view[at + 2] === 1
            || (view[at + 2] === 0 && view[at + 3] === 1))) break;
    at += 1;
  }
  while (at + 3 <= view.length) {
    const wide = view[at + 2] === 0;
    const from = at + (wide ? 4 : 3);
    let next = from;
    while (next + 3 <= view.length) {
      if (view[next] === 0 && view[next + 1] === 0
          && (view[next + 2] === 1
              || (view[next + 2] === 0 && view[next + 3] === 1))) break;
      next += 1;
    }
    const end = (next + 3 <= view.length) ? next : view.length;
    if (end > from) units.push(view.subarray(from, end));
    if (end >= view.length) break;
    at = end;
  }
  return units;
}

/* The avcC box a decoder wants as its `description`, or null if this frame
   does not carry the parameter sets. */
function avcDescription(bytes) {
  const units = splitAnnexB(new Uint8Array(bytes));
  const sps = [], pps = [];
  for (const unit of units) {
    const kind = unit[0] & 0x1f;
    if (kind === 7) sps.push(unit);
    else if (kind === 8) pps.push(unit);
  }
  if (!sps.length || !pps.length || sps[0].length < 4) return null;
  let size = 7;
  for (const one of sps) size += 2 + one.length;
  for (const one of pps) size += 2 + one.length;
  const out = new Uint8Array(size);
  let at = 0;
  out[at++] = 1;                       // configurationVersion
  out[at++] = sps[0][1];               // profile
  out[at++] = sps[0][2];               // profile compatibility
  out[at++] = sps[0][3];               // level
  out[at++] = 0xff;                    // reserved + four-byte lengths
  out[at++] = 0xe0 | (sps.length & 0x1f);
  for (const one of sps) {
    out[at++] = (one.length >> 8) & 0xff;
    out[at++] = one.length & 0xff;
    out.set(one, at); at += one.length;
  }
  out[at++] = pps.length & 0xff;
  for (const one of pps) {
    out[at++] = (one.length >> 8) & 0xff;
    out[at++] = one.length & 0xff;
    out.set(one, at); at += one.length;
  }
  return out;
}

/* Start codes to four-byte lengths: the inverse of toAnnexB, and what a
   decoder configured with a description expects every frame to look like. */
function toLengthPrefixed(bytes) {
  const units = splitAnnexB(new Uint8Array(bytes));
  if (!units.length) return bytes;
  let size = 0;
  for (const one of units) size += 4 + one.length;
  const out = new Uint8Array(size);
  let at = 0;
  for (const one of units) {
    out[at++] = (one.length >>> 24) & 0xff;
    out[at++] = (one.length >>> 16) & 0xff;
    out[at++] = (one.length >>> 8) & 0xff;
    out[at++] = one.length & 0xff;
    out.set(one, at); at += one.length;
  }
  return out.buffer;
}

/* Whether this browser can decode and draw the picture itself.
 *
 * No mention of encoded transforms any more. Taking the frames off the media
 * track needed one, and a transform turned out to want a receiver that has
 * not started yet, to deliver nothing when attached to one that has, and to
 * leave the receiver delivering nothing for ever once removed. The client
 * this is modelled on does not use it either: it carries whole frames on a
 * data channel. So does this now, and all a browser needs is a decoder and a
 * canvas it can hand to a worker. */
function canPaintDirectly() {
  return typeof VideoDecoder !== "undefined"
    && typeof VideoFrame !== "undefined"
    && typeof OffscreenCanvas !== "undefined"
    && typeof HTMLCanvasElement !== "undefined"
    && Boolean(HTMLCanvasElement.prototype.transferControlToOffscreen);
}

/* The page's half of it: start the worker, give it the canvas, and relay.
 *
 * Almost nothing happens here on purpose. The decoder, the pacing and the
 * painting are all in the worker now -- see frames.js for why -- so this
 * chooses the codec, hands over a canvas the worker can draw on, and carries
 * what the worker says back to whoever is listening. A frame is never
 * touched on this thread.
 */
function makePainter(canvas, say) {
  let worker = null, running = false, mine = canvas;
  let last = null;                       // the worker's last set of counters
  let ever = false;                      // it has painted at least once
  let carrying = null, letting = null;   // the channel, and how to stop reading
  let onGone = null;

  /* A canvas can only be handed to a worker once, so each attempt gets a
     fresh one. The element keeps its id, its classes and its place, because
     the page positions it by all three. */
  function freshCanvas() {
    const old = document.getElementById("painted");
    if (!old || !old.parentNode) return old;
    const made = document.createElement("canvas");
    made.id = old.id;
    made.className = old.className;
    made.hidden = old.hidden;
    made.style.cssText = old.style.cssText;
    old.parentNode.replaceChild(made, old);
    return made;
  }

  return {
    start(channel, codec) {
      if (running) return false;
      if (!channel || channel.readyState !== "open") {
        say("the picture channel is not open yet");
        return false;
      }
      if (!canPaintDirectly()) {
        say("this browser cannot hand a canvas to a worker");
        return false;
      }
      mine = freshCanvas();
      let surface = null;
      try {
        surface = mine.transferControlToOffscreen();
      } catch (err) {
        say("this browser would not hand over the canvas: "
            + ((err && err.message) || "no reason given"));
        return false;
      }
      worker = new Worker("/static/frames.js");
      const it = worker;
      worker.onerror = (err) => {
        say("the frame worker would not load: "
            + ((err && err.message) || "no reason given"));
        this.stop();
        if (onGone) onGone();
      };
      worker.onmessage = (event) => {
        const m = event.data || {};
        if (m.ready) {
          it.postMessage({ start: { canvas: surface, codec } }, [surface]);
          return;
        }
        if (m.started) {
          // Only now: frames arriving before there is a decoder are frames
          // thrown away, and the host holds them back until it is asked.
          try { channel.send("on"); } catch (_) {}
          say("the picture channel was asked for whole frames");
          return;
        }
        if (m.painted) { ever = true; return; }
        if (m.note) { say(m.note); return; }
        if (m.stats) { last = m.stats; return; }
        if (m.failed) {
          say(m.failed);
          this.stop();
          if (onGone) onGone();
        }
      };
      channel.binaryType = "arraybuffer";
      carrying = channel;
      const onPictureChunk = (event) => {
        const data = event.data;
        if (!worker || !(data instanceof ArrayBuffer)) return;
        // Transferred rather than copied: it is this page's last contact with
        // the bytes, and the worker is the only thing that reads them.
        worker.postMessage({ chunk: data }, [data]);
      };
      // An abort signal rather than keeping the function to hand back later:
      // one thing to forget instead of two that have to match.
      letting = new AbortController();
      channel.addEventListener("message", onPictureChunk,
                               { signal: letting.signal });
      running = true;
      return true;
    },

    /* Another spelling of the codec, without disturbing the channel. */
    useCodec(codec) {
      if (!worker || !codec) return false;
      worker.postMessage({ codec });
      return true;
    },

    whenGone(fn) { onGone = fn; },

    stop() {
      running = false;
      last = null;
      ever = false;
      if (carrying) {
        // Tell the host to stop sending them, then stop listening. In that
        // order: the other way round leaves frames arriving at nothing for as
        // long as the message takes to get there.
        try { carrying.send("off"); } catch (_) {}
        if (letting) { try { letting.abort(); } catch (_) {} }
        carrying = null;
        letting = null;
      }
      if (worker) {
        try { worker.postMessage({ stop: true }); } catch (_) {}
        try { worker.terminate(); } catch (_) {}
        worker = null;
      }
      // A canvas that was handed to a worker cannot be drawn on here again,
      // so it is replaced with a plain one ready for the next attempt.
      try { freshCanvas(); } catch (_) {}
    },

    running() { return running; },

    /* Asked of the worker, answered from its previous reply. One window of
       lag, and the alternative is making every caller wait on a message. */
    report() {
      if (worker) { try { worker.postMessage({ report: true }); } catch (_) {} }
      if (!last) return "drawing here: nothing said yet";
      return ("drawing here: " + last.handed + " handed over by the transform, "
              + last.fed + " fed to the decoder, " + last.out + " came out, "
              + last.drawn + " painted, " + last.refused + " refused, "
              + last.skipped + " before the first keyframe, " + last.stale
              + " too late to matter, " + last.reserve + "ms reserve, canvas "
              + (last.size || "?")
              + (last.drawFails ? ", " + last.drawFails + " paints refused" : "")
              + (last.shown
                 ? "; painted every " + last.shown.typical + "ms typical, "
                   + "worst " + last.shown.worst + "ms, " + last.shown.off
                   + " of " + last.shown.of + " off the beat"
                 : ""));
    },

    painted() { return ever || Boolean(last && last.ever); },
    /* Frames are arriving and none has been a keyframe yet, so there is
       nothing to decode against and nothing is wrong. */
    waitingForKey() {
      return Boolean(last && last.handed > 0 && !last.keyed);
    },
    drawnLately() { return last ? last.drawn : 0; },
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { makePacer, codecCandidates, pickCodec,
                     toAnnexB, looksAnnexB, splitAnnexB,
                     avcDescription, toLengthPrefixed,
                     makePainter, PACE };
}
