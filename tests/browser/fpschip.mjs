/* The frame rate belongs on a chip, not in a notice.

   A notice appeared over the picture to say the rate was low -- over the
   very picture the number was about, interrupting somebody watching it, and
   wrong about as often as right because it was reading a counter another
   caller had just emptied. Something that interrupts to tell you a number
   should only speak when it matters, and a frame rate never does. */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const page = readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");
const css = readFileSync(new URL("../../web/style.css", import.meta.url), "utf8");

let bad = 0;
const check = (cond, what) => {
  console.log((cond ? "  ok   " : "  FAIL ") + what);
  if (!cond) bad += 1;
};

console.log("the notice is gone");
check(!app.includes("frames a second on this page"),
      "nothing interrupts the picture to report a frame rate");
check(!app.includes("The browser's own drawing may be smoother"),
      "nor to suggest switching away from it");

console.log("and a chip says it instead");
check(page.includes('id="fps-chip"'), "the page has one");
check(/<span id="fps-chip"/.test(page),
      "as a label, not a button: it does nothing but say");
check(page.includes("hidden"), "hidden until there is a number worth showing");
check(app.includes("function sayTheRate"), "and something that writes it");
check(app.includes('chip.textContent = shown + " fps"'), "in frames a second");

console.log("it speaks for whichever method is drawing");
const rate = app.slice(app.indexOf("function tellAboutTheRate"),
                       app.indexOf("function sayTheRate"));
check(rate.includes("sayTheRate(mine)"),
      "the page's own count when the page is drawing");
check(rate.includes('sayTheRate(per("decoded"))'),
      "and the browser's when the browser is");

console.log("a rate well under what is being sent is marked, not announced");
check(app.includes('chip.classList.toggle("warn"'), "with a class");
check(css.includes(".chip.warn"), "which is styled");
check(app.includes("shown < want * 0.75"),
      "at the same threshold the notice used, so nothing is lost but the "
      + "interruption");

console.log("and nothing is shown before there is anything to show");
check(app.includes("if (!(rate > 0)) { chip.hidden = true; return; }"),
      "a rate of nothing hides it rather than reading zero");

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
