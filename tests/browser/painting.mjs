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
const paintFile = readFileSync(new URL("../../web/paint.js", import.meta.url), "utf8");
const css2 = readFileSync(new URL("../../web/style.css", import.meta.url), "utf8");
const worker = readFileSync(new URL("../../web/frames.js", import.meta.url), "utf8");
const stopBody = app.slice(app.indexOf("function stopPainting"),
                           app.indexOf("function startPainting"));
const stopBodyFor = (what) => stopBody.includes(what);

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

console.log("and it always shows the mode that is in force");
// It had three meanings at once -- the choice, "follow the host", or the
// fallback, depending on how it got there -- so the one question anybody
// asks of it could not be answered by it.
check(app.includes("box.value = paintMethod;"),
      "the effective mode is what is selected");
check(!app.includes('follow.value = ""'),
      "there is no 'follow the host' entry to disagree with the screen");
const gaveUp = app.slice(app.indexOf("paintGaveUp = true"),
                         app.indexOf("paintGaveUp = true") + 400);
check(gaveUp.includes("paintMethod = PAINT_METHODS[0].id"),
      "giving up makes the browser the mode, because it is what is drawing");
check(app.includes('paintGaveUp && paintChoice === "here"'),
      "while the note still speaks for what was asked for");

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
const workerSrc = worker;
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

console.log("a remembered choice that does not work gives the picture back");
// The complaint: the option is stored per browser, so a page that had it on
// came up black on every load with nothing to be done from the chair.
// isConfigSupported saying yes is not the same as a decoder that works -- on
// iOS it says yes and then fails -- so the only honest test is whether
// anything was painted.
check(app.includes("PAINT_PROVE_MS"), "there is a deadline to show something");
check(/PAINT_PROVE_MS = (\d+)/.test(app), "and it is a named number");
check(Number(app.match(/PAINT_PROVE_MS = (\d+)/)[1]) <= 6000,
      "short enough that nobody sits in front of a black screen wondering");
check(app.includes("painter.painted()"), "the painter is asked, not the canvas");
check(paintFile.includes("painted() { return ever || Boolean(last && last.ever); }"),
      "answered from what the worker said, not by measuring the canvas -- an "
      + "untouched one is 300x150 and would have said yes");
check(workerSrc.includes("self.postMessage({ painted: true })"),
      "and the worker says it the moment it happens, not when next asked: a "
      + "deadline reached before the first report had nothing to read");
check(app.includes("if (painter) painter.report();"),
      "and the watchdog asks for the numbers when it is armed");

console.log("and the two methods are named after what does the work");
// "Browser" and "this page" said who to blame, which is not a distinction
// anybody can act on: both run in the browser and both are this page.
check(app.includes('label: "WebRTC (default)"'), "WebRTC");
check(app.includes('label: "WebCodecs"'), "and WebCodecs");
check(readFileSync(new URL("../../web/setup.js", import.meta.url), "utf8")
        .includes('{ browser: "WebRTC", here: "WebCodecs" }'),
      "and the setup page says the same two words");
check(app.includes("there is nothing else to try"),
      "when the spellings run out it says so");
// It used to call setPaintMethod("browser") here, which writes the choice
// down -- so a failed attempt silently replaced what somebody had picked.
// A toast fades in seconds and is easy to be looking away from: this
// happened three times before it was noticed at all. The notice panel stays
// until it is dismissed and says what to do about it.
check(app.includes('showNotice("<b>Drawing it on this page produced no picture'),
      "the fallback says so on screen, in the notice rather than a toast");
check(app.includes("reloading tries again"),
      "and says what to do about it");
check(app.includes("paintGaveUp = true"),
      "and gives up on this connection rather than unchoosing the method");
check(app.slice(app.indexOf("there is nothing else to try") - 400,
                app.indexOf("there is nothing else to try") + 400)
         .indexOf('setPaintMethod("browser")') < 0,
      "the remembered choice is not overwritten by a failure");
check(app.includes("if (paintGaveUp) return;"),
      "and nothing retries in a loop in between");
check(app.includes("paintGaveUp = false;\n  paintTried = 0;"),
      "a fresh media connection is a fresh chance");

console.log("and a retry keeps the transform, because it is attached once");
// Every retry read "0 fed to the decoder" while megabytes arrived: taking
// the worker away and attaching another transform to the same receiver left
// nothing delivering frames, so the second and third attempts could not have
// worked whatever was wrong with the first.
check(app.includes("function tryAnotherSpelling"), "there is a retry path");
check(!app.includes("function restartPainting"),
      "and it is not a restart any more");
check(paintFile.includes("useCodec(codec)"),
      "the painter rebuilds only the decoder");
check(app.includes("painter.useCodec(codec)"), "and the page asks it to");

console.log("and a failure walks the list rather than giving up on the first");
check(app.includes("function paintNextSpelling"), "there is a next one");
check(app.includes("paintTried += 1"), "the attempts advance");
check(app.includes("paintTried = 0"),
      "and choosing it by hand starts from the top again");

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

console.log("the codec is read from the offer, which is the agreement itself");
// A receiver's getParameters() came back empty and so did getStats'
// sdpFmtpLine, so the page fell back to constrained baseline at level 3.1 --
// and configured a decoder for a ceiling far below the 1440p60 it was about
// to be handed. That is what "Decoder failure" was.
check(app.includes("function codecFromSdp"), "the offer is parsed");
check(app.includes("lastSdp = String(message.sdp"), "and kept when it arrives");
check(app.indexOf("const offered = codecFromSdp()")
      < app.indexOf("if (!pc || !pc.getReceivers)"),
      "and asked before the browser's own accessors, not after them");

console.log("and the level asked for is a ceiling, so a high one is offered too");
const low = paint.codecCandidates("video/H264", "profile-level-id=42e01f");
check(low.length === 2 && low[1] === "avc1.42E034",
      "the same profile at 5.2 follows the negotiated one: " + low.join(", "));
const high = paint.codecCandidates("video/H264", "profile-level-id=4D0033");
check(high[0] === "avc1.4D0033" && high[1] === "avc1.4D0034",
      "main profile keeps its profile bytes and raises only the level: "
      + high.join(", "));

console.log("\nand the video element gives up its decoder while we hold one");
// iOS allows very few video decoders at once, few enough that one is a
// realistic number, and hiding the element does not release the one it holds.
check(app.includes("function keepAudioOnly"),
      "the element is handed the sound and nothing else");
check(app.includes("getAudioTracks"), "the audio tracks are kept, not dropped");
check(app.includes("function giveTheVideoBack"), "and it is given back");
check(app.indexOf("giveTheVideoBack()") < app.indexOf("canvas.hidden = true"),
      "on the way out, before the canvas is hidden");
check(app.includes("video.srcObject = whole;"),
      "a browser that refuses a hand-built stream keeps the one it had");

console.log("switching back rebuilds the connection, because nothing less works");
// Measured after switching back: "0.0 frames a second (0.0 arrived)" and the
// element pausing itself over and over. WebRTC's receiver had stopped
// delivering to its own decoder and setting receiver.transform to null did
// not undo that -- a receiver that has had a transform taken off it cannot be
// relied on again.
check(stopBodyFor("renewSoon(0, true)"),
      "a fresh media connection is asked for");
check(stopBodyFor("if (was) {"),
      "but only when this page was really drawing, so an ordinary "
      + "renegotiation does not recurse into another one");

console.log("switching back asks for a keyframe, or it stays black");
// The browser's own decoder has had nothing for as long as the transform was
// attached, so it has no reference frame to decode against -- and with
// keyframes sent only on request, nothing asks on its behalf. Reported as
// switching back needing a page refresh.
check(stopBodyFor("receiver.transform = null"),
      "and the transform comes off first, whatever else follows");

console.log("and the decoding, pacing and painting all happen off this thread");
// About twenty frames a second reached the canvas out of sixty, with the
// decoder and the painting both reporting themselves healthy, because their
// work was queued behind everything else the page does. A 1440p frame drawn
// on the main thread sixty times a second is not something to ask of a page
// a game is being played through.
check(worker.includes("new VideoDecoder"), "the decoder is in the worker");
check(worker.includes("drawImage"), "and so is the painting");
check(worker.includes("makePacer"), "and the pacing");
check(worker.includes('importScripts("/static/paint.js")'),
      "sharing one copy of the pacing and the bitstream code, not two");
check(paintFile.includes("transferControlToOffscreen"),
      "the canvas is handed over once");
check(paintFile.includes("function freshCanvas"),
      "and replaced each attempt, because it can only be handed over once");
check(worker.includes("decodeQueueSize >= QUEUE_MAX"),
      "a saturated decoder is not given more");
check(worker.includes("state.stale += 1"),
      "and a frame with a newer one behind it is dropped rather than painted");

console.log("switching back gives the receiver its transform back");
// Terminating the worker is not enough: while a transform is attached every
// frame goes to it and none is written back, so the browser's own decoder is
// starved whether anything is reading or not. Leaving it there made the
// browser option a permanently black picture -- and it was the last place
// anybody would look, since nothing about that path had changed.
check(app.includes("receiver.transform = null"),
      "the transform is taken off on the way out");

check(stopBody.indexOf("receiver.transform = null")
      < stopBody.indexOf("giveTheVideoBack()"),
      "before the stream is handed back, so nothing is starved in between");

console.log("the canvas that is shown is the one that was handed over");
// start() replaces the element -- a canvas can only be given to a worker
// once -- so anything done to the old reference afterwards is done to a node
// no longer in the page. The visible canvas was never unhidden: a black
// rectangle over a video element whose picture had been taken away.
const startBody = app.slice(app.indexOf("async function startPainting"),
                            app.indexOf("function askHostForKeyframe"));
check(startBody.includes("const drawnOn = paintCanvas();"),
      "the element is read again after start()");
check(startBody.indexOf("drawnOn.hidden = false")
      > startBody.indexOf("painter.start(receiver, codec)"),
      "and it is the one that is shown");
check(startBody.indexOf("canvas.hidden = false")
      < startBody.indexOf("painter.start(receiver, codec)"),
      "while the handover gets a canvas that is already visible, which is one "
      + "less thing for a browser to decline to composite");

console.log("and the canvas is put where the video is, not where the stage is");
// The stage is more than the picture: the on-screen controller has the bottom
// of it on a phone, so a canvas stretched over the whole stage centres the
// picture lower than the video was and slides it under the controller.
check(app.includes("function fitPainted"), "the video's own box is measured");
check(app.includes("video.getBoundingClientRect()")
      && app.includes("canvas.style.width = mine.width"),
      "and copied onto the canvas in pixels");
const fitBody = app.slice(app.indexOf("function fitStage"),
                          app.indexOf("function fitStage") + 800);
check(fitBody.includes("fitPainted"),
      "re-measured by fitStage, which runs on every reason the stage moves");
const overRule = css2.slice(css2.indexOf("#painted.over {"),
                            css2.indexOf("}", css2.indexOf("#painted.over {")));
check(overRule.indexOf("width: 100%") < 0,
      "and the rule does not stretch it over the stage any more");

console.log("the canvas goes over the video, never instead of it");
// Everything that reads the picture reads the <video> element: twenty-odd
// pointer, wheel and gesture handlers, the zoom's geometry, and
// requestPointerLock, which is how the keyboard and mouse are taken. A
// display:none element accepts none of that -- so the element stays where it
// is, showing black with its video track removed, and the canvas is laid on
// top and takes no pointer events at all.
check(!app.includes("video.hidden = true"),
      "the video element is never hidden while painting");
check(app.includes('canvas.classList.add("over")'), "the canvas is laid over it");
check(app.includes('canvas.classList.remove("over")'), "and taken off again");
const css = css2;
const over = css.slice(css.indexOf("#painted.over {"),
                       css.indexOf("}", css.indexOf("#painted.over {")));
check(over.includes("position: absolute"), "positioned over the stage");
check(over.includes("pointer-events: none"),
      "and deaf to pointers, which is the whole of why the handlers below "
      + "still work");
check(/z-index:\s*1/.test(over), "and above the video, not behind it");

console.log("and it moves with the picture when that is zoomed or dragged");
check(app.includes("canvas.style.transform = how"),
      "the same transform is written to both");
check(app.indexOf("video.style.transform = how")
      < app.indexOf("canvas.style.transform = how"),
      "from one place, so they cannot drift apart");

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
const paintSrc = paintFile;
check(worker.includes("state.skipped += 1"),
      "deltas before the first keyframe are counted and dropped");
check(worker.includes("state.started = true"), "and the gate opens on a keyframe");
check(worker.includes("state.started = false"), "and closes again on a retry");

console.log("and every stage of it is counted, because they fail alike");
for (const one of ["fed", "out", "drawn", "refused", "skipped", "handed"]) {
  check(new RegExp("\\b" + one + "\\b").test(worker), `${one} is counted`);
}
check(paintFile.includes("came out") && paintFile.includes("painted"),
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
check(worker.indexOf("self.onrtctransform") < worker.indexOf("ready: true"),
      "the worker registers its handler before it says it is ready");
check(paintFile.includes("if (m.ready)"),
      "and the page waits for that before attaching anything");
check(paintFile.indexOf("new RTCRtpScriptTransform(it")
      > paintFile.indexOf("if (m.ready)"),
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
