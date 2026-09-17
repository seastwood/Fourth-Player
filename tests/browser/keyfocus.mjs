/* Using a control while the on-screen keyboard is up.
 *
 * Reported on iOS: with the keyboard button enabled, the controller dropdown
 * would not open, and nor would any other setting.
 *
 * The transparent field that raises the keyboard takes focus back whenever it
 * loses it, so that a phone putting its keyboard away for its own reasons does
 * not close it under somebody mid-sentence. But a tap on a dropdown also
 * blurs that field -- and taking focus straight back cancelled the tap.
 *
 * The two are only distinguishable after the browser has moved focus:
 * activeElement is not updated when blur fires, and a <select> on iOS fills in
 * no relatedTarget at all.
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

const blur = app.slice(app.indexOf('field.addEventListener("blur"'),
                       app.indexOf("function deskAfterBlur"));
check(blur.length > 100, "the blur handler was found");

console.log("focus is left alone when it went to something somebody tapped");
check(/const going = event && event\.relatedTarget;/.test(blur),
      "the element receiving focus is looked at");
check(/if \(going && going !== field\) return;/.test(blur),
      "and focus is not taken back from it");

console.log("\nand the case iOS does not report is caught a moment later");
// A <select> on iOS gives no relatedTarget, so relatedTarget alone is not
// enough and checking activeElement inside blur is too early -- it is still
// the field at that point.
check(/setTimeout\(\(\) => \{/.test(blur), "it looks again a turn later");
check(/const now = document\.activeElement;/.test(blur),
      "at what actually has focus by then");
check(/if \(now && now !== document\.body && now !== field\) return;/.test(blur),
      "and leaves it alone if that is a real control");
check(/deskAfterBlur\(field\)/.test(blur),
      "otherwise the old behaviour runs, which is the phone having dropped it");

console.log("\nleaving the field is not leaving the keyboard");
// deskWantKeyboard is the intent, and none of this touches it: finishing with
// the dropdown and tapping the field brings the keyboard straight back.
check(!/deskWantKeyboard = false/.test(blur),
      "the blur handler never decides the keyboard is unwanted");

console.log("\nand the comment no longer claims to be immediate");
// Searched forward from deskAfterBlur: "const row = el(\"desk-keys\")" also
// appears earlier in the file, and the first version of this sliced backwards
// and measured an empty string.
const afterAt = app.indexOf("function deskAfterBlur");
const after = app.slice(afterAt,
                        app.indexOf('const row = el("desk-keys")', afterAt));
check(/one task later/.test(after),
      "it says it runs a task later, because it does -- a comment that says "
      + "'straight away' over code that waits is worse than no comment");
check(/deskRefocus < 12/.test(after),
      "and the loop guard survived the move");

process.exit(fails ? 1 : 0);
