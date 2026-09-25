/* A codec the browser receives but cannot draw with.
 *
 * Two different questions, and only one of them was ever asked. The host is
 * told what this browser can decode, from RTCRtpReceiver.getCapabilities --
 * which describes the browser's own video pipeline. A guest drawing its own
 * picture decodes through WebCodecs instead, and the two do not have to
 * agree.
 *
 * On iOS 18.7 they do not. Safari plays H.265 on a <video> element happily
 * and its WebCodecs H.265 decoder dies with EncodingError on a delta frame,
 * after anything from twenty to five hundred good ones, in all four spellings
 * of the codec string (hvc1/hev1 x 1.6/1.4). Measured over WireGuard with the
 * host reporting "sent 600 frames and is 0 bytes behind, having skipped 0",
 * so it is not loss.
 *
 * The host agreed on H.265 in good faith, every drawing mode failed, and the
 * page handed the picture back to the browser -- which worked, because that
 * was the pipeline that could decode it all along. Reported as "webcodecs
 * just gives a blank screen and reverts to webrtc".
 */
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
let bad = 0;
const check = (cond, what) => {
  console.log((cond ? "  ok   " : "  FAIL ") + what);
  if (!cond) bad += 1;
};

console.log("what cannot be drawn with is remembered");
check(/const BAD_DRAW_CODECS_KEY = /.test(src),
      "there is somewhere to write it down");
check(/localStorage\.setItem\(BAD_DRAW_CODECS_KEY/.test(src),
      "and it survives a reload -- it is a fact about the browser, not the "
      + "session, and learning it again each time means failing again each "
      + "time");
check(/catch \(_\) \{ \/\* private window/.test(src),
      "a private window that refuses storage does not break the page");

console.log("\nand the host is told the narrower truth while the page draws");
const fn = src.slice(src.indexOf("function videoCodecs()"));
const body = fn.slice(0, fn.indexOf("\n}"));
check(/drawing !== "browser" && badDrawCodecs\.size/.test(body),
      "only while the page is drawing: the browser decodes it perfectly well, "
      + "and hiding that would cost everybody a better codec for a limit "
      + "that is not theirs");
check(/if \(left\.length\) return left;/.test(body),
      "and never all of them -- an empty list reads as 'give me H.264', and "
      + "something that might not work beats nothing at all");
check(/try \{ drawing = paintChoice; \} catch \(_\)/.test(body),
      "reading the choice cannot throw: this runs on the join path, and "
      + "paintChoice is declared further down the file");

console.log("\nand exhausting the spellings asks for another codec");
const give = src.slice(src.indexOf("const more = paintNextSpelling();"));
const near = give.slice(0, 2000);
check(/const family = drawCodecFamily\(videoCodecNow\(\)\.mime\);/.test(near),
      "which codec failed is worked out from what is actually being sent");
check(/if \(family && ruleOutDrawCodec\(family\)\)/.test(near),
      "it is ruled out once -- every spelling failing says something about "
      + "the codec, not about how it was named");
check(/reviveNow\(/.test(near),
      "and the connection is rebuilt so the host can agree on another");
check(/left\.indexOf\(family\) < 0/.test(near),
      "only when there is genuinely another to move to");
check(near.indexOf("giveUpPainting();") > near.indexOf("reviveNow("),
      "giving up is what happens after that, not instead of it");

console.log("\nand ruling one out twice is not a loop");
const rule = src.slice(src.indexOf("function ruleOutDrawCodec"));
check(/if \(!name \|\| badDrawCodecs\.has\(name\)\) return false;/
        .test(rule.slice(0, 400)),
      "the second attempt at the same codec answers no, so the reconnect "
      + "happens once and the next connection is offered the other one from "
      + "the start");

console.log("\nand a picture that works and interrupts is not a broken one");
/* Measured on the host's own logs: 186 decoder failures in one evening at
 * home, every one EncodingError on a delta with the parameter sets unchanged,
 * each attempt lasting 193 to 602 frames. Invisible, because each recovery
 * cost a second and the picture came back. The same fault away from home
 * kills an attempt every twenty frames, the three recoveries are gone in
 * seconds, and it hands back to WebRTC -- which is the whole of "it worked at
 * home and does not work here". It is the same fault in both places. */
const good = src.slice(src.indexOf("const PAINT_GOOD_RUN_MS"));
check(/const PAINT_GOOD_RUN_MS = (\d+);/.test(good.slice(0, 200)),
      "there is a length that counts as having worked");
check(/const PAINT_GOOD_RUNS = (\d+);/.test(good.slice(0, 200)),
      "and a bound on how often that may buy the budget back -- a picture "
      + "dying every four seconds for ever is a worse offer than the browser "
      + "drawing it");
const stop = src.slice(src.indexOf("if (painter && painter.painted()) {"));
check(/const ranFor = paintRunFrom \? Date\.now\(\) - paintRunFrom : 0;/
        .test(stop.slice(0, 1200)),
      "how long the run lasted is measured");
check(/if \(ranFor >= PAINT_GOOD_RUN_MS && paintGoodRuns < PAINT_GOOD_RUNS\)/
        .test(stop.slice(0, 1200)),
      "a long enough run gives the recovery budget back");
check(/paintRecoveries = 0;/.test(stop.slice(0, 1200)),
      "so a hiccup after ten good minutes does not get answered by trying a "
      + "different codec string");
check(/paintRunFrom = Date\.now\(\);/.test(
        src.slice(src.indexOf("function restartTheDrawing"), 
                  src.indexOf("function restartTheDrawing") + 400)),
      "and each restart begins a new run");

console.log("\nand a report says which code it came from");
/* It did not move all session, so a report from the old page and a report
 * from the new one were indistinguishable -- which cost a round of "is this
 * even deployed?" in the middle of a diagnosis. */
const stamp = (/const CLIENT_BUILD = "([^"]+)";/.exec(src) || [])[1];
check(!!stamp && stamp !== "2026-09-10i",
      "the build stamp moved with the change: " + stamp);

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
