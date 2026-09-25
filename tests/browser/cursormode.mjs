/* How a finger drives the pointer: as a place, or as a movement.
 *
 * Reported as: in games I cannot move the aiming crosshair, because the
 * absolute cursor is limited to the screen size.
 *
 * That is exactly right. A game reads the mouse as turning, not as pointing,
 * and an absolute pointer is pinned inside the screen -- so a crosshair stops
 * at the edge and will not turn any further however far the finger drags. The
 * relative mode sends the movement instead, like a trackpad, and there are no
 * edges to stop at.
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const css = readFileSync(new URL("../../web/style.css", import.meta.url), "utf8");
const html = readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

const from = app.indexOf("const CURSOR_MODE_KEY");
const until = app.indexOf("/* Move the pointer by a fraction");
check(from > 0 && until > from, "the cursor mode code was found");

function harness(stored) {
  const sent = [];
  const moved = [];
  const store = new Map();
  if (stored !== undefined) store.set("fp:cursor-mode", stored);
  const fns = new Function(
    "deskSend", "deskMoved", "pictureBox", "zoom", "localStorage", "el",
    "POINT_MAX",
    "let cursorU = 0.5, cursorV = 0.5;"
    + app.slice(from, until)
    + "; return { cursorSend, cursorSendMotion, setCursorMode,"
    + " mode: () => cursorMode };")(
      (list) => sent.push(...list),
      (dx, dy) => moved.push([dx, dy]),
      () => ({ width: 1000, height: 500 }), 1,
      { getItem: (k) => (store.has(k) ? store.get(k) : null),
        setItem: (k, v) => store.set(k, v) },
      () => null, 65535);
  return { sent, moved, fns, store };
}

console.log("absolute is the default, and is what a touchscreen expects");
let h = harness();
check(h.fns.mode() === "absolute", "nothing stored means absolute");
check(h.fns.cursorSendMotion(0.1, 0.1) === false,
      "a drag is not turned into movement");
h.fns.cursorSend();
check(h.sent.length === 1 && h.sent[0].t === "p",
      `it sends a place: ${JSON.stringify(h.sent[0])}`);

console.log("\nrelative sends the movement instead");
h = harness("relative");
check(h.fns.mode() === "relative", "the stored mode is read back");
check(h.fns.cursorSendMotion(0.1, 0.2) === true, "a drag becomes movement");
check(h.sent.length === 0, "and no place is sent");
check(h.moved.length === 1, "exactly one movement");
check(h.moved[0][0] === 100 && h.moved[0][1] === 100,
      `a tenth of a 1000px picture is 100px across, a fifth of 500 is 100 `
      + `down: ${JSON.stringify(h.moved[0])}`);

console.log("\nit goes through deskMoved, not straight to the wire");
// deskMoved is where the pointer speed is applied and where the estimate of
// the host's cursor is kept. Sending motion around it would move the pointer
// and leave the estimate behind, which is the bug that made zoom follow a
// cursor that was not there.
check(/deskMoved\(du \* \(picture\.width/.test(app),
      "the relative path calls deskMoved");

console.log("\nswitching is remembered");
h = harness();
h.fns.setCursorMode("relative");
check(h.fns.mode() === "relative", "it changes");
check(h.store.get("fp:cursor-mode") === "relative", "and is written down");
h.fns.setCursorMode("nonsense");
check(h.fns.mode() === "absolute",
      "anything unrecognised falls back to absolute, which is the safe one: "
      + "a pointer that goes where you touch is never unusable, and one that "
      + "only turns is baffling on a desktop");

console.log("\nrelative has no edges, which is the entire point");
const move = app.slice(app.indexOf("function cursorMove"),
                       app.indexOf("function cursorMove") + 700);
check(/if \(cursorSendMotion\(du, dv\)\) return true;/.test(move),
      "a relative drag returns early, before the clamping");
check(/Math\.max\(0, Math\.min\(1, cursorU \+ du\)\)/.test(move),
      "and the clamping is still there for the absolute one, which does have "
      + "edges");


/* The names, which is the part somebody reads.
 *
 * "Point where I touch" described neither mode: nothing jumps to where a
 * finger lands, both are dragged about, and the difference is what is sent
 * and therefore whether the pointer can leave the edge of the picture. It
 * was reported, fairly, as not making sense. */
const markup = readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");
// To the menu's own closing tag, not to whatever happens to come after it in
// the file. It used to end at id="desk-kb", which no longer exists -- indexOf
// then answered -1 and the slice worked by accident.
const menuAt = markup.indexOf('id="cursor-menu"');
const menu = markup.slice(menuAt, markup.indexOf("</div>", menuAt));
check(!menu.includes("Point where I touch"),
      "the old name is gone");
check(/data-cursor="absolute">\s*<strong>Trackpad<\/strong>/.test(menu),
      "the absolute one is called what it behaves like: a trackpad");
check(!menu.includes("stops at the edges"),
      "with no paragraph under it: the menu opens over the picture somebody "
      + "is trying to look at");
check(/data-cursor="relative">\s*<strong>Trackpad for games<\/strong>/.test(menu),
      "and the relative one says who it is for");
check(!/<span>/.test(menu),
      "neither entry carries a description at all");

console.log("\nit is chosen from a list, not toggled blindly");
// The button used to change the mode on a press and hold: invisible until
// somebody found it by accident, and then silent about what it had become.
// Reported as hard to understand.
check(!/tap and hold to change/i.test(app),
      "there is no press-and-hold gesture left");
check(/cursorMenuOpen/.test(app), "a list is opened instead");
check(/data-cursor/.test(html), "with an entry per mode");
check(/aria-checked/.test(app),
      "and the one in force is marked, rather than left to be inferred");
check(/deskChoose\("cursor"\)/.test(app.slice(app.indexOf('el("cursor-menu")'))),
      "picking one also switches to the cursor, so one tap does the obvious "
      + "thing");
check(/\.cursor-menu/.test(css), "and it is styled");
check(/\.desk-bar \{[^}]*position: relative/.test(css),
      "anchored to the bar, or an absolutely positioned list lands somewhere "
      + "else entirely");

console.log("\nand holding it does not select its own labels");
/* iOS reads a long press on anything selectable as "select this word", so the
 * gesture that opens this menu also put a blue highlight and two drag handles
 * over the entries it had just revealed. */
check(/\.desk-bar \{[^}]*-webkit-user-select: none/.test(css),
      "the corner refuses selection, Safari's spelling included");
check(/\.desk-bar \{[^}]*-webkit-touch-callout: none/.test(css),
      "and the magnifier and share sheet that come with a long press");

console.log("\nand the gesture that opens it cannot also close it");
/* Reported twice. The first fix stopped the click that ends the hold, which
 * was enough on a desktop and not on a phone: iOS also sends compatibility
 * mouse events after a touch, and treats a long press as a system gesture it
 * may cancel the pointer for. Chasing those one at a time is how the menu
 * came to be fixed on one machine and still flashing on another.
 *
 * So the rule is over time instead, where it does not depend on which events
 * a browser sends: a menu that has only just opened is still opening. */
check(/const CURSOR_MENU_SETTLE = (\d+);/.test(app),
      "there is a settling window after it opens");
const settle = Number((/const CURSOR_MENU_SETTLE = (\d+);/.exec(app) || [])[1]);
check(settle >= 250 && settle <= 800,
      "long enough to outlast the gesture, short enough not to feel stuck: "
      + settle + "ms");
const open = app.slice(app.indexOf("function cursorMenuOpen"));
check(/if \(!yes && !force && cursorMenuAt\s*\n?\s*&& Date\.now\(\) - cursorMenuAt < CURSOR_MENU_SETTLE\) return;/
        .test(open.slice(0, 700)),
      "and a close inside it is refused, whoever asked for it");
check(/cursorMenuAt = yes \? Date\.now\(\) : 0;/.test(open.slice(0, 700)),
      "measured from the moment it opened");
check(/cursorMenuOpen\(false, true\)/.test(app),
      "except choosing an entry, which is a decision rather than a leftover");
check(app.indexOf("cursorMenuOpen(false, true)")
        > app.indexOf('el("cursor-menu").addEventListener'),
      "and that is the menu's own handler doing it");

process.exit(fails ? 1 : 0);
