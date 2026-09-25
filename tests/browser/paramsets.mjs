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

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
