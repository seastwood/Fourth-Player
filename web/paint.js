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
/* The presentation clock.
 *
 * This is moonlight-web's FramePacer, adopted after measuring what was here
 * before and reading theirs. Their header states the problem better than any
 * number I took: presenting a frame the moment it decodes is optimal on a
 * metronomic link and the *worst* case under jitter -- a frame 10ms late
 * leaves the one before it up for an extra refresh, then the two that arrive
 * together collapse into one, and that repeat-then-skip pair is the judder.
 *
 * What was here instead was an interval accumulator: `nextAt += gap`, with
 * gap taken from the capture deltas and rounded to whole refreshes, nudged by
 * the queue depth. It is open-loop, and it drifts -- the host's sixty a
 * second and the screen's sixty a second are not the same sixty -- so the
 * schedule slid against the arrivals until frames were late on arrival, hit
 * `if (nextAt < now) nextAt = now`, and the queue drained in a burst.
 * Measured on a game: 51 of 350 refreshes with nothing to paint and 22 frames
 * thrown away for being too late, on a stream the host was sending perfectly.
 *
 * The objection written against an absolute schedule -- that the two clocks
 * have unrelated origins and unrelated rates, and the capture clock restarts
 * whenever the pipeline does -- is real, and is answered here rather than
 * avoided. Only the *difference* between the clocks is ever used, so the
 * origins cancel. DRIFT lets the baseline follow a rate difference at about
 * three milliseconds a second, so drift is tracked rather than accumulated.
 * RESYNC_MS rebases outright on a discontinuity, which is what a pipeline
 * restart or a throttled tab looks like from here.
 *
 * The control law, which is theirs:
 *   baseline  the best transit seen lately. Down instantly -- a shorter path
 *             is a fact -- and up only by DRIFT, so a sustained bad patch is
 *             not quietly absorbed into the baseline and hidden.
 *   excess    how much later than the baseline this frame actually landed.
 *   target    p95 of the excess, plus a small margin, clamped. Up at once,
 *             because the judder has already happened; down slowly, because
 *             one calm second is not evidence.
 * A frame is then held for `target - excess`: one that took the best path
 * waits the whole reserve, one that has already spent it goes straight out.
 * The latency added is the reserve, and only while the link is really
 * jittery -- DEADBAND snaps a negligible target to exactly zero so a clean
 * link is the immediate path again.
 *
 * Clock-injected on purpose: `now` is always an argument and nothing here
 * reads performance.now(), so the whole law is deterministic in a test. The
 * version this replaces could only be tested through a running decoder.
 */
const PACE = {
  MIN: 0,              // a clean link adds nothing
  MAX: 25,             // about a frame and a half at 60fps; see MAX_FOR
  QUANTILE: 0.95,      // cover the late tail, not the mean
  SAFETY: 1.15,        // a small margin over the measured tail
  WINDOW_MS: 2000,     // the sliding window behind the tail estimate
  MAX_SAMPLES: 256,    // and a hard bound on it
  CONTROL_MS: 100,     // how often the target may be re-evaluated
  DECAY_MS: 250,       // and how often it may step down
  DECAY_STEP: 2,       // by this much -- 8ms a second, slow on purpose
  BUMP: 8,             // step up when a frame blew through the whole reserve
  DEADBAND: 3,         // below this the target snaps to MIN
  DRIFT: 0.05,         // how fast the baseline may rise, per frame
  RESYNC_MS: 500,      // past this it is a discontinuity, not jitter
  SLACK_MS: 2,         // present now rather than wait for less than this
};

/* The cap, from the Smoothing setting. Every frame of reserve is a frame of
   delay and a hiccup absorbed, which is the one real trade here, so it stays
   the guest's to make. Three -- the default -- is moonlight-web's 25ms. */
function maxFor(frames) {
  // Never below twenty milliseconds, whatever the setting says.
  //
  // One frame of smoothing mapped to 8ms, and a link with a late tail of 18
  // to 26ms was then capped at 8 -- so the pacer could not cover what it had
  // measured, every frame past the cap was an underrun, and the setting
  // meant for "least delay" produced the most judder. A cap is a ceiling on
  // what may be held, and the control law only holds what the link has been
  // seen to need, so a generous one costs nothing on a link that does not
  // need it: the deadband takes a clean link to zero either way.
  return Math.max(20, Math.min(100, Math.round((frames || 3) * (1000 / 120))));
}

function makePacer(limits) {
  const c = Object.assign({}, PACE, limits || null);
  let primed = false;
  let baseline = 0;
  let targetMs = c.MIN;
  let samples = [];            // { at, excess } over WINDOW_MS
  let lastControl = 0, lastDecay = 0;
  let lateTail = 0, lastExcess = 0, underruns = 0;

  function tail() {
    const n = samples.length;
    if (!n) return 0;
    const v = samples.map((one) => one.excess).sort((a, b) => a - b);
    return v[Math.min(n - 1, Math.floor(n * c.QUANTILE))];
  }

  /* The reserve was shorter than what the link just did, so the judder has
     already happened -- step up for the frames behind this one.

     Capped by the same tail estimate the periodic control would apply, only
     evaluated now instead of up to CONTROL_MS later. Uncapped it compounds:
     every late frame adds BUMP while decay sheds 8ms a second, so a link with
     a few percent of late frames ratchets to the cap and stays pinned there.
     moonlight-web measured that: 24ms of reserve held against a p95 tail of
     2.7ms. The quantile is the whole point -- cover the tail and let the
     outliers hitch -- and reacting to each outlier overrides it. */
  function noteUnderrun() {
    underruns += 1;
    const justified = tail() * c.SAFETY;
    const want = Math.min(c.MAX, targetMs + c.BUMP, justified);
    if (want > targetMs) targetMs = want;
  }

  function control(nowMs) {
    if (nowMs - lastControl < c.CONTROL_MS) return;
    lastControl = nowMs;
    lateTail = tail();
    const want = Math.min(c.MAX, lateTail * c.SAFETY);
    if (want > targetMs) {
      targetMs = want;
      lastDecay = nowMs;
    } else if (nowMs - lastDecay >= c.DECAY_MS) {
      lastDecay = nowMs;
      targetMs = Math.max(c.MIN, want, targetMs - c.DECAY_STEP);
    }
    if (targetMs < c.DEADBAND) targetMs = c.MIN;
  }

  return {
    /* When this frame should be shown, on the same clock `nowMs` is on. */
    schedule(captureMs, nowMs) {
      if (!(captureMs > 0)) return nowMs;     // no stamp, nothing to pace to
      const delay = nowMs - captureMs;
      if (!primed || Math.abs(delay - baseline) > c.RESYNC_MS) {
        primed = true;
        baseline = delay;
        samples = [];
        lastControl = lastDecay = nowMs;
        lateTail = lastExcess = 0;
        return nowMs;
      }
      baseline = Math.min(delay, baseline + c.DRIFT);
      const excess = delay - baseline;        // >= 0 by construction
      lastExcess = excess;
      samples.push({ at: nowMs, excess });
      const cutoff = nowMs - c.WINDOW_MS;
      while (samples.length && samples[0].at < cutoff) samples.shift();
      while (samples.length > c.MAX_SAMPLES) samples.shift();
      control(nowMs);
      const wait = targetMs - excess;
      // Noted after the deadline is worked out, never for this frame: it is
      // already late, and holding it longer adds to the hitch being removed.
      if (targetMs > 0 && excess > targetMs) noteUnderrun();
      return wait > c.SLACK_MS ? nowMs + wait : nowMs;
    },
    /* A frame due but not arrived, seen by the renderer rather than here. */
    ranDry() { noteUnderrun(); },
    forget() {
      primed = false;
      baseline = 0;
      targetMs = c.MIN;
      samples = [];
      lateTail = lastExcess = 0;
    },
    cap(frames) { c.MAX = maxFor(frames); },
    reserve() { return Math.round(targetMs); },
    stats() {
      return { reserve: Math.round(targetMs), tail: Math.round(lateTail),
               excess: Math.round(lastExcess), underruns };
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
/* What kind of unit this is, for either codec.
 *
 * H.264 keeps the type in the low five bits of one byte. H.265 has a two-byte
 * header and the type is six bits starting one bit up. Reading an H.265 stream
 * with the H.264 rule is not approximately right, it is scrambled -- and it
 * shipped that way: an H.265 IDR (type 19, first byte 0x26) reads as 6 under
 * the H.264 rule, so `hasPicture` called every keyframe "not a picture" and
 * held it back. The decoder was never given one and the screen stayed black,
 * which is the blacking out coming back the moment this host chose H.265.
 *
 * Parameter sets differ as well: 7 and 8 in H.264, 32, 33 and 34 (VPS, SPS,
 * PPS) in H.265. So does what counts as a picture: 1 to 5 against 0 to 31. */
function nalKind(unit, hevc) {
  return hevc ? (unit[0] >> 1) & 0x3f : unit[0] & 0x1f;
}

function nalIsParameterSet(kind, hevc) {
  return hevc ? (kind >= 32 && kind <= 34) : (kind === 7 || kind === 8);
}

function nalIsPicture(kind, hevc) {
  return hevc ? kind <= 31 : (kind >= 1 && kind <= 5);
}

/* Annex-B again from units, each behind a four-byte start code. */
function joinAnnexB(units) {
  let size = 0;
  for (const one of units) size += 4 + one.length;
  const out = new Uint8Array(size);
  let at = 0;
  for (const one of units) {
    out[at] = 0; out[at + 1] = 0; out[at + 2] = 0; out[at + 3] = 1;
    at += 4;
    out.set(one, at);
    at += one.length;
  }
  return out;
}

/* One SPS and one PPS in a frame, not several.
 *
 * Measured on iOS Safari, which is where this was found: the keyframes this
 * host sends arrive as `AUD SPS PPS SPS PPS IDR` -- the parameter sets twice.
 * The encoder emits them with the IDR and h264parse's config-interval=-1 puts
 * them in front of it as well, so both are present and the decoder is handed
 * a set it has already been given inside the same access unit. It survives
 * several of those and then fails on one, with EncodingError, which is a
 * picture that works for a few seconds and goes black.
 *
 * The last copy of each is the one kept, so if the two ever disagree the newer
 * wins -- and whether they disagreed is reported, because two *different* SPSs
 * in one frame and two identical ones are different faults.
 *
 * Returns the frame unchanged when there is nothing repeated, which is every
 * frame on a host that does not do this. */
function tidyParameterSets(bytes, hevc) {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let units;
  try {
    units = splitAnnexB(view);
  } catch (_) {
    return { data: bytes, dropped: 0, disagreed: false, copies: null };
  }
  // Keyed by NAL type, because H.265 has three parameter sets rather than two
  // and hard-coding a pair of variables for them is how the H.264 shape got
  // baked in here in the first place.
  const held = new Map();
  let dropped = 0, disagreed = false, copies = null;
  const hex = (u) => Array.from(u)
    .map((b) => (b < 16 ? "0" : "") + b.toString(16)).join("");
  const same = (a, b) => {
    if (!a || !b || a.length !== b.length) return false;
    for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return false;
    return true;
  };
  const rest = [];
  for (const unit of units) {
    const kind = nalKind(unit, hevc);
    if (nalIsParameterSet(kind, hevc)) {
      const before = held.get(kind);
      if (before) {
        dropped += 1;
        if (!same(before, unit)) {
          disagreed = true;
          // Both copies, kept for the caller to report once. Which of them is
          // authoritative decides whether the decoder runs on the right
          // parameters or stale ones, and that is not guessable from here --
          // it is the difference between a clean picture and artefacts that
          // clear at every keyframe and come straight back.
          if (!copies) copies = [];
          copies.push({ kind, first: hex(before), then: hex(unit) });
        }
      }
      held.set(kind, unit);
      continue;
    }
    rest.push(unit);
  }
  if (!dropped) return { data: bytes, dropped: 0, disagreed: false, copies: null };
  // Put the surviving pair back immediately before the first coded slice,
  // which is where a decoder expects to meet them.
  // In ascending type order, which is VPS, SPS, PPS for H.265 and SPS, PPS for
  // H.264 -- the order a decoder needs them in, since each refers back to the
  // one before it.
  const sets = Array.from(held.keys()).sort((a, b) => a - b)
    .map((k) => held.get(k));
  const out = [];
  let placed = false;
  for (const unit of rest) {
    if (!placed && nalIsPicture(nalKind(unit, hevc), hevc)) {
      for (const one of sets) out.push(one);
      placed = true;
    }
    out.push(unit);
  }
  if (!placed) for (const one of sets) out.push(one);
  return { data: joinAnnexB(out), dropped, disagreed, copies };
}

/* Whether a frame contains a coded picture at all.
 *
 * Measured, and it is what a decoder dies on: frames arrive carrying nothing
 * but `SPS PPS` -- forty bytes, no coded slice -- delivered as delta frames.
 * The host sends its parameter sets as their own access unit and WebRTC's
 * depacketizer hands that over like any other frame, so a chunk with no
 * picture in it went to the decoder, which answered EncodingError. A picture
 * that had been running for several seconds went black, over and over.
 *
 * NAL 1 is a non-IDR slice and NAL 5 an IDR slice; 2 to 4 are the partitions
 * of a slice, which only appear in profiles this never sees but count all the
 * same. Everything else -- parameter sets, delimiters, SEI, filler -- carries
 * no picture. */
function hasPicture(bytes, hevc) {
  let units;
  try {
    units = splitAnnexB(
        bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes));
  } catch (_) {
    return true;            // unreadable: hand it over rather than drop it
  }
  // Nothing parsed is not the same as nothing in it. splitAnnexB answers with
  // an empty list for bytes it finds no start code in, and that is the one
  // case where guessing wrong is expensive in only one direction: a frame
  // wrongly dropped is a black screen until the next keyframe, a frame wrongly
  // kept is a single bad decode the decoder is built to survive.
  if (!units.length) return true;
  for (const unit of units) {
    if (nalIsPicture(nalKind(unit, hevc), hevc)) return true;
  }
  return false;
}

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
/* Set by the page before a painter is made. Kept here so the worker and the
   page cannot disagree about the default. */
let smoothingFrames = 3;
function smoothingWanted() { return smoothingFrames; }
function setSmoothing(frames) {
  smoothingFrames = Math.max(1, Math.min(10, Number(frames) || 3));
  return smoothingFrames;
}

/* A gap between pieces arriving that is long enough to be seen as a freeze
   rather than felt as jitter. */
const STALL_MS = 250;

function makePainter(canvas, say) {
  let worker = null, running = false, mine = canvas;
  let last = null;                       // the worker's last set of counters
  let ever = false;                      // it has painted at least once
  let carrying = null, letting = null;   // the channel, and how to stop reading
  let beating = 0;                       // the animation-frame loop
  let onGone = null, onShape = null;
  let chunks = 0;                        // pieces off the channel, ever
  let paintFlat = false;                 // 2D instead of WebGL
  let fromTrack = null;                  // a receiver, while starting
  let chunkAt = 0, chunkGap = 0;         // and the worst gap between them
  const stalls = [];                     // when the long ones began

  /* How regularly the stalls come, in seconds, or 0 when they do not.
   *
   * Every interval within a sixth of the median is a metronome, and a
   * metronome is not congestion -- it is something taking the radio on a
   * schedule. Six of them before saying so, because a false "your machine is
   * misbehaving" is worse than staying quiet. */
  function stallPeriod() {
    if (stalls.length < 6) return 0;
    const gaps = [];
    for (let i = 1; i < stalls.length; i += 1) gaps.push(stalls[i] - stalls[i - 1]);
    const sorted = gaps.slice().sort((a, b) => a - b);
    const middle = sorted[Math.floor(sorted.length / 2)];
    if (!(middle > 0)) return 0;
    const agree = gaps.every((g) => Math.abs(g - middle) <= middle * 0.167);
    return agree ? Math.round(middle / 100) / 10 : 0;
  }
  let beatAt = 0, beatGap = 0;           // the same, for animation frames
  const now = () => ((typeof performance !== "undefined" && performance.now)
                     ? performance.now() : Date.now());

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
    /* Draw with the 2D context rather than WebGL. Set before start(); the
       context belongs to the canvas and the canvas is handed over once. */
    useFlat(yes) { paintFlat = Boolean(yes); },

    /* Start from a receiver rather than from a data channel.
     *
     * Everything past the first step is identical -- the same worker, the
     * same decoder, the same pacing -- so this hands `receiver` where the
     * other hands a channel and the worker is told which to listen on.
     *
     * The transform must be attached before the receiver has frames to give
     * it, so this is called the moment the track arrives, and it is never
     * detached: removing one permanently stops the receiver delivering. */
    startFromTrack(receiver, codec) {
      if (running) return false;
      if (typeof RTCRtpScriptTransform === "undefined") {
        say("this browser has no encoded transform, so the frames cannot be "
            + "taken off the media track");
        return false;
      }
      fromTrack = receiver;
      return this.start(null, codec);
    },

    start(channel, codec) {
      if (running) return false;
      const viaTrack = fromTrack;
      fromTrack = null;                  // one start, one receiver
      if (!viaTrack && (!channel || channel.readyState !== "open")) {
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
          // The transform goes on here, at the first instant the worker can
          // receive one -- not after the decoder is built.
          //
          // Attached any later it delivers nothing at all, and "later" is
          // measured in frames, not seconds: the receiver had begun. Waiting
          // for the decoder cost exactly that, and read as a transform that
          // said it was attached and then handed over nothing for ever. The
          // frames that arrive before there is a decoder are dropped by
          // take(), which is the cheaper of the two mistakes.
          if (viaTrack) {
            try {
              viaTrack.transform = new RTCRtpScriptTransform(it, { kind: "video" });
              say("the media track's frames were routed to the decoder");
            } catch (err) {
              say("this browser would not take an encoded transform: "
                  + ((err && err.message) || "no reason given"));
              this.stop();
              if (onGone) onGone();
              return;
            }
          }
          it.postMessage({ start: { canvas: surface, codec,
                                    smoothing: smoothingWanted(),
                                    flat: paintFlat } },
                         [surface]);
          return;
        }
        if (m.started) {
          if (viaTrack) {
            // Nothing to ask the host for -- it is already sending the
            // picture on this line -- except a keyframe, because the decoder
            // has just been built and the browser's own decoder is no longer
            // there to ask on its behalf.
            try {
              if (channel && channel.readyState === "open") channel.send("key");
            } catch (_) {}
            return;
          }
          // Only now: frames arriving before there is a decoder are frames
          // thrown away, and the host holds them back until it is asked.
          try { channel.send("on"); } catch (_) {}
          say("the picture channel was asked for whole frames");
          return;
        }
        if (m.painted) { ever = true; return; }
        if (m.shape) { if (onShape) onShape(m.shape); return; }
        // The worker cannot reach the channel: it has the counters, the page
        // has the wire. Both of these are the worker asking the page to say
        // something to the host.
        if (m.ask === "key") {
          try { if (channel.readyState === "open") channel.send("key"); }
          catch (_) {}
          return;
        }
        if (m.tally) {
          try {
            if (channel.readyState === "open") {
              channel.send(JSON.stringify(m.tally));
            }
          } catch (_) {}
          return;
        }
        if (m.note) { say(m.note); return; }
        if (m.stats) { last = m.stats; return; }
        if (m.failed) {
          say(m.failed);
          this.stop();
          if (onGone) onGone();
        }
      };
      // The display's own cadence, handed to the worker.
      //
      // A worker cannot see the refresh; the page can, and an animation
      // frame happens just after one. Telling the worker to paint there puts
      // every frame on the display's rhythm rather than near it, which is
      // the difference between paints that are evenly spaced and a picture
      // that looks evenly spaced.
      const beat = () => {
        if (!worker) return;
        // And the longest gap between animation frames, on the same clock.
        // A stalled channel and a throttled page both end in a picture that
        // holds still; only these two numbers side by side say which.
        const at = now();
        if (beatAt && at - beatAt > beatGap) beatGap = at - beatAt;
        beatAt = at;
        try { worker.postMessage({ tick: true }); } catch (_) {}
        beating = requestAnimationFrame(beat);
      };
      beating = requestAnimationFrame(beat);
      if (viaTrack) {
        running = true;
        return true;                     // nothing to listen to here
      }
      channel.binaryType = "arraybuffer";
      carrying = channel;
      const onPictureChunk = (event) => {
        const data = event.data;
        if (!worker || !(data instanceof ArrayBuffer)) return;
        // Counted here rather than asked of the worker, because what needs it
        // is the page's own check on whether this connection is alive, and
        // that has to be answerable without waiting for a message to come
        // back. See `arrived`.
        chunks += 1;
        // The longest a piece has ever taken to follow the one before it,
        // timed here -- on the page, the moment the channel hands it over,
        // before the worker has touched it. Everything else measured so far
        // has been after the fact and downstream: "frames did not arrive" is
        // true of a channel that stalled and of a thread that could not
        // service it, and those want opposite fixes. This one number
        // separates them, and none of the others could.
        const at = now();
        if (chunkAt) {
          const gap = at - chunkAt;
          if (gap > chunkGap) chunkGap = gap;
          // When a stall began, kept so the spacing between them can be
          // looked at. moonlight-web's PeriodicStallDetector is the idea:
          // congestion is random, a radio being time-shared is not. On a Mac
          // that is AWDL -- AirDrop, Handoff, AirPlay, Sidecar and Continuity
          // share the Wi-Fi chip with the infrastructure link and leave it at
          // periodic availability windows -- and it sits below the
          // application, so no amount of changing this code touches it. The
          // regularity is the whole signal, and it is the difference between
          // "your network is busy" and "something on this machine is taking
          // the radio", which the person at the keyboard can actually fix.
          if (gap > STALL_MS) {
            stalls.push(at);
            if (stalls.length > 12) stalls.shift();
          }
        }
        chunkAt = at;
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

    /* Told the shape of the picture as the decoder sees it. The page has no
       other way to know it while this path is drawing: the <video> element it
       would normally measure is holding the sound and nothing else. */
    whenShaped(fn) { onShape = fn; },

    stop() {
      running = false;
      last = null;
      ever = false;
      if (beating) { cancelAnimationFrame(beating); beating = 0; }
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
      // The smoothness first, because it is the only part that describes what
      // an eye can see, and because everything after it kept being the half
      // that got cut off.
      return ("drawing here: "
              + (last.shown
                 ? "painted every " + last.shown.typical + "ms typical, worst "
                   + last.shown.worst + "ms, " + last.shown.off + " of "
                   + last.shown.of + " off the beat; "
                 : "")
              + (last.ticks
                 ? last.starved + " of " + last.ticks
                   + " refreshes had nothing to paint and " + (last.early || 0)
                   + " nothing due yet (a refresh is "
                   + (last.refresh || "?") + "ms); "
                 : "")
              + (last.pace
                 ? "reserve " + last.pace.reserve + "ms over a late tail of "
                   + last.pace.tail + "ms, widened " + last.pace.underruns
                   + " times; "
                 : "")
              + last.handed + " handed over by the transform, "
              + last.fed + " fed to the decoder, " + last.out + " came out, "
              + last.drawn + " painted, " + last.refused + " refused, "
              + last.skipped + " before the first keyframe, " + last.stale
              + " too late to matter, " + (last.behind || 0)
              + " dropped catching up, " + (last.gaps || 0)
              + " gaps in the host's numbering, " + (last.lost || 0)
              + " lost on the way, canvas " + (last.size || "?")
              + (last.drawFails ? ", " + last.drawFails + " paints refused" : ""));
    },

    /* How many frames to keep in hand, changed while it runs. */
    smooth(frames) {
      if (worker) { try { worker.postMessage({ smoothing: frames }); } catch (_) {} }
    },

    /* Ask without emptying the counters, for whoever only wants to know
       whether anything is happening. */
    peek() {
      if (worker) { try { worker.postMessage({ peek: true }); } catch (_) {} }
    },
    painted() { return ever || Boolean(last && last.ever); },
    /* Frames are arriving and none has been a keyframe yet, so there is
       nothing to decode against and nothing is wrong. */
    waitingForKey() {
      return Boolean(last && last.handed > 0 && !last.keyed);
    },
    /* Nothing has arrived at all since the last look. That is the link or the
       host, and it says nothing whatever about whether this browser can
       decode the stream -- so it must not be answered by walking to the next
       spelling of the codec and then giving up having never been tested.

       Deliberately false when there are no counters at all. `last` is null
       until the worker's first reply, and "no information yet" is not the
       same claim as "nothing arrived" -- the caller answers this one by
       waiting, and waiting on no information means waiting in front of a
       black picture with nothing to end it.

       Tried the other way and reverted the same hour: a transform that
       attaches and is handed nothing sits at null for ever, so the page
       waited instead of falling back and the screen stayed black on every
       join. A picture by the other route beats no picture, which is the rule
       the rest of this file already follows. */
    starving() {
      return Boolean(last && !last.handed);
    },
    drawnLately() { return last ? last.drawn : 0; },
    /* How much has come off the picture channel, ever.
     *
     * The page's liveness check used to watch video bytes on the media track,
     * and the host now deliberately sends none of those to a guest drawing
     * its own picture -- it was sending the picture twice. So the check saw
     * silence, called the connection dead and rebuilt it, every ten seconds,
     * for ever: the black screening and the freezes were a renegotiation each
     * time. This is the other place a working connection shows itself. */
    arrived() { return chunks; },
    /* The worst gaps seen since the last time this was asked, in the page's
       own time: one for pieces arriving off the channel, one for animation
       frames. Reading them clears them, so each answer describes the window
       just gone. */
    gaps() {
      const was = { chunk: Math.round(chunkGap), beat: Math.round(beatGap),
                    stalls: stalls.length, every: stallPeriod() };
      chunkGap = 0;
      beatGap = 0;
      return was;
    },
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { makePacer, codecCandidates, pickCodec,
                     toAnnexB, looksAnnexB, splitAnnexB, setSmoothing,
                     joinAnnexB, tidyParameterSets, hasPicture,
                     nalKind, nalIsPicture, nalIsParameterSet,
                     avcDescription, toLengthPrefixed,
                     makePainter, PACE };
}
