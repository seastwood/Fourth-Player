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

console.log("\nthe codec string is read from the connection, not guessed");
check(paint.codecFrom("video/H264", "profile-level-id=640c1f;packetization-mode=1")
      === "avc1.640C1F", "H.264 takes profile-level-id straight from the fmtp");
check(paint.codecFrom("video/H264", "") === "avc1.42E01F",
      "with no fmtp it is constrained baseline, which is the convention");
check(paint.codecFrom("video/H265", "profile-id=1;level-id=93") === "",
      "H.265 is refused rather than guessed at -- a wrong string is a black "
      + "picture with no message");
check(paint.codecFrom("", "") === "", "and so is nothing at all");

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
