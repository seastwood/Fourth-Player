/* Choosing how you play, rather than being asked on the way in.
 *
 * Entering a stream used to raise "Press any button on your controller", with
 * two offers underneath: on-screen buttons, or "play on the keyboard". The
 * second maps the keyboard onto a gamepad -- arrow keys become a d-pad,
 * letters become buttons -- so somebody who took the offer found every key
 * remapped underneath them. That is not what "play on the keyboard" sounds
 * like, and not a thing to discover in the middle of a game.
 *
 * So the prompt does not open itself, the option is named for what it does,
 * and there is a separate choice for driving the machine with a real keyboard
 * and mouse, where nothing is remapped at all.
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

console.log("the prompt no longer lets itself in");
const prompt = html.slice(html.indexOf('id="prompt"'),
                          html.indexOf('id="prompt"') + 200);
check(/hidden/.test(prompt), "it starts hidden");
check(!/Press any button on your controller/.test(html),
      "and the page no longer carries that sentence at all");
check(!/play on the keyboard/.test(html),
      "nor the offer that remapped the keyboard without saying so");
// The element stays: other things borrow it and put their own words in.
check(/id="prompt"/.test(html), "the panel itself is still there to borrow");

console.log("\nand nothing reopens it behind the guest's back");
check(!/if \(padIndex === null && !touchOn && !keyboardOn\) el\("prompt"\)\.hidden = false/
        .test(app),
      "closing the controls panel does not put it back over the picture");
check(!/else if \(!keyboardOn\) el\("prompt"\)\.hidden = false/.test(app),
      "and a controller going away does not either");

console.log("\nthe keyboard option says which of the two things it is");
check(/keys\.textContent = "Keyboard-Controller"/.test(app),
      "it is named Keyboard-Controller, because it makes the keyboard a pad");
check(!/keys\.textContent = "Keyboard";/.test(app),
      "and not just Keyboard, which is what it was and what misled");

console.log("\nthere is a separate choice for a real keyboard and mouse");
check(/desk\.value = "desk"/.test(app), "the option exists");
check(/desk\.textContent = "Mouse and keyboard"/.test(app), "and is named so");

const apply = app.slice(app.indexOf("function applyLayoutChoice"),
                        app.indexOf("function paintPicker"));
check(/deskMode = key === "desk"/.test(apply), "choosing it sets the mode");
check(/key === "off" \|\| keyboardOn \|\| deskMode/.test(apply),
      "and puts the on-screen pad away, like the other non-pad choices");
check(/keyboardOn = key === "keyboard"/.test(apply),
      "while leaving the key mapping off, which is the whole distinction: "
      + "picking this must not remap anything");

console.log("\nand it does not take the desk panel away from anybody");
const paint = app.slice(app.indexOf("function paintDeskMode"),
                        app.indexOf("function buildLayoutPicker"));
check(!/panel\.hidden/.test(paint),
      "whether the desk panel shows is the host's answer, not this choice -- "
      + "gating it here would take it from somebody who has the permission "
      + "and simply has not opened this menu");
check(/desk-mode-note/.test(paint),
      "what it does instead is say where the rest of it is");
check(/id="desk-mode-note"/.test(html), "and that note exists");

console.log("\nthe note points at the button that actually does it");
const note = html.slice(html.indexOf('id="desk-mode-note"'),
                        html.indexOf('id="desk-mode-note"') + 400);
check(/Take the keyboard and\s+mouse/.test(note),
      "it names the button");
check(/nothing is\s+remapped/.test(note),
      "and says the thing that was not obvious before");

process.exit(fails ? 1 : 0);
