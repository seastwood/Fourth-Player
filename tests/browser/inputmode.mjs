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
// The button, not the phrase: the comment explaining why it went says the
// words too, and a test that cannot tell prose from markup fails on its own
// explanation.
check(!/<button[^>]*id="use-keys"/.test(html),
      "nor the button that remapped the keyboard without saying so");
check(!/<button[^>]*id="use-touch"/.test(html), "nor its neighbour");
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

console.log("\nbut only for somebody who may actually use it");
// The desk lands on the machine itself, as a keyboard and a mouse: the
// signed-in Steam account, a browser, somebody's files. The host has always
// gated it -- granted per account, never by default, and an authenticator
// code at the moment of use -- but the menu offered it to everybody, which
// says the opposite to anyone reading it.
const option = app.slice(app.indexOf("function paintDeskOption"),
                         app.indexOf("let deskMode = false;"));
check(/const allowed = may\("desk"\)/.test(option),
      "the option is in the list only while the guest holds `desk`");
check(/if \(allowed && !present\)/.test(option), "added when they may");
check(/present\.remove\(\)/.test(option), "and taken away when they may not");
check(/picker\.value === "desk" \|\| deskMode/.test(option),
      "and somebody already in that mode when the permission goes is moved "
      + "off it, rather than left on a choice that is no longer in the menu");
// Asserted as "inside the function that paints the permissions", not as
// "on the line after show(session-desk)". The first version of this checked
// the two were adjacent, and broke the moment something unrelated was added
// between them -- which is a test measuring the layout of the file rather
// than the behaviour it is there for.
const session = app.slice(app.indexOf("function paintSession() {"),
                          app.indexOf("function wireSession"));
check(/show\("session-desk", may\("desk"\)\)/.test(session)
      && /paintDeskOption\(\)/.test(session),
      "and it is repainted with the rest of the permissions, so it follows a "
      + "grant without a reload");

console.log("\nthe page is not the gate, though");
// Worth stating: this is the page agreeing with the host, not a second lock.
check(/taking the desk is a request the\n \* host answers/.test(app),
      "the code says so, so nobody later mistakes the menu for enforcement");
check(/may\("desk"\)/.test(app.slice(app.indexOf("function paintDeskMode"),
                                    app.indexOf("function buildLayoutPicker"))),
      "and the note does not tell somebody to press a button they cannot see");

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
