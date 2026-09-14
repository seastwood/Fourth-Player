/* Not focusing a text field on a machine that has a real keyboard.
 *
 * Reported as: holding W on a MacBook shows the accent panel -- the other
 * language characters -- over the game, on both Safari and Chrome.
 *
 * That panel is macOS's press-and-hold, and it appears when a held key is
 * going into something editable. The page has exactly one editable thing: a
 * transparent contenteditable that exists so a *phone* will raise its
 * keyboard, because a phone raises nothing without focus. A laptop needs none
 * of it -- physical keys are read from a window listener and never went near
 * that field -- so focusing it there bought nothing and cost the panel.
 *
 * The other half is that "is the keyboard up" used to mean "does that field
 * have focus", which cannot be right once the field is not focused on a
 * laptop. It means "was it asked for" now, which is what the code around it
 * already said it meant.
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

const from = app.indexOf("function needsSoftKeyboard");
const until = app.indexOf("function deskKeyboardUp");
check(from > 0 && until > from, "the device check was found");

function harness(media) {
  return new Function("window", app.slice(from, until)
    + "; return needsSoftKeyboard;")(
      { matchMedia: media });
}

console.log("a phone still gets its field focused");
check(harness(() => ({ matches: true }))() === true,
      "coarse pointer, no hover: a soft keyboard is the only keyboard");

console.log("\na laptop does not");
check(harness(() => ({ matches: false }))() === false,
      "a real pointer and real hover: there is a real keyboard too");

console.log("\nand a browser that cannot answer behaves as the phone does");
check(harness(() => { throw new Error("no"); })() === true,
      "an exception means the field is focused, which is the safe way to be "
      + "wrong: a phone with no keyboard cannot type at all, where a laptop "
      + "with an accent panel is merely annoyed");
check(harness(undefined)() === true, "and so does a browser with no matchMedia");

console.log("\nthe field is only focused where it is needed");
const show = app.slice(app.indexOf("function deskShowKeyboard"),
                       app.indexOf("/* Modifiers are real presses"));
check(/if \(needsSoftKeyboard\(\)\) field\.focus/.test(show),
      "deskShowKeyboard asks before focusing");
check(/field\.blur\(\)/.test(show),
      "and still blurs unconditionally, so a field focused before this "
      + "change is let go of");

console.log("\nand the refocus loop asks too");
// A phone that drops focus has it taken back. A laptop has nothing to take
// back, and a loop that kept grabbing focus would restore the accent panel
// by another route.
check(/deskWantKeyboard && deskHeld && !document\.hidden\s*\n\s*&& needsSoftKeyboard\(\)/
        .test(app),
      "the refocus guard includes the device check");

console.log("\n\"the keyboard is up\" means it was asked for, not that a field has focus");
const up = app.slice(app.indexOf("function deskKeyboardUp"),
                     app.indexOf("function deskKeyboardUp") + 400);
check(/return deskWantKeyboard;/.test(up),
      "deskKeyboardUp reads the intent");
check(!/activeElement/.test(up),
      "and not the focused element, which is now empty on a laptop whose "
      + "keyboard is very much up");

console.log("\nthe field is still there for the phones that need it");
check(/id="desk-input"[\s\S]{0,200}contenteditable="true"/.test(html),
      "the contenteditable was not removed along the way");

process.exit(fails ? 1 : 0);
