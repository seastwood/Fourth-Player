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
/* stopPainting's own body, and only that.
 *
 * This used to run to "function startPainting", which is a long way further
 * down and swallowed whatever was declared in between -- so a check that reads
 * "stopPainting does not do X" quietly became "nothing between these two
 * functions does X", and failed when something else was added there. A
 * top-level `function` at column zero is the real end of it. */
const stopAt = app.indexOf("function stopPainting");
const stopBody = app.slice(stopAt,
                           stopAt + app.slice(stopAt + 1).indexOf("\nfunction ") + 1);
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
check(app.includes("paintGaveUp && paintsHere(paintChoice)"),
      "while the note still speaks for what was asked for -- for either way "
      + "of drawing here, since they differ only in how a frame reaches the "
      + "canvas");

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
check(app.includes("if (painter) painter.peek();"),
      "and the watchdog asks for the numbers when it is armed, without "
      + "emptying them");

console.log("a page that is not on screen is not a page that cannot draw");
// Backgrounding a tab stops its animation frames, and browsers take hardware
// decoders back from tabs nobody is looking at. Both reached the painter as
// "it stopped", and after a few of those it concluded the browser could not
// do it -- so minimising and coming back meant choosing the setting again
// every time.
check(app.includes("function watchTheTab"), "the page notices going away");
check(app.includes("paintPaused = true"), "and puts the drawing down");
check(app.includes("document.hidden") && app.includes("watchThePainting();"),
      "the deadline waits rather than judging while nobody is watching");
// Each counter by name rather than by the shape of the block they sit in.
// This used to match the two lines adjacent to each other, and a third
// counter added between them failed a test whose point was that counters get
// reset -- which the change had made more true, not less.
const back = app.slice(app.indexOf("if (!paintPaused) return;"));
for (const counter of ["paintTried", "paintKeyAsks", "paintRecoveries",
                       "paintGoodRuns"]) {
  check(new RegExp(counter + " = 0;").test(back.slice(0, 600)),
        "and coming back resets " + counter);
}
check(/paintGaveUp = false;/.test(back.slice(0, 600)),
      "and stops counting it as given up on");
// Through restartTheDrawing, not startPainting. The drawing was put down while
// the page was away, so by the time it comes back the receiver has been
// delivering to nobody for as long as the page was gone -- and an encoded
// transform attached to a receiver that has started delivers nothing at all.
// It attached, said so, painted nothing for four seconds and handed the
// picture back, so minimising the page and returning to it switched to WebRTC
// every single time.
check(app.includes("paintGaveUp = false;\n    // Not startPainting"),
      "and it is said why this one cannot simply start again");
check(/paintGaveUp = false;[\s\S]{0,600}?restartTheDrawing\(\);/.test(app),
      "the resume goes through restartTheDrawing, which gets a fresh receiver "
      + "where this mode needs one");

console.log("and how many frames to hold is the viewer's to choose");
// The one real trade in drawing it here: every frame held is a frame of
// delay, and every frame held is a hiccup absorbed. Moonlight calls the same
// choice frame pacing, and offers it for the same reason -- different rooms
// want different answers.
check(page.includes('<select id="stream-smooth">'), "there is a Smoothing list");
check(app.includes("SMOOTH_KEY"), "remembered per browser, like the method");
check(app.includes("painter.smooth(want)"),
      "and applied to a painter already running, so it can be heard out "
      + "without a reload");
check(workerSrc.includes("m.smoothing")
      && workerSrc.includes("state.pacer.cap(SMOOTHING)"),
      "the worker takes it, rather than holding a number of its own -- and it "
      + "moves the *cap* on the reserve, so the setting is a ceiling on the "
      + "delay rather than an amount of delay bought whether or not the link "
      + "needs it");

console.log("something that was drawing and stops is started again, not abandoned");
// A decoder can fail in the middle of a working stream -- a picture that
// changes size, a frame that arrives damaged -- and the answer is another
// decoder and a keyframe, not the conclusion that this browser cannot do it.
// Reported as switching itself to WebRTC while WebCodecs was working.
// Asked as two conditions rather than as one source line: a run that drew
// for long enough now gives the recovery budget back before this is tested,
// so the two no longer sit in one expression.
const stopped = app.slice(app.indexOf("if (painter && painter.painted()) {"));
check(/if \(painter && painter\.painted\(\)\) \{/.test(stopped.slice(0, 80)),
      "a method that has painted is treated as one that works");
check(/if \(paintRecoveries < PAINT_RECOVERIES\) \{[\s\S]{0,240}restartTheDrawing\(\);/
        .test(stopped.slice(0, 1400)),
      "and gets started again while it has budget left");
check(/PAINT_RECOVERIES = (\d+)/.test(app), "a bounded number of times");
check(Number(app.match(/PAINT_RECOVERIES = (\d+)/)[1]) <= 5,
      "few enough that a genuinely broken one does not retry for ever");
check(app.includes("paintRecoveries = 0;"),
      "and choosing the method by hand starts that count again");

console.log("a counter that empties when read is not shared between two askers");
// "Drawing 0 of 60 frames a second" over a picture that was playing
// perfectly. The watchdog and the rate report both asked the worker for its
// numbers, and asking emptied them -- so whichever asked second was told
// nothing had happened.
check(workerSrc.includes("if (m.report || m.peek)"),
      "there are two ways to ask");
check(workerSrc.includes("if (m.report) {\n      state.handed"),
      "and only one of them empties the counters");
check(app.includes("painter.peek()"),
      "the watchdog peeks, because it only wants to know if anything is "
      + "happening");
check(app.includes("if (!(rate > 0)) { chip.hidden = true; return; }"),
      "and a window that counted no frames hides the chip rather than "
      + "reporting a zero at somebody");

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

console.log("waiting for a keyframe is not the same as failing");
// Frames arrived, nothing was painted, and it walked the codec list and gave
// up -- having never had anything it could decode. With keyframes sent only
// on request, a decoder that has just started has nothing to decode against
// until one is asked for and granted.
check(paintFile.includes("waitingForKey()"),
      "the painter can say it is waiting rather than failing");
check(workerSrc.includes("keyed: state.started"),
      "which the worker knows, because it is the one that sees the frames");
check(app.includes("painter.waitingForKey() && paintKeyAsks < PAINT_KEY_ASKS"),
      "and the watchdog asks again instead of moving on");
check(/PAINT_KEY_ASKS = (\d+)/.test(app), "a bounded number of times");
check(Number(app.match(/PAINT_KEY_ASKS = (\d+)/)[1]) <= 5,
      "few enough that a real failure is still found in seconds");

console.log("and a failure walks the list rather than giving up on the first");
check(app.includes("function paintNextSpelling"), "there is a next one");
check(app.includes("paintTried += 1"), "the attempts advance");
check(app.includes("paintTried = 0"),
      "and choosing it by hand starts from the top again");

console.log("there is exactly one way back to the browser's element");
check(app.includes("function stopPainting"), "stopPainting exists");
// The support check asks about the chosen method rather than always about
// "here": three modes draw here and they do not need the same things, and
// asking about the wrong one skipped the encoded-transform check that the
// media-track mode depends on.
for (const spot of ["painter.start(pictureChannel, codec)", "if (!codec)",
                    "!paintMethodById(paintMethod).ok()"]) {
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
check(app.includes("if (paintsHere() && !painter) restartTheDrawing()"),
      "and the watchdog keeps trying, so the choice starts when it can -- "
      + "through the restart that knows this mode needs a fresh receiver");
// This one is polled, so the guard matters more here than anywhere: without
// it, a page that had given up would ask the host to rebuild the media
// connection on every stats tick.
// To the end of the function rather than a fixed number of characters: the
// guards moved past a 900-character window when the reason they exist was
// written down above them, and a test that measures its subject in bytes
// fails on the comment explaining it.
const restartAt = app.indexOf("function restartTheDrawing");
const restart = app.slice(restartAt,
                          app.indexOf("\nfunction ", restartAt + 10));
check(restart.includes("paintGaveUp"),
      "and it refuses once this page has given up, so a poll cannot become a "
      + "loop of renegotiations");
check(restart.includes("!paintsHere()"),
      "or once the viewer has chosen the browser");
// Two of the three ways of drawing are done by this page, and everything but
// the last step -- a texture upload against handing the frame to a 2D context
// -- is the same code. Asking "is it 'here'" was true of one and false of the
// other, which would leave the second looking chosen and doing nothing.
check(app.includes("function paintsHere("),
      "and there is one place that knows which methods this page draws");
check(!app.includes('paintMethod !== "here"'),
      "with nothing left comparing against the one id");
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
// Within the function that picks the codec, rather than by position in the
// file: another function has since been added above it that also guards on
// getReceivers, and comparing indexes across the whole file made this a test
// of where things happen to sit.
const picking = app.slice(app.indexOf("const offered = codecFromSdp()"));
check(picking.indexOf("codecFromSdp()")
      < picking.indexOf("pc.getReceivers"),
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

console.log("the frames come down a data channel, not off the media track");
// Taking them off the track needed an encoded transform, and a transform
// wants a receiver that has not started, delivers nothing when attached to
// one that has, and leaves the receiver delivering nothing for ever once
// removed. The client this is modelled on carries whole frames on a data
// channel instead. So does this.
check(app.includes('event.channel.label === "picture"'),
      "the page takes a channel called picture");
check(paintFile.includes('channel.send("on")'),
      "and asks for frames only once there is a decoder for them");
check(paintFile.includes('carrying.send("off")'),
      "and says when to stop, so nothing is sent into nothing");
// And the transform as well, now, as a third way of drawing here rather than
// a replacement. The reasons it was rejected were lifecycle ones and they are
// obeyed rather than argued with: it is attached the moment the track
// arrives, never mid-flight, and never removed. What it buys is the transport
// -- a data channel is SCTP, and SCTP answers a lost packet by *detecting*
// it, which is a second with nothing delivered; ordered, unordered,
// unreliable and time-limited all gave the same second, because they change
// what happens after a loss is found. RTP does not detect loss at all, which
// is why the video line bundled on the same socket has never stalled.
check(paintFile.includes("RTCRtpScriptTransform"),
      "with an encoded transform as the other way in");
check(paintFile.includes("startFromTrack(receiver, codec)"),
      "started from a receiver rather than a channel, everything past the "
      + "first step being the same worker and decoder and pacing");
check(app.includes('if (event.track.kind === "video") startPainting();'),
      "at the one moment it may be attached: a receiver that exists and has "
      + "no frames yet");
// Attached any later it delivers nothing at all, and "later" is measured in
// frames rather than seconds. Waiting for the decoder to be built cost
// exactly that: "the media track's frames were routed to the decoder"
// followed by "0 handed over by the transform", for ever.
check(paintFile.indexOf("viaTrack.transform = new RTCRtpScriptTransform")
      < paintFile.indexOf("it.postMessage({ start:"),
      "and before the worker is even given its canvas, rather than after the "
      + "decoder is built");
// And a receiver that has already carried a picture will take a transform and
// then ignore it, so choosing this mid-session has to rebuild the connection.
check(app.includes('paintMethod === "rtp" && lastBytes > 0'),
      "choosing it on a connection that is already carrying video rebuilds "
      + "that connection first");
check(app.includes("renewSoon(0, true);\n    return;"),
      "and lets the track handler start the painting on the new one");
check(!app.includes("receiver.transform"),
      "and none left in the page either");

console.log("which makes leaving it free");
// "the frame worker is ready and the transform is attached" and then no first
// frame, ever. A transform attached to a receiver already carrying a picture
// delivers nothing; the same receiver on a fresh connection delivers at once.
// It is part of how a receiver is set up rather than something it will take
// mid-flight, which is the mirror of why leaving has to rebuild as well.
check(!stopBody.includes("renewSoon"),
      "no fresh media connection is needed on the way out");
check(stopBody.includes("Nothing to undo on the receiver"),
      "because the media track never knew about any of it");

console.log("and the canvas is put where the video is, not where the stage is");
// The stage is more than the picture: the on-screen controller has the bottom
// of it on a phone, so a canvas stretched over the whole stage centres the
// picture lower than the video was and slides it under the controller.
check(app.includes("function fitPainted"), "the video's own box is measured");
check(app.includes("video.offsetWidth") && app.includes("video.offsetTop"),
      "and copied onto the canvas in pixels");
// getBoundingClientRect reports the box *after* transforms. Zoom the picture
// and it returns the zoomed rectangle; the canvas was sized to that and then
// given the same zoom on top, so it left the screen at twice the scale.
const fitted = app.slice(app.indexOf("function fitPainted"),
                         app.indexOf("function fitPainted") + 600);
check(!fitted.includes("getBoundingClientRect"),
      "from the layout box, which knows nothing about the zoom that is about "
      + "to be applied to both of them");
const fitBody = app.slice(app.indexOf("function fitStage"),
                          app.indexOf("function fitStage") + 800);
check(fitBody.includes("fitPainted"),
      "re-measured by fitStage, which runs on every reason the viewport moves");
// And the reasons the box moves while the viewport stands still: a notice
// above the picture, the on-screen pad arriving, a chip strip growing by a
// line. The video reflows for those and the canvas over it did not.
const watcher = app.slice(app.indexOf("watchingTheBox = new ResizeObserver"),
                          app.indexOf("watchingTheBox.observe(video)"));
check(watcher.includes("fitPainted()"),
      "and by watching the element itself, which asks no questions about why");
check(app.includes("watchingTheBox.observe(video)"),
      "the picture's own box is what is watched");
// The chips and the keyboard's buttons decide how far the letterbox black
// may reach, and they are laid out independently of the picture -- so they
// are watched too, and the picture's own box deliberately is not allowed to
// re-trigger that measurement or it would resize itself for ever.
check(app.includes('watchingTheBox.observe(hud)')
      && app.includes('watchingTheBox.observe(dock)'),
      "along with the furniture the black stops at");
check(watcher.includes("entry.target !== video") && watcher.includes("fitPicture"),
      "with the picture's own box excluded from that half, so it cannot chase "
      + "its own tail");
check(app.includes("typeof ResizeObserver === \"undefined\""),
      "and a browser without one still works, it just does not follow");
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
check(paintFile.indexOf('channel.send("on")') > paintFile.indexOf("if (m.started)"),
      "and the host is asked for frames only once the decoder exists, so "
      + "none arrive before there is anything to decode them");

console.log("\nthe page times the two things only it can see");
// Every measurement so far has been taken in the worker, after the fact, and
// downstream of both candidates: "frames did not arrive" is equally true of a
// channel that stalled and of a thread that could not service one, and those
// want opposite fixes. Several rounds were spent inferring which from numbers
// that could not tell them apart. These two can, because they are timed on
// the page at the moment the channel hands a piece over and at the moment an
// animation frame runs.
check(paintFile.includes("gaps() {"),
      "the painter reports the worst gap between pieces arriving and the "
      + "worst gap between animation frames");
check(paintFile.includes("chunkGap = 0;\n      beatGap = 0;"),
      "cleared as they are read, so each answer describes the window just "
      + "gone rather than the worst since the page loaded");
check(app.includes("worst gap between pieces arriving"),
      "and the report carries both");
// Congestion is random; a radio being time-shared is not. On a Mac that is
// AWDL -- AirDrop, Handoff, AirPlay, Sidecar and Continuity share the Wi-Fi
// chip with the infrastructure link and leave it at periodic availability
// windows -- and it sits below the application, so no amount of changing this
// code touches it. The regularity is the whole signal, and it separates "your
// network is busy" from "something on this machine is taking the radio",
// which the person at the keyboard can actually do something about. The
// freezes here are rate-independent, which is what makes this worth asking.
check(paintFile.includes("function stallPeriod()"),
      "and whether the stalls come at a regular spacing");
check(paintFile.includes("stalls.length < 6"),
      "over enough of them to mean something, because a false 'your machine "
      + "is misbehaving' is worse than staying quiet");
check(paintFile.includes("middle * 0.167"),
      "with every interval close to the median before it is called a "
      + "metronome");

console.log("\nthe timer takes over whenever the beats stop, not only if none came");
// Measured: 244 refreshes in eleven seconds where sixty a second is 660, with
// a 1050ms gap between two paints in the middle. Chrome throttles animation
// frames in a window it thinks is occluded, and a Mac occludes a window
// whenever anything is in front of it. The fallback was guarded on
// `!state.ticked`, which the first tick sets and nothing clears -- so it was
// available to a page that had never sent an animation frame and to no other.
check(!worker.includes("if (!state.ticked && !state.timer) pump();"),
      "the fallback is no longer for a page that never started beating");
// moonlight-web's worker has no animation frames at all -- "rendering is
// driven by decoder output" -- and pays in phase: a paint landing at an
// arbitrary point in the refresh cycle is shown at the next one, so evenly
// spaced paints are not evenly spaced pictures. Both, then: the beat leads
// and the timer is a whole refresh behind it, so a healthy beat always finds
// the frame already painted and a stopped one costs a refresh, not a hang.
check(worker.includes("if (!state.timer) pump();"),
      "the timer is armed whatever the beats are doing");
check(worker.includes("next.due + refresh - performance.now()"),
      "a whole refresh behind the deadline, so it does not race the beat and "
      + "win half the time -- which would be paints landing mid-refresh, the "
      + "unevenness the beats were brought in to remove");
check(worker.includes("const BEAT_GAP_MS"),
      "with a sweep for a queue nothing has armed a timer for");

console.log("\na window behind another window has not gone away");
// Chrome on a Mac calls a window hidden whenever it is occluded -- another
// window in front, a space switching, a full-screen app taking over for a
// moment. This tore the decoder down and rebuilt it every time: the host log
// filled with "the page went away" while somebody sat watching the picture,
// and each one is a stop, a restart, and a black gap waiting for a keyframe.
check(app.includes("PAINT_HIDDEN_MS"),
      "going out of sight starts a clock rather than a teardown");
const hidden = Number((app.match(/const PAINT_HIDDEN_MS = (\d+);/) || [])[1]);
check(hidden >= 10000,
      hidden + "ms: long enough that an occluded window costs nothing, short "
      + "enough that a background tab still lets the decoder go");
check(app.includes("clearTimeout(paintHideTimer)"),
      "and coming back inside that cancels it, having lost nothing");
check(app.includes("document.hidden && paintPaused"),
      "the same judgement where a failure is blamed on the page being away: "
      + "merely occluded is not away");
// A decode error after ten minutes of a good picture is not evidence that
// this browser cannot decode the stream. Counting those against a budget
// fixed at the start of the session means a long session runs out.
check(app.includes("paintRecoveries = 0;\n    setLink(\"ok\");"),
      "and a stream that is working earns its recovery allowance back");

console.log("\na silent media track is not a dead connection");
// The page judged the connection alive by video bytes on the media track, and
// the host now deliberately sends none of those to a guest drawing its own
// picture -- it was sending the picture twice. So the check saw silence,
// called the connection dead, and rebuilt it. Every ten seconds, for ever:
// the repeated black screening and freezes were a renegotiation each time,
// and the rebuilt connection worked perfectly until the next one.
check(paintFile.includes("arrived() { return chunks; }"),
      "the painter says how much has come off the picture channel");
check(paintFile.includes("chunks += 1;"),
      "counted as the pieces arrive, so the answer needs no round trip to "
      + "the worker");
check(app.includes("const drawn = (painter && painter.arrived)"),
      "and the media watchdog asks it");
check(app.indexOf("if (drawn > lastDrawn) {")
      < app.indexOf("if (bytes > lastBytes) {"),
      "before it judges anything by bytes that are not being sent");
check(app.includes("if (lastDrawn && painter) return;"),
      "including the never-carried-anything case -- and only while that "
      + "route is in use, or a page back on the media track could never "
      + "notice a connection that carries nothing");

console.log("\nnothing arriving is not a decoder that cannot decode");
// This is what the flashing was. The rate controller climbed until the data
// channel backed up, frames stopped for a few seconds, and the watchdog read
// the silence as "this browser cannot handle the stream" -- so it walked the
// codec spellings, ran out, and handed the picture back to WebRTC. Which
// resumed the media line, which showed black until a keyframe, at which point
// the page started the painter again and the cycle repeated every few
// seconds.
check(paintFile.includes("starving()"),
      "the painter can say that nothing has arrived at all");
check(paintFile.includes("return Boolean(last && !last.handed);"),
      "which is what nothing handed over means, and says nothing whatever "
      + "about the decoder");
// It used to read `Boolean(last && !last.handed)`, so a painter with no
// counters at all -- `last` is null until the worker's first reply -- answered
// "not starving", and the watchdog took that as a decoder worth doubting.
// That is precisely the state a restart is in when it does not take: an
// encoded transform only delivers on a receiver which has not carried a
// frame, the fresh connection loses that race, and the report reads "0 handed
// over by the transform, 0 fed, 0 came out, canvas none". Seen in the host's
// log after two runs that painted 323 and 309 frames, so the drawing plainly
// worked and only the restart did not.
// And NOT true when there are no counters at all. That was tried and
// reverted within the hour: a transform that attaches and is handed nothing
// sits at null for ever, so the watchdog waited instead of falling back and
// the screen stayed black on every join -- worse than the fallback it was
// meant to avoid. A picture by the other route beats no picture.
check(!paintFile.includes("return !last || !last.handed;"),
      "no information yet is not the same claim as nothing arrived");
check(app.includes("if (painter.starving()) {"),
      "and the watchdog waits rather than blaming it");
check(app.indexOf("if (painter.starving()) {")
      < app.indexOf("const more = paintNextSpelling();"),
      "before it would have walked to the next spelling of the codec");
// But waiting for ever is the other way to leave somebody staring at black.
// The association errored, the host stopped sending on the media line because
// this page had asked for whole frames, and the page waited patiently with
// both routes silent. A picture by the other route beats no picture, always.
check(app.includes('pictureChannel.readyState !== "open"'),
      "a channel that has gone is not something to wait for");
check(app.includes("PAINT_STARVE_MS"),
      "and even an open one is only waited on for so long");
check(app.includes("giveUpPainting();\n      return;\n    }\n    paintSaidStarved"),
      "past which the picture goes back on the video track rather than "
      + "staying black");

console.log("\nthe presentation clock holds a frame for what the link owes it");
// moonlight-web's FramePacer, adopted whole. Every number here is the control
// law rather than a running decoder, which is the point of it being
// clock-injected: nothing reads performance.now(), so this is deterministic.
const L = { MAX: 25 };

const bursty = paint.makePacer(L);
const shown = [];
for (let i = 0; i < 300; i += 1) {
  const captured = i * 16.7;
  // Frames arrive in pairs: one quickly, the next 18ms behind it. That is the
  // shape the reserve exists for -- without one, the late frame leaves the
  // previous picture up an extra refresh and then the pair collapses into a
  // single paint, which is the repeat-then-skip judder.
  shown.push(bursty.schedule(captured, captured + 10 + (i % 2 ? 0 : 18)));
}
const settled = [];
for (let i = 201; i < shown.length; i += 1) settled.push(shown[i] - shown[i - 1]);
const tightest = Math.min(...settled), widest = Math.max(...settled);
check(widest - tightest < 6,
      `the pairs are pulled apart: gaps ${tightest.toFixed(1)}ms to `
      + `${widest.toFixed(1)}ms, against 18ms of arrival burst`);
check(bursty.reserve() >= 15 && bursty.reserve() <= 25,
      `paid for with ${bursty.reserve()}ms of delay, which is the burst`);

console.log("\na clean link waits for nothing at all");
// The deadband is why: a 1-2ms residual hold on a link with no jitter is
// latency bought for nothing, so a negligible target snaps to exactly zero
// and the path is bit-for-bit the immediate one.
const clean = paint.makePacer(L);
let held = 0;
for (let i = 0; i < 300; i += 1) {
  const captured = i * 16.7, arrived = captured + 10;
  if (clean.schedule(captured, arrived) > arrived) held += 1;
}
check(clean.reserve() === 0,
      `nothing reserved on a link with no jitter: ${clean.reserve()}ms`);
check(held === 0, `and not one frame held: ${held}`);

console.log("\nand the reserve is capped, because a reserve is latency");
const wild = paint.makePacer(L);
for (let i = 0; i < 300; i += 1) {
  wild.schedule(i * 16.7, i * 16.7 + 10 + (i % 2) * 400);
}
check(wild.reserve() <= L.MAX,
      `a link jittering by 400ms still reserves at most ${L.MAX}: `
      + wild.reserve());

console.log("\na few late frames do not ratchet it to the cap");
// The trap moonlight-web documents from having measured it: every late frame
// bumped the reserve by 8ms while decay shed 8ms a *second*, so any link with
// a few percent of late frames pinned at the cap -- 24ms of reserve held
// against a p95 late tail of 2.7ms. The bump is capped by the same tail
// estimate the periodic control would use, so it changes when the target
// moves and never where it moves to.
const occasional = paint.makePacer(L);
for (let i = 0; i < 2000; i += 1) {
  // A few milliseconds of ordinary jitter -- enough that the reserve is not
  // zero, because a zero reserve cannot ratchet -- plus one frame in
  // twenty-five arriving 30ms late.
  const ordinary = [0, 3, 5][i % 3];
  occasional.schedule(i * 16.7,
                      i * 16.7 + 10 + ordinary + (i % 25 === 0 ? 30 : 0));
}
check(occasional.reserve() > 0 && occasional.reserve() < 10,
      `4% of frames 30ms late buys the tail, not the cap: `
      + `${occasional.reserve()}ms of ${L.MAX}`);
check(occasional.stats().underruns > 0,
      "and the underruns were seen and counted rather than swallowed: "
      + occasional.stats().underruns);

console.log("\none early frame does not poison it for ever");
const poisoned = paint.makePacer(L);
poisoned.schedule(1, 1);                 // one impossibly fast arrival
for (let i = 1; i < 1200; i += 1) poisoned.schedule(i * 16.7, i * 16.7 + 20);
// It takes a while on purpose. The baseline rises at 3ms a second, so a 20ms
// step is absorbed over about seven seconds -- and that slowness is the point
// of it: a baseline that chased the delay upward would swallow a sustained
// bad patch and report no jitter at all. What must not happen is staying
// pegged, and it does not.
check(poisoned.reserve() === 0,
      `a steady link after one freak arrival settles: ${poisoned.reserve()}ms`);

console.log("\nand a discontinuity rebases instead of pegging the reserve");
// A uint32 wrap, a resolution change, a pipeline restart, a tab that was
// throttled in the background: the transit delay jumps by more than any
// jitter, and adapting to it instead of rebasing holds the cap for the whole
// window.
const jumped = paint.makePacer(L);
for (let i = 0; i < 200; i += 1) jumped.schedule(i * 16.7, i * 16.7 + 10);
for (let i = 200; i < 400; i += 1) jumped.schedule(i * 16.7, i * 16.7 + 9000);
check(jumped.reserve() === 0,
      `nine seconds of step is a new baseline, not jitter: `
      + `${jumped.reserve()}ms`);

console.log("\nthe Smoothing setting moves the cap and nothing else");
const tight = paint.makePacer();
tight.cap(1);
for (let i = 0; i < 300; i += 1) {
  tight.schedule(i * 16.7, i * 16.7 + 10 + (i % 2) * 400);
}
const loose = paint.makePacer();
loose.cap(10);
for (let i = 0; i < 300; i += 1) {
  loose.schedule(i * 16.7, i * 16.7 + 10 + (i % 2) * 400);
}
// With a floor under the cap, because one frame mapped to 8ms and a link
// whose late tail was 18 to 26ms was then capped at 8 -- the pacer could not
// cover what it had measured, and the setting meant for "least delay"
// produced the most judder.
check(tight.reserve() < loose.reserve() && tight.reserve() <= 25,
      `one frame of smoothing holds ${tight.reserve()}ms and ten holds `
      + `${loose.reserve()}ms on the same link`);
check(tight.reserve() >= 20,
      `and the lowest setting is still not tighter than a real link's `
      + `jitter: ${tight.reserve()}ms`);

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
