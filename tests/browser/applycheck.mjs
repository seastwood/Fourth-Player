/* Apply noticing everything it would send.
 *
 * Reported as: unticking Virtual Display and pressing Apply does nothing, so
 * the display is never removed.
 *
 * The button disables itself when nothing has changed -- each apply costs the
 * room a second of picture, so saying so beforehand is right. But "changed"
 * was a hand-written list of fields, and three settings were added to what
 * Apply *sends* without being added to it. So the page compared seven things,
 * found them equal, said "Nothing to apply", and there was no way to send a
 * change it had itself just accepted.
 *
 * This checks the two lists against each other rather than checking today's
 * fields, because the fault is structural: the next setting added to
 * streamFields will have the same problem, and naming them here would just
 * move the omission into this file.
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

// What Apply sends.
const fields = app.slice(app.indexOf("function streamFields"),
                         app.indexOf("function paintStream"));
const sent = new Set();
for (const m of fields.matchAll(/^\s{4}([a-z_]+):/gm)) sent.add(m[1]);
// The spread form, which is how an optional field is written.
for (const m of fields.matchAll(/\{ ([a-z_]+): Number\(el\("stream-/g)) sent.add(m[1]);
check(sent.size >= 8, `streamFields sends ${sent.size} settings: ${[...sent]}`);
for (const must of ["virtual_display", "monitor", "height", "codec"]) {
  check(sent.has(must), `it sends ${must}`);
}

// What Apply compares before deciding it would do nothing.
const compare = app.slice(app.indexOf("const apply = el(\"stream-apply\")"),
                          app.indexOf("apply.disabled = same"));
check(compare.length > 100, "the comparison was found");

console.log("\nevery setting Apply sends is one Apply looks at");
for (const key of sent) {
  check(compare.includes(key),
        `${key} is compared, so changing it enables the button`);
}

console.log("\nand every control that changes one tells the button");
// The comparison knowing about a field is not enough: something has to ask it
// to look again when the control moves.
const wiring = app.slice(app.indexOf('for (const id of ["stream-size"'),
                         app.indexOf('const apply = el("stream-apply");',
                                     app.indexOf('for (const id of ["stream-size"')));
for (const id of ["stream-virtual", "stream-screen", "stream-width",
                  "stream-height", "stream-size", "stream-codec"]) {
  check(wiring.includes(id), `${id} is wired to repaint the button`);
}
check(/type === "number"[\s\S]{0,120}addEventListener\("input"/.test(wiring),
      "number boxes listen for `input` as well as `change` -- `change` waits "
      + "for the field to be left, so the button would stay dead while "
      + "somebody looked straight at the number they had just typed");

console.log("\nthe switch shows the boxes it governs, before Apply is pressed");
check(/virtualBox\.addEventListener\("change"[\s\S]{0,200}row\.hidden = !virtualBox\.checked/
        .test(app),
      "ticking it reveals the exact-size boxes without waiting for the host");

console.log("\nand the controls it compares actually exist on the page");
for (const id of ["stream-virtual", "stream-screen", "stream-width",
                  "stream-height"]) {
  check(html.includes(`id="${id}"`), `${id} is in the markup`);
}

process.exit(fails ? 1 : 0);
