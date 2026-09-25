/* One SPS and one PPS in a keyframe, not two of each.
 *
 * Found on an iPhone, and only after four other explanations had been reasoned
 * out and shown wrong. The decoder ran for two to seven seconds and then failed
 * with EncodingError, and the frame it failed on was named by the host log:
 *
 *   died on key 245860B [nal9 SPS PPS SPS PPS IDR]; parameter sets unchanged
 *
 * The parameter sets are in there twice. The encoder emits them with the IDR
 * and h264parse's config-interval=-1 puts them in front of it as well, so the
 * decoder is handed a set it has already been given inside the same access
 * unit. It tolerates several and then does not, which from the sofa is a
 * picture that runs for a few seconds and goes black, over and over.
 *
 * What makes it worth a test rather than a one-line fix: the frame must come
 * out still decodable. Stripping a duplicate is easy; putting the survivors
 * back where a decoder expects to meet them -- immediately before the first
 * coded slice -- is the part that would break silently. */
import { strict as assert } from "node:assert";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const paint = require("../../web/paint.js");
const { tidyParameterSets, splitAnnexB, joinAnnexB } = paint;

let bad = 0;
const check = (cond, what) => {
  console.log((cond ? "  ok   " : "  FAIL ") + what);
  if (!cond) bad += 1;
};

const AUD = [9, 0x10];
const SEI = [6, 0x05, 0x01, 0x80];
const SPS = [0x67, 0x42, 0xc0, 0x33, 0xaa, 0xbb];
const SPS_OTHER = [0x67, 0x42, 0xc0, 0x33, 0xaa, 0xcc];
const PPS = [0x68, 0xce, 0x3c, 0x80];
const IDR = [0x65, 0x88, 0x84, 0x01, 0x02];
const SLICE = [0x61, 0x9a, 0x00];

const frame = (units) => joinAnnexB(units.map((u) => new Uint8Array(u)));
const kinds = (bytes) =>
  splitAnnexB(new Uint8Array(bytes)).map((u) => u[0] & 0x1f).join(" ");

console.log("the shape the host actually sends");
let got = tidyParameterSets(frame([AUD, SPS, PPS, SPS, PPS, IDR]));
check(got.dropped === 2, "both repeats are dropped, got " + got.dropped);
check(kinds(got.data) === "9 7 8 5",
      "leaving one of each in front of the IDR, got " + kinds(got.data));
check(got.disagreed === false,
      "and identical copies are not reported as disagreeing");

console.log("\nthe parameter sets land before the first slice, not at the end");
// The part that would break silently. A decoder meeting an IDR before the SPS
// it needs has nothing to decode it against.
got = tidyParameterSets(frame([SPS, PPS, IDR, SPS, PPS]));
const order = kinds(got.data).split(" ");
check(order.indexOf("7") < order.indexOf("5")
      && order.indexOf("8") < order.indexOf("5"),
      "SPS and PPS both precede the IDR, got " + kinds(got.data));
check(order.filter((k) => k === "7").length === 1
      && order.filter((k) => k === "8").length === 1,
      "and exactly one of each survives, got " + kinds(got.data));

console.log("\nnothing else in the frame is disturbed");
got = tidyParameterSets(frame([AUD, SEI, SPS, PPS, SPS, PPS, IDR, SLICE]));
check(kinds(got.data) === "9 6 7 8 5 1",
      "the delimiter, the SEI and both slices stay, in order, got "
      + kinds(got.data));

console.log("\na frame with nothing repeated is handed back untouched");
const clean = frame([AUD, SPS, PPS, IDR]);
got = tidyParameterSets(clean);
check(got.dropped === 0, "nothing dropped");
check(got.data === clean,
      "and the very same bytes, not a copy -- this runs on every keyframe");
const delta = frame([AUD, SLICE]);
check(tidyParameterSets(delta).data === delta,
      "a delta frame is not touched either");

console.log("\ncopies that disagree are noticed, and the newer one kept");
// Two different SPSs in one access unit is a different fault from two
// identical ones, and wants knowing about rather than silently papering over.
got = tidyParameterSets(frame([AUD, SPS, PPS, SPS_OTHER, PPS, IDR]));
check(got.disagreed === true, "the disagreement is reported");
const kept = splitAnnexB(new Uint8Array(got.data))
  .find((u) => (u[0] & 0x1f) === 7);
check(kept && kept[5] === 0xcc,
      "and the last copy is the one kept, so a changed set wins");

console.log("\nrubbish in is not a crash");
for (const junk of [new Uint8Array(0), new Uint8Array([0, 0, 0, 1]),
                    new Uint8Array([1, 2, 3])]) {
  const out = tidyParameterSets(junk);
  check(out && out.data !== undefined,
        "survives " + junk.length + " byte(s) of nonsense");
}

console.log("\nand the round trip is lossless for a frame it does change");
got = tidyParameterSets(frame([SPS, PPS, SPS, PPS, IDR]));
const back = splitAnnexB(new Uint8Array(got.data));
assert.deepEqual(Array.from(back[back.length - 1]), IDR);
check(true, "the IDR's bytes come back exactly as they went in");

/* The other half, and the one that was actually killing it.
 *
 *   died on delta 40B [SPS PPS]
 *
 * Forty bytes: the parameter sets as an access unit of their own, with no
 * coded slice, delivered as a delta frame. Fed to a decoder that is a chunk
 * with no picture in it, and the answer is EncodingError -- which took out a
 * decoder that had been running cleanly for four hundred frames. */
console.log("\nboth differing copies are reported, in full");
// Which copy is authoritative is not decidable from inside the page, and the
// wrong choice is artefacts that clear at every keyframe and come back. So the
// bytes of both are handed out rather than only the fact that they differed.
got = tidyParameterSets(frame([AUD, SPS, PPS, SPS_OTHER, PPS, IDR]));
check(Array.isArray(got.copies) && got.copies.length === 1,
      "one disagreeing pair is reported, got "
      + (got.copies ? got.copies.length : got.copies));
check(got.copies[0].kind === 7, "named by NAL type, got " + got.copies[0].kind);
check(got.copies[0].first === "6742c033aabb"
      && got.copies[0].then === "6742c033aacc",
      "with both copies as hex, got " + JSON.stringify(got.copies[0]));
check(tidyParameterSets(frame([AUD, SPS, PPS, SPS, PPS, IDR])).copies === null,
      "and identical copies report nothing, so the line means something");

console.log("\nwhether a frame carries a picture at all");
const { hasPicture } = paint;
check(hasPicture(frame([AUD, SPS, PPS, IDR])) === true, "an IDR is a picture");
check(hasPicture(frame([AUD, SLICE])) === true, "a plain slice is a picture");
check(hasPicture(frame([SPS, PPS])) === false,
      "parameter sets on their own are not -- this is the 40-byte frame");
check(hasPicture(frame([AUD, SPS, PPS])) === false,
      "nor with a delimiter in front of them");
check(hasPicture(frame([AUD, SEI])) === false, "nor is an SEI on its own");
check(hasPicture(frame([AUD])) === false, "nor a delimiter alone");
// Data-partitioned slices are NAL 2 to 4. They do not appear in the profiles
// this host sends, and counting them costs nothing next to dropping a picture.
check(hasPicture(frame([[0x42, 0x11]])) === true,
      "a slice data partition counts as a picture");
check(hasPicture(new Uint8Array([1, 2, 3])) === true,
      "and something unreadable is handed over rather than dropped: a frame "
      + "wrongly dropped is a black screen, a frame wrongly kept is one bad "
      + "decode");

/* H.265, which the first version of all this got silently and badly wrong.
 *
 * H.264 keeps the NAL type in the low five bits of one byte; H.265 has a
 * two-byte header with a six-bit type one bit up. Reading an H.265 stream with
 * the H.264 rule is not approximately right, it is scrambled, and it shipped:
 *
 *   IDR_W_RADL (19) -> 0x26 & 0x1f = 6   -> "not a picture", dropped
 *   TRAIL_N (0)     -> 0x00 & 0x1f = 0   -> "not a picture", dropped
 *   CRA (21)        -> 0x2a & 0x1f = 10  -> "not a picture", dropped
 *   SPS (33)        -> 0x42 & 0x1f = 2   -> read as a coded slice
 *   AUD (35)        -> 0x47 & 0x1f = 7   -> read as an SPS
 *
 * So whole classes of frame were thrown away and the parameter sets were
 * reordered around an access unit delimiter mistaken for one. The host chose
 * H.265 and the blacking out came straight back. */
console.log("\nH.265 has a different header, and it is read as one");
const { nalKind, nalIsPicture } = paint;
const h265 = (type, ...tail) => [type << 1, 1, ...tail];
const VPS = h265(32, 0xaa), HSPS = h265(33, 0xbb), HPPS = h265(34, 0xcc);
const HSPS2 = h265(33, 0xdd);
const HAUD = h265(35), HIDR = h265(19, 0x88), TRAIL_N = h265(0, 0x77);
const CRA = h265(21, 0x66);

for (const [name, unit, want] of [["IDR_W_RADL", HIDR, 19], ["TRAIL_N", TRAIL_N, 0],
                                  ["CRA", CRA, 21], ["SPS", HSPS, 33],
                                  ["AUD", HAUD, 35], ["VPS", VPS, 32]]) {
  check(nalKind(new Uint8Array(unit), true) === want,
        name + " reads as " + want + ", got "
        + nalKind(new Uint8Array(unit), true));
}

console.log("\nand the frames the old rule threw away are pictures again");
for (const [name, units] of [["an IDR", [HAUD, HIDR]],
                             ["a CRA", [HAUD, CRA]],
                             ["a non-reference trailing slice", [HAUD, TRAIL_N]]]) {
  check(hasPicture(frame(units), true) === true, name + " is a picture");
  // The point of the test: each of these was dropped before.
  check(hasPicture(frame(units), false) === false,
        "  and would have been dropped by the H.264 rule");
}
check(hasPicture(frame([VPS, HSPS, HPPS]), true) === false,
      "H.265 parameter sets on their own are still not a picture");

console.log("\nall three H.265 parameter sets are de-duplicated, in order");
// Three, not two: hard-coding a pair of variables for SPS and PPS is how the
// H.264 shape got baked in, so they are keyed by type now.
got = tidyParameterSets(
    frame([HAUD, VPS, HSPS, HPPS, VPS, HSPS2, HPPS, HIDR]), true);
check(got.dropped === 3, "all three repeats dropped, got " + got.dropped);
check(got.disagreed === true, "and the disagreeing SPS is reported");
const after = splitAnnexB(new Uint8Array(got.data))
  .map((u) => nalKind(u, true)).join(" ");
check(after === "35 32 33 34 19",
      "VPS then SPS then PPS then the picture -- each refers back to the one "
      + "before it, so the order is not cosmetic. Got " + after);
const cleanH = frame([HAUD, VPS, HSPS, HPPS, HIDR]);
check(tidyParameterSets(cleanH, true).data === cleanH,
      "and a clean H.265 frame is handed back untouched");

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
