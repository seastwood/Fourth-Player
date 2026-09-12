/* The picture presets, checked for the thing they exist to prevent.
 *
 * The dials are not independent: bits are shared across every pixel of every
 * frame, so raising the size at a fixed bitrate makes the picture softer. The
 * report that prompted these was "I increased the resolution to 1080p and the
 * quality remained kind of poor" -- true, and arithmetic rather than a fault.
 * A preset list that repeated the mistake would be worse than none.
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const from = app.indexOf("const STREAM_PRESETS = [");
const until = app.indexOf("];", from) + 2;
if (from < 0) { console.log("FAIL no STREAM_PRESETS in app.js"); process.exit(1); }
const presets = new Function(app.slice(from, until) + "; return STREAM_PRESETS;")();

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

const bpp = (p) => (p.kbps * 1000) / (p.height * (p.height * 16 / 9) * p.fps);

check(presets.length >= 4, "there are enough presets to be a real choice");
check(presets.every((p) => p.label && p.why && p.height && p.fps && p.kbps),
      "every preset is fully specified, including why somebody would pick it");

for (const p of presets) {
  const v = bpp(p);
  check(v >= 0.09 && v <= 0.30,
        `${p.label} (${p.height}p${p.fps} @ ${p.kbps}) is ${v.toFixed(3)} bits/pixel -- in the sane band`);
}

// The failure that prompted this: 1080p30 at 4500 is 0.07 bits/pixel.
check(bpp({ height: 1080, fps: 30, kbps: 4500 }) < 0.09,
      "and the setting that was reported as poor sits below that band, as it should");

// Bigger pictures must ask for more bits, never the same or fewer.
//
// Ordered by real pixels a second, which goes as the square of the height --
// the width is 16/9 of it. Getting that wrong makes 720p60 look like more
// work than 1080p30 when it is in fact less (55.3M against 62.2M), and this
// test failed on its own arithmetic before it failed on anything real.
const rate = (p) => (16 / 9) * p.height * p.height * p.fps;
const byPixels = [...presets].sort((a, b) => rate(a) - rate(b));
let ok = true;
for (let i = 1; i < byPixels.length; i++) {
  if (byPixels[i].kbps < byPixels[i - 1].kbps) ok = false;
}
check(ok, "more pixels per second always asks for more bitrate, never less");

check(presets.some((p) => p.height <= 540),
      "there is something for a weak uplink");
check(presets.some((p) => p.fps >= 60),
      "and something that puts motion first");

// Smoothing should rise as the link gets worse, not fall.
const weakest = byPixels[0], strongest = byPixels[byPixels.length - 1];
check(weakest.jitter >= strongest.jitter,
      "the modest preset holds more picture back than the sharpest one");

console.log("");
if (fails) { console.log(`presets: ${fails} FAILED`); process.exit(1); }
console.log("presets: all ok");
