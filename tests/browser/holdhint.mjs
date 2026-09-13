/* Press and hold, and the half second before it becomes a right click.
 *
 * Two reports at once. On a laptop, "I can't click-hold-drag, the cursor just
 * sticks there then does a right click" -- press-and-hold is how a surface
 * with no buttons asks for the right one, and it was armed for a mouse, which
 * has a right button already. And "it would be handy if we had some sort of
 * indicator for a click hold right click that's about to happen".
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const css = readFileSync(new URL("../../web/style.css", import.meta.url), "utf8");
const html = readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

// -- a mouse must never arm it -------------------------------------------
check(/!held\.size && event\.pointerType !== "mouse"/.test(app),
      "press-and-hold is armed for a finger, never for a mouse");
const arm = app.slice(app.indexOf("function cursorWatchForHold"),
                      app.indexOf("function cursorWatchForHold") + 900);
check(!/pointerType/.test(arm),
      "the decision is made once at the press rather than inside the timer");

// -- the countdown is shown, and taken away again -------------------------
// The bar waits before it is drawn, so an ordinary tap never sees one.
const AFTER = Number(/const HOLD_HINT_AFTER = (\d+)/.exec(app)[1]);
const HOLD = Number(/const HOLD_MS = (\d+)/.exec(app)[1]);
check(/cursorHintTimer = setTimeout\(\(\) => \{[\s\S]{0,160}holdHint\(true\)/.test(app),
      `the bar is drawn only after ${AFTER}ms of pressing, not on contact`);
check(AFTER > 0 && AFTER < HOLD,
      `and that wait is inside the hold: ${AFTER} of ${HOLD}ms`);
check(/if \(cursorHoldTimer\) holdHint\(true\)/.test(app),
      "and only while the press is still being counted");
check(/function cursorForgetHold\(\)[^}]*holdHint\(false\)/s.test(app),
      "and goes when the press is abandoned -- a move, or letting go");
check(/cursorHoldTimer = cursorHintTimer = 0;/.test(app),
      "with both timers cleared, so a bar cannot appear after the press ended");
const fire = app.slice(app.indexOf("cursorHoldTimer = setTimeout"),
                       app.indexOf("}, HOLD_MS);"));
check(fire.indexOf("holdHint(false)") < fire.indexOf("return"),
      "and when it fires, before the early return that decides not to click");

// -- the bar must agree with the timer ------------------------------------
check(/setProperty\("--hold-ms", \(HOLD_MS - HOLD_HINT_AFTER\) \+ "ms"\)/.test(app),
      `the sweep covers what is left of the wait -- ${HOLD - AFTER}ms -- so it`
      + " finishes at the click rather than before it");
check(/animation: hold-fill var\(--hold-ms/.test(css),
      "which the stylesheet reads rather than hardcoding");
check(/\.hold-hint \{[^}]*pointer-events: none/s.test(css),
      "the bar takes no taps -- it appears under a finger already pressing");

// -- restarting the sweep on reuse ---------------------------------------
check(/bar\.style\.animation = "none"; void bar\.offsetWidth; bar\.style\.animation = ""/.test(app),
      "and the animation is restarted, since a finished one will not replay");

// -- it exists, and where it can be positioned ---------------------------
check(/id="hold-hint"/.test(html), "the element is in the page");
check(/reduce\)[^}]*\{[^}]*\.hold-hint > i \{ animation: none/s.test(css),
      "somebody who asked for less movement still gets the bar, without the sweep");

console.log("");
if (fails) { console.log(`holdhint: ${fails} FAILED`); process.exit(1); }
console.log("holdhint: all ok");
