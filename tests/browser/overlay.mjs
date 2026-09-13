/* The picture fills the stage; the controls float on it.
 *
 * Reported from a phone: "there's still a background behind the controller
 * area and the top chip button area ... what I would like is for the video to
 * clearly fill these spaces when zoomed in, the controller buttons would still
 * overlay, and the top chip buttons would still overlay, just without the
 * background over the video stream."
 *
 * Landscape had always done this. Upright was a flex column: the video took
 * what was left after the pad, so the pad sat on the stage's own black instead
 * of on the game. These are the invariants that keep both orientations the
 * same shape.
 */
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../../web/style.css", import.meta.url), "utf8");

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

/* Every @media block for an orientation, joined.
 *
 * All of them, not the first or the last: the stylesheet has four portrait
 * blocks and rules for one orientation are spread across them. Picking one was
 * this test's own first bug -- it read a block that says nothing about the pad
 * and reported the layout broken while it was fine. */
function mediaBlock(want) {
  const needle = "@media (orientation: " + want + ")";
  let out = "", from = 0;
  for (;;) {
    const at = css.indexOf(needle, from);
    if (at < 0) return out;
    let depth = 0, i = css.indexOf("{", at);
    const start = i;
    for (; i < css.length; i++) {
      if (css[i] === "{") depth++;
      else if (css[i] === "}" && --depth === 0) break;
    }
    out += css.slice(start + 1, i) + "\n";
    from = i;
  }
}

/* The body of the `.touch {` rule that actually positions the pad. There is
   more than one such rule per orientation; the others tune its grid. */
function touchRule(block) {
  let from = 0;
  for (;;) {
    const at = block.indexOf(".touch {", from);
    if (at < 0) return "";
    const body = block.slice(at, block.indexOf("}", at));
    if (/position:/.test(body)) return body;
    from = at + 1;
  }
}
const portrait = mediaBlock("portrait");
const landscape = mediaBlock("landscape");

check(portrait.length > 0 && landscape.length > 0,
      "both orientation blocks are found");

// -- no scrim over the picture --------------------------------------------
const hud = css.slice(css.indexOf(".hud {"), css.indexOf(".hud:not(.show)"));
check(!/^\s*background\s*:/m.test(hud),
      "the chip row draws no background of its own over the picture");

// -- upright: the picture fills, the pad floats ---------------------------
check(!/\.stage\s*\{[^}]*display:\s*flex/.test(portrait),
      "upright, the stage is not a flex column sharing space with the pad");

const vat = portrait.indexOf("video {");
const pvideo = portrait.slice(vat, portrait.indexOf("}", vat));
check(/position:\s*absolute/.test(pvideo) && /inset:\s*0/.test(pvideo),
      "upright, the picture is laid over the whole stage");
check(!/flex:\s*1 1 auto/.test(pvideo),
      "and no longer takes only the space left over");

// -- the pad overlays, in BOTH orientations -------------------------------
for (const [name, block] of [["upright", portrait], ["landscape", landscape]]) {
  const rule = touchRule(block);
  check(/position:\s*absolute/.test(rule),
        `${name}, the pad is positioned over the picture`);
  check(/inset:\s*auto 0 0 0/.test(rule),
        `${name}, pinned to the bottom so rows grow upwards and none is pushed off`);
  check(/pointer-events:\s*none/.test(rule),
        `${name}, the pad's empty space lets taps through to the picture`);
  check(/\.touch-left,\s*\.touch-right,\s*\.touch-mid,\s*\n?\s*\.shoulders\s*\{\s*pointer-events:\s*auto/
        .test(block),
        `${name}, but every cluster of controls still takes them`);
}

// -- the two mistakes that broke this the first time ----------------------
//
// Both were invisible to every other assertion here, because both are about
// what a rule says *twice* rather than what it says.
for (const [name, block] of [["upright", portrait], ["landscape", landscape]]) {
  const rule = touchRule(block);
  const positions = (rule.match(/^\s*position\s*:/gm) || []).length;
  check(positions === 1,
        `${name}, the pad declares position exactly once (found ${positions})`
        + " -- a second one later in the block silently wins, which left the"
        + " pad in flow at the top of the screen over the chips");
  check(/position:\s*absolute/.test(rule),
        `${name}, and that one declaration is the absolute one`);
}

// A bare `.touch { background: ... }` is the panel that was being seen behind
// the controller. Only rules for the element itself: the buttons inside it
// have their own fills and must keep them.
for (const [name, block] of [["upright", portrait], ["landscape", landscape]]) {
  let painted = false, from = 0;
  for (;;) {
    const at = block.indexOf(".touch {", from);
    if (at < 0) break;
    if (/^\s*background\s*:/m.test(block.slice(at, block.indexOf("}", at)))) {
      painted = true;
    }
    from = at + 1;
  }
  check(!painted,
        `${name}, the pad paints no panel behind itself -- the buttons sit on`
        + " the game");
}

console.log("");
if (fails) { console.log(`overlay: ${fails} FAILED`); process.exit(1); }
console.log("overlay: all ok");
