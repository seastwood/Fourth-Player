/* The quality presets, which exist because these settings are not independent.
 *
 * Asked for as: make the stream more responsive and buttery smooth, like
 * playing natively.
 *
 * "Responsive" is not one dial. It is the frame rate, and then three separate
 * buffers that each add delay: how long the browser holds a frame before
 * drawing it, how much encoded video may pile up on the host, and how much
 * the encoder may hold back to smooth a burst. A preset that set only the
 * first left the other two wherever a previous experiment had put them --
 * which is how a host ended up at jitter 60, queue 70 and cpb 170 while
 * somebody wondered why it felt slow.
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

const block = app.slice(app.indexOf("const STREAM_PRESETS = ["),
                        app.indexOf("/* Only the sizes this host"));
const entries = block.match(/\{[^{}]*\}/gs) || [];
check(entries.length >= 5, `${entries.length} presets found`);

const presets = entries.map((text) => {
  const num = (key) => {
    const m = new RegExp(key + ":\\s*(\\d+)").exec(text);
    return m ? Number(m[1]) : null;
  };
  return {
    label: /label: "([^"]+)"/.exec(text)[1],
    height: num("height"), fps: num("fps"), kbps: num("kbps"),
    jitter: num("jitter"), queue: num("queue"), cpb: num("cpb"),
  };
});

console.log("every preset sets the whole delay chain, not just one buffer");
for (const p of presets) {
  check(p.jitter != null && p.queue != null && p.cpb != null,
        `${p.label}: jitter ${p.jitter}, queue ${p.queue}, cpb ${p.cpb}`);
}

console.log("\nand the applying code writes all of them");
const apply = app.slice(app.indexOf("function buildStreamPresets"),
                        app.indexOf("/* Which screens the host has"));
for (const id of ["stream-size", "stream-fps", "stream-bitrate",
                  "stream-jitter", "stream-queue", "stream-cpb"]) {
  check(apply.includes(id), `${id} is written`);
}

console.log("\nthe faster a preset is, the less it buffers");
// Not a rule of thumb: buffering is latency, so anything sold as more
// responsive must ask for less of it. A preset offering 120fps with a
// mobile-data jitter buffer would be a contradiction nobody could see.
const sorted = [...presets].sort((a, b) => b.fps - a.fps || b.kbps - a.kbps);
for (let i = 1; i < sorted.length; i += 1) {
  const faster = sorted[i - 1], slower = sorted[i];
  check(faster.jitter <= slower.jitter,
        `${faster.label} (${faster.fps}fps) buffers no more than `
        + `${slower.label} (${slower.fps}fps): ${faster.jitter} <= ${slower.jitter}`);
}

console.log("\nand bitrate rises with the pixels and frames it has to carry");
// The complaint this whole mechanism exists for: raising the resolution and
// getting a softer picture, because the bits were spread over more pixels.
for (const p of presets) {
  const load = (p.height * p.height * 16 / 9) * p.fps;
  check(p.kbps / (load / 1e6) > 30,
        `${p.label} has ${Math.round(p.kbps / (load / 1e6))} kb/s per megapixel-second`);
}

console.log("\nthe fast ones say what they need of the host");
// A 60Hz desktop cannot be captured at 120: the extra frames are duplicates
// that cost bitrate and buy nothing. The preset has to say so, because the
// host's refresh rate is not something the page can see.
for (const p of presets.filter((x) => x.fps > 60)) {
  const text = entries.find((e) => e.includes(`"${p.label}"`));
  check(/Hz|refresh/i.test(text),
        `${p.label} mentions what refresh rate the host's screen needs`);
}

process.exit(fails ? 1 : 0);
