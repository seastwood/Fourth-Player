/* Two ways to draw the picture, both kept on purpose.

   The browser's <video> element is not a fallback to be grown out of. It is
   the only method every browser has, it decodes in hardware without help, and
   on a weak machine it costs less than anything a page can do. Drawing on a
   canvas costs more and buys control over when each frame is shown. Which is
   right depends on the machine, so both stay and the next idea joins the
   list. */
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const page = readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");
const paint = require("../../web/paint.js");

let bad = 0;
const check = (cond, what) => {
  console.log((cond ? "  ok   " : "  FAIL ") + what);
  if (!cond) bad += 1;
};

console.log("the choice is a list, not a switch");
check(app.includes("const PAINT_METHODS = ["), "there is a list of methods");
check(/id: "browser"/.test(app), "the browser's own element is one of them");
check(/id: "here"/.test(app), "drawing here is another");
check(app.indexOf('id: "browser"') < app.indexOf('id: "here"'),
      "and the browser is first, so it is the default");
check(page.includes('<select id="stream-paint">'),
      "the page offers it as a dropdown");

console.log("and every option names what actually does the drawing");
// "The browser" and "this page" said who to blame and nothing about how,
// which is the part worth knowing when two are being compared -- and the
// next method added will be another combination of the same technologies.
for (const word of ["WebRTC", "WebCodecs", "video element", "canvas"]) {
  check(app.includes(word), `a label or its note names ${word}`);
}
const methods = app.slice(app.indexOf("const PAINT_METHODS = ["),
                          app.indexOf("function paintMethodById"));
const labels = [...methods.matchAll(/label: "([^"]+)"/g)].map((m) => m[1]);
check(labels.length >= 2, `found ${labels.length} labels`);
for (const one of labels) {
  check(/WebRTC|WebCodecs/.test(one),
        `"${one}" says which technology draws it`);
}

console.log("a method this browser cannot do is not chosen for it");
check(app.includes("found.ok() ? found.id : PAINT_METHODS[0].id"),
      "a remembered method that will not work falls back to the browser");
check(app.includes("option.disabled = !one.ok()"),
      "and is shown disabled rather than silently missing");

console.log("there is exactly one way back to the browser's element");
check(app.includes("function stopPainting"), "stopPainting exists");
for (const spot of ["painter.start(receiver, codec)", "if (!codec)",
                    "!paintMethodById(\"here\").ok()"]) {
  check(app.includes(spot), "every failure goes through it (" + spot + ")");
}
check((app.match(/setPaintMethod\("browser"\)/g) || []).length >= 3,
      "a failed experiment is never a black screen");

console.log("a codec not known yet is not the same as one that cannot work");
// The first real attempt at this failed here: a receiver's getParameters()
// came back with no codecs a second into the connection, the page read that
// as "unknown", and it put the viewer back on the browser -- so choosing the
// option appeared to do nothing at all.
check(app.includes("if (shape.mime) {"),
      "a known codec that is not H.264 switches back and says so");
check(app.includes("waiting to draw here"),
      "one that is not known yet waits instead");
check(app.includes('if (paintMethod === "here" && !painter) startPainting()'),
      "and the watchdog keeps trying, so the choice starts when it can");
check(app.includes("lastCodec = { mime: codec.mimeType"),
      "the codec is remembered from the statistics, which always carry it");
check(app.includes("return lastCodec;"),
      "and used when the receiver will not say");

console.log("the canvas and the video are never both showing");
check(app.includes("canvas.hidden = false") && app.includes("video.hidden = true"),
      "starting hides the video");
check(app.includes("canvas.hidden = true") && app.includes("video.hidden = false"),
      "stopping hides the canvas");

console.log("a fresh connection does not leave a reader on a dead receiver");
check(app.includes('stopPainting("")') &&
      app.indexOf('stopPainting("")') < app.indexOf("await pc.setRemoteDescription"),
      "the old receiver is let go before the new offer is taken");
check(app.includes("if (event.track.kind === \"video\") startPainting()"),
      "and it starts again when the new track arrives");

console.log("nothing is fed to the decoder before the first keyframe");
// A decoder handed a delta frame with nothing to apply it to raises on the
// spot, and that exception was being swallowed -- so the first second of
// every connection was a stream of invisible errors and whether the decoder
// recovered was luck. It was a black screen with "drawing the picture here"
// in the log and no way to tell which of four things had gone wrong.
const paintSrc = readFileSync(new URL("../../web/paint.js", import.meta.url), "utf8");
check(paintSrc.includes("if (!key) { skipped += 1; return; }"),
      "deltas before the first keyframe are counted and dropped");
check(paintSrc.includes("started = true"), "and the gate opens on a keyframe");
check(paintSrc.includes("started = false"), "and closes again on stop");

console.log("and every stage of it is counted, because they fail alike");
for (const one of ["fed", "out", "drawn", "refused", "skipped"]) {
  check(new RegExp("\\b" + one + "\\b").test(paintSrc), `${one} is counted`);
}
check(paintSrc.includes("came out") && paintSrc.includes("painted"),
      "the report distinguishes decoded from painted");
check(app.includes("painter.report()"), "and the page says it out loud");
check(app.includes('painter ? "webrtc counted "'),
      "with WebRTC's own numbers labelled as its own, not as the picture");

console.log("\nthe codec string is asked of the browser, not constructed and hoped for");
// H.265 was left out of this path because its string is assembled from a
// profile space, a profile, a bit-reversed compatibility mask, a tier letter,
// a level and a constraint byte, in an order that is not the order they
// appear in the fmtp. That was the wrong call: a wrong string is a decoder
// that refuses to configure, which is reported rather than silent, and
// isConfigSupported makes asking free.
const h264 = paint.codecCandidates("video/H264", "profile-level-id=640c1f");
check(h264[0] === "avc1.640C1F",
      "H.264 offers the negotiated profile first: " + h264[0]);
check(h264.indexOf("avc1.42E01F") > 0,
      "with constrained baseline behind it, which is what silence means");

const main = paint.codecCandidates("video/H265",
                                   "profile-id=1;tier-flag=0;level-id=120");
check(main.length >= 4, `H.265 offers several spellings: ${main.length}`);
check(main[0] === "hvc1.1.6.L120.B0", "Main at the level agreed: " + main[0]);
check(main.some((c) => c.indexOf("hev1.") === 0),
      "and hev1 as well as hvc1, since builds disagree about which they take");

const ten = paint.codecCandidates("video/H265",
                                  "profile-id=2;tier-flag=1;level-id=93");
check(ten[0] === "hvc1.2.4.H93.B0",
      "Main 10 leads with mask 4 and the high tier: " + ten[0]);
check(ten[0].indexOf(".H") > 0, "tier-flag=1 is the high tier, not the level");

const av1 = paint.codecCandidates("video/AV1", "profile=0;level-idx=13;tier=0");
check(av1[0] === "av01.0.13M.08", "AV1 is spelled too, ready for a host that "
      + "sends it: " + av1[0]);

check(paint.codecCandidates("video/VP8", "").length === 0,
      "and a codec nobody here handles offers nothing rather than a guess");

console.log("\nand nothing is attempted that the browser has not agreed to");
const paintFile = readFileSync(new URL("../../web/paint.js", import.meta.url), "utf8");
check(paintFile.includes("VideoDecoder.isConfigSupported"),
      "the browser is asked which spelling it will take");
check(paintFile.includes("answer.supported"), "and its answer is believed");
check(app.includes("await pickCodec"), "the page waits for that answer");
check(app.includes("paintStarting"),
      "with a guard, because the watchdog calls this every couple of seconds");

console.log("\nthe bitstream is looked at rather than believed");
// The symptom of getting this wrong is one frame fed and "Decoder failure"
// with nothing else to go on. WebRTC is supposed to hand out Annex B and not
// every browser does; a decoder configured without a description expects
// nothing else.
const bytes = (a) => new Uint8Array(a).buffer;
let shaped = paint.toAnnexB(bytes([0, 0, 0, 1, 0x65, 0x88]));
check(shaped.shape === "annex-b", "a four-byte start code is left alone");
check(new Uint8Array(shaped.data).join() === "0,0,0,1,101,136",
      "untouched, byte for byte");
check(paint.toAnnexB(bytes([0, 0, 1, 0x65, 7])).shape === "annex-b",
      "and so is a three-byte one");

shaped = paint.toAnnexB(bytes([0, 0, 0, 3, 0x67, 1, 2, 0, 0, 0, 2, 0x68, 9]));
check(shaped.shape === "length-prefixed",
      "lengths that add up exactly are proof, not a guess");
check(new Uint8Array(shaped.data).join() === "0,0,0,1,103,1,2,0,0,0,1,104,9",
      "each length becomes a start code, in place: "
      + new Uint8Array(shaped.data).join());

check(paint.toAnnexB(bytes([9, 9, 9, 9, 1, 2, 3])).shape === "unknown",
      "anything else goes through unchanged, for the decoder to judge");

console.log("\nand the transform is not attached before the worker can hear it");
// Measured: "0 fed to the decoder" over ten seconds while twenty megabytes
// arrived. new Worker() returns before the worker's script has run, so
// attaching a transform on the next line races its own handler -- and the
// losing side is silence.
const frames = readFileSync(new URL("../../web/frames.js", import.meta.url), "utf8");
check(frames.indexOf("self.onrtctransform") < frames.indexOf("ready: true"),
      "the worker registers its handler before it says it is ready");
check(paintFile.includes("if (m && m.ready)"),
      "and the page waits for that before attaching anything");
check(paintFile.indexOf("new RTCRtpScriptTransform(mine")
      > paintFile.indexOf("if (m && m.ready)"),
      "the transform is attached inside that, not beside it");

console.log("\nthe pacing holds the early frames and releases the late ones");
const pacer = paint.makePacer({ MAX_MS: 25, SLACK_MS: 2, QUANTILE: 0.95,
                                WINDOW: 120 });
// Twenty frames on the fast path, so a baseline exists and the tail is flat.
for (let i = 0; i < 20; i += 1) pacer.hold(i * 16.7, i * 16.7 + 10);
check(pacer.reserve() === 0,
      "a link with no jitter reserves nothing: " + pacer.reserve());
check(pacer.hold(20 * 16.7, 20 * 16.7 + 10) === 0,
      "and holds nothing back");

// Now a late tail: one frame in five arrives 20ms behind the fastest.
const jittery = paint.makePacer({ MAX_MS: 25, SLACK_MS: 2, QUANTILE: 0.95,
                                  WINDOW: 120 });
for (let i = 0; i < 40; i += 1) {
  jittery.hold(i * 16.7, i * 16.7 + 10 + (i % 5 === 0 ? 20 : 0));
}
check(jittery.reserve() > 0,
      "a link with a late tail reserves something: " + jittery.reserve().toFixed(1));
const early = jittery.hold(41 * 16.7, 41 * 16.7 + 10);
check(early > 0, "a frame on the fastest path is held: " + early.toFixed(1) + "ms");
const already = jittery.hold(42 * 16.7, 42 * 16.7 + 40);
check(already === 0, "one that is already late goes out now");

console.log("\nand the reserve is capped, because a reserve is latency");
const wild = paint.makePacer({ MAX_MS: 25, SLACK_MS: 2, QUANTILE: 0.95,
                               WINDOW: 120 });
for (let i = 0; i < 40; i += 1) wild.hold(i * 16.7, i * 16.7 + 10 + (i % 2) * 400);
check(wild.reserve() <= 25,
      "a link jittering by 400ms still reserves at most 25: " + wild.reserve());

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
