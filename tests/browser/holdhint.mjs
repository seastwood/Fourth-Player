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
check(/holdHint\(true\);\s*\n\s*cursorHoldTimer = setTimeout/.test(app),
      "the bar appears when the press starts being counted");
check(/function cursorForgetHold\(\)[^}]*holdHint\(false\)/s.test(app),
      "and goes when the press is abandoned -- a move, or letting go");
const fire = app.slice(app.indexOf("cursorHoldTimer = setTimeout"),
                       app.indexOf("}, HOLD_MS);"));
check(fire.indexOf("holdHint(false)") < fire.indexOf("return"),
      "and when it fires, before the early return that decides not to click");

// -- the bar must agree with the timer ------------------------------------
check(/hint\.style\.setProperty\("--hold-ms", HOLD_MS \+ "ms"\)/.test(app),
      "the sweep is timed from HOLD_MS itself, not a number copied into the CSS");
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
