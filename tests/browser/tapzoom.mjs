/* Double-tapping the picture to zoom into what was tapped.
 *
 * A phone shows a television about as wide as two fingers, and the part
 * somebody wants is often a corner of it. The zoom control exists, but
 * reaching for it means stopping playing; a tap on the thing itself does not.
 *
 * Three things here are easy to get wrong and awkward to find by tapping a
 * phone, which is why they are a decision over plain numbers:
 *
 *   * Two taps close in time, but not so close that they are one press
 *     arriving twice -- the trap the on-screen sticks fell into, where iOS's
 *     compatibility mouse event turned every single tap into a pair.
 *   * Near enough in space to be one gesture. Two deliberate taps at opposite
 *     corners are two taps.
 *   * Not while anything is being driven: with the keyboard or pointer live, a
 *     double tap is a double *click* somebody is sending to the machine, and
 *     swallowing it would take away the gesture that opens everything on a
 *     desktop.
 */
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
let bad = 0;
const check = (cond, what) => {
  console.log((cond ? "  ok   " : "  FAIL ") + what);
  if (!cond) bad += 1;
};

// Just the rule, not the listener that uses it: the listener touches the DOM
// and this runs in node.
const body = src.slice(src.indexOf("const TAP_ZOOM_MS"),
                       src.indexOf("/* Watched on pointerup"));
const F = new Function(body + `; return {
  isPictureDoubleTap, MS: TAP_ZOOM_MS, MIN: TAP_ZOOM_MIN_MS,
  SLOP: TAP_ZOOM_SLOP, TO: TAP_ZOOM_TO };`)();
const tap = (x, y, at, last) => F.isPictureDoubleTap(x, y, at, "touch", last);
const first = { x: 100, y: 100, at: 1000, kind: "touch" };

console.log("two quick taps in the same place");
check(tap(100, 100, 1000 + 150, first) === true, "are a double tap");
check(tap(100, 100, 1000, null) === false,
      "and the first one alone is not -- there is nothing to pair it with");

console.log("\nbut not two taps somebody meant separately");
check(tap(100, 100, 1000 + F.MS + 1, first) === false,
      "past " + F.MS + "ms they are two taps");
check(tap(400, 300, 1000 + 150, first) === false,
      "and two far apart are two taps, however quick");

console.log("\nnor one press arriving twice");
// The fault the on-screen sticks had: iOS sends a compatibility mouse event
// after a touch, and every single tap became a pair.
check(tap(100, 100, 1000 + 10, first) === false,
      "ten milliseconds apart is a duplicate, not a gesture");
check(tap(100, 100, 1000 + F.MIN + 1, first) === true,
      "while just past the minimum is real");

console.log("\nand it is forgiving, which is the point");
// The first version used the on-screen sticks' numbers, and that was the
// wrong place to copy from: a stick is a small well with a thumb already on
// it, so both taps land close and fast. Tapping a picture is a different
// motion, and somebody choosing what to zoom into is not hurrying.
check(F.MS >= 450,
      "there is time to think between the taps: " + F.MS + "ms");
check(F.SLOP >= 80,
      "and room for a hand that travels: " + F.SLOP + " pixels");
check(tap(100, 100, 1000 + 420, first) === true,
      "a slow double tap still counts");
check(tap(100 + 70, 100 + 40, 1000 + 300, first) === true,
      "and one where the second lands a thumb's width away");

console.log("\nthe slop is generous but bounded");
// A thumb does not land twice in the same place; two things worth zooming at
// are much further apart than this.
check(tap(100 + F.SLOP, 100, 1000 + 150, first) === true, "just inside counts");
check(tap(100 + F.SLOP + 1, 100, 1000 + 150, first) === false,
      "just outside does not");
check(tap(100, 100 + F.SLOP, 1000 + 150, first) === true,
      "and it is a radius rather than one axis");

console.log("\nand one press arriving twice is not a gesture either");
// iOS sends a compatibility mouse event after a touch. Without this every
// single tap on the picture would look like a pair -- the same fault the
// on-screen sticks had.
check(F.isPictureDoubleTap(100, 100, 1000 + 150, "mouse", first) === false,
      "a mouse event after a touch is not the second half of it");
check(F.isPictureDoubleTap(100, 100, 1000 + 150, "touch", first) === true,
      "while a real second touch is");

console.log("\nwhat the gesture does");
// Watched on pointerup rather than click: while the picture is zoomed, every
// pointermove calls preventDefault to stop the page scrolling under a drag,
// and that suppresses the click the browser would otherwise synthesise. So
// the tap that should zoom back out could never arrive.
const handler = src.slice(src.indexOf("function pictureTapEnded"),
                          src.indexOf('let zoomedByTap'));
check(/video\.addEventListener\("pointerup", pictureTapEnded\);/.test(src),
      "it listens on pointerup, which fires whether or not a click does");
check(/if \(cursorDriving\(\)\) \{ lastPictureTap = null; return; \}/
        .test(handler),
      "nothing happens while the keyboard or pointer is live");
check(/if \(dragged\) \{[\s\S]{0,160}lastPictureTap = null;/.test(handler),
      "and a drag is not a tap -- pointerup arrives after the move handlers, "
      + "so `dragged` is settled by the time this reads it");
check(/const others = held\.size - \(held\.has\(event\.pointerId\) \? 1 : 0\);/
        .test(handler),
      "and it counts every finger but this one: this listener runs before the "
      + "one that forgets the pointer, so held still contains the finger that "
      + "is lifting -- asking for held.size alone answered 'one' for every "
      + "single tap and returned");
check(/if \(others > 0\) \{/.test(handler),
      "so letting go of one of two fingers is still not a tap");
check(/zoom > ZOOM_MIN\) zoomAbout\(ZOOM_MIN/.test(handler),
      "zoomed in, a double tap goes all the way back out");
check(/else zoomAbout\(TAP_ZOOM_TO, x, y\)/.test(handler),
      "and zoomed out, it goes in towards the point that was tapped");
check(F.TO > 1 && F.TO < 4,
      "to somewhere with room left to pinch further: " + F.TO);
check(/lastPictureTap = null;\s*\/\/ spent/.test(handler),
      "and the pair is spent, so three taps are one zoom and not two");

console.log("\nand a compatibility mouse event does not break the pair");
/* The fault that made this gesture feel like it needed to be quicker and
 * better aimed, when neither was true.
 *
 * iOS follows a touch with a compatibility mouse event. It cannot be the
 * second half of a touch -- checked above -- but it used to be written into
 * `lastPictureTap` on its way past, so a real double tap arrived as touch,
 * mouse, touch and the second touch was compared against the mouse. Every
 * double tap failed. Read as a rule over the record rather than by tapping a
 * phone, because tapping a phone is how it went unfound. */
check(/if \(lastPictureTap && \(lastPictureTap\.kind \|\| ""\) !== kind/
        .test(handler),
      "an event of another kind does not overwrite a live record");
check(/&& at - lastPictureTap\.at <= TAP_ZOOM_MS\) \{/.test(handler),
      "though a stale one is replaced -- a phone put down and a mouse picked "
      + "up is not one gesture");

console.log("\nand a thumb resting on glass is still a tap");
/* The other half. While the picture is zoomed, one finger pans it, and that
 * branch called any movement at all a drag -- which discarded the tap and
 * wiped the one before it, so a double tap could not be landed while zoomed
 * at all. */
const pan = src.slice(src.indexOf("  if (zoom > ZOOM_MIN) {\n    panX"));
check(/panMoved \+= Math\.hypot/.test(pan.slice(0, 900)),
      "how far the finger pushed the picture is measured");
check(/if \(panMoved >= PAN_SLOP\) dragged = true;/.test(pan.slice(0, 900)),
      "and only past a threshold is it a drag rather than a wobble");
check(/panMoved = 0;/.test(src.slice(src.indexOf("dragged = false;\n    panMoved"))),
      "reset when a new finger lands, so one drag does not spoil the next tap");
const slop = Number((/const PAN_SLOP = (\d+);/.exec(src) || [])[1]);
check(slop > 0 && slop <= 20,
      "and it forgives a thumb, not a nudge: " + slop + " pixels");

console.log("\nand it says which guard refused a tap");
/* Because every cheap explanation for "zooming out is reliable and zooming in
 * is finicky" has been checked and ruled out: the thresholds, iOS's
 * compatibility mouse event, the drag slop, pointercancel, touch-action on
 * the picture, pointer-events on the canvas over it, and the zoom clamp. What
 * is left needs the phone to say which guard it hit. */
check(/function tapRefused\(why\)/.test(src),
      "there is one place that says so");
check(/if \(now - tapWhyAt < 3000\) return;/.test(src),
      "rarely, so a gesture cannot flood the host's log");
for (const why of ["other finger", "it moved, so it was a drag",
                   "read as one press twice", "too slow to be one gesture",
                   "too far to be one gesture"]) {
  check(src.includes(why), "and names it: " + why);
}

console.log("\nand a tap the system took away still counts");
/* iOS hands a pointer to its own gesture recogniser and sends pointercancel
 * instead of pointerup -- far more readily for a quick tap than a slow
 * deliberate one. That is the whole of "it works if I double tap slowly": the
 * cancelled first tap was never recorded, so the second had nothing to pair
 * with. */
check(/video\.addEventListener\("pointercancel", pictureTapEnded\);/.test(src),
      "and on pointercancel, which is the same tap ended a different way");
// A pointer gets exactly one of the two, and which one is not the page's
// business: iOS cancels a touch far more readily for a quick tap than a slow
// deliberate one. They used to differ -- a cancel only *remembered* the tap
// instead of completing the pair -- so a cancelled second tap did not zoom
// and the record it left paired with whatever came next. Reported as zooming
// out working well and zooming in being finicky, and as the picture flashing
// in and out at the wrong moment.
check(src.indexOf('video.addEventListener("pointerup", pictureTapEnded);')
      < src.indexOf('video.addEventListener("pointerup", letGoOfPicture)'),
      "both before the handler that forgets the pointer, or `held` would "
      + "already have lost the finger that is leaving");
check(src.indexOf('video.addEventListener("pointercancel", pictureTapEnded);')
      < src.indexOf('video.addEventListener("pointercancel", letGoOfPicture)'),
      "for the cancelled one too");

console.log("\nand a fast double tap is not rejected as a duplicate");
check(F.MIN <= 20,
      "the minimum gap is low: " + F.MIN + "ms -- the pointer-kind check "
      + "keeps out iOS's compatibility mouse event now, so this only has to "
      + "reject the same pointer reported twice");
check(tap(100, 100, 1000 + 60, first) === true, "60ms apart is a double tap");
check(tap(100, 100, 1000 + 25, first) === true, "and so is 25ms");

console.log("\nthe thresholds stayed generous");
check(F.MS >= 600, "asked for twice, so there is real time: " + F.MS + "ms");
check(F.SLOP >= 120, "and real room: " + F.SLOP + " pixels");

console.log("\nand the hud is not toggled by the tap that zoomed");
const click = src.slice(src.indexOf('el("screen").addEventListener("click"'));
check(/if \(zoomedByTap\) \{ zoomedByTap = false; return; \}/
        .test(click.slice(0, 600)),
      "the click that follows a zooming tap is spent on the zoom");
check(/zoomedByTap = true;/.test(handler),
      "which the gesture says by setting it");

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
