/* The on-screen pad gets out of the picture's way once it is zoomed.
 *
 * Upright at 1x the pad belongs in the flow below the picture: a 16:9 stream
 * on a tall phone is letterboxed, the space underneath is free, and a thumb
 * cannot reach the middle of an upright screen anyway. That reasoning stops
 * holding the moment somebody zooms in -- there is no letterbox left, every
 * pixel under the pad is picture they deliberately magnified, and a solid
 * panel across the bottom third is exactly what they zoomed in to look at.
 *
 * Asked for in those terms: "when I zoom in I would like to allow the video
 * stream to be visible behind the on screen controllers buttons, like how its
 * visible when zoomed in behind the top chip buttons".
 */
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../../web/style.css", import.meta.url), "utf8");
const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
let bad = 0;
const check = (cond, what) => {
  console.log((cond ? "  ok   " : "  FAIL ") + what);
  if (!cond) bad += 1;
};

console.log("the page says whether the picture is zoomed");
check(/stage\.classList\.toggle\("zoomed", zoom > ZOOM_MIN\);/.test(app),
      "on the stage, where the layout can answer it");
// In applyZoom rather than in the gesture: every route to a zoom goes through
// it -- the slider, a pinch, a double tap, a restored zoom on reconnect -- and
// a class set in only some of them is a layout that depends on how you got
// there.
const apply = app.slice(app.indexOf("function applyZoom()"),
                        app.indexOf("function paintZoom()"));
check(apply.includes('classList.toggle("zoomed"'),
      "from applyZoom, so every way of zooming says it and not just the "
      + "gesture that prompted this");

console.log("\nand upright the pad floats once it is set");
const portrait = css.slice(css.lastIndexOf("@media (orientation: portrait)"));
const rule = css.slice(css.indexOf(".stage.zoomed .touch {"));
check(css.includes(".stage.zoomed .touch {"), "there is a rule for it");
check(/position: absolute;/.test(rule.slice(0, 200)),
      "it leaves the flow, so the picture takes the height back");
check(/bottom: 0;/.test(rule.slice(0, 200)),
      "and stays at the bottom, where the thumbs are");
check(/background: none;/.test(rule.slice(0, 200)),
      "with the panel's own background gone, which is what was hiding the "
      + "picture");

console.log("\nbut only its background, and only when zoomed");
check(/\.touch \{\s*\n\s*background: var\(--bg\);\s*\n\s*\}/.test(css),
      "at 1x the panel is still solid: there is nothing behind it to see, "
      + "and a letterboxed picture needs no help");
// Each key paints itself, so the buttons stay readable over a bright scene
// instead of becoming outlines on top of a game.
check(/--key: #/.test(css), "the keys keep a background of their own");

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
