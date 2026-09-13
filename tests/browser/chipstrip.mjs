/* The strip of chips across the top, upright.
 *
 * It used to be painted in --bg with a rule along the bottom, which on a phone
 * reads as a solid bar across the top of the screen. Reported as "the black
 * background along the top where the top chip buttons go should be
 * transparent". The chips carry their own backgrounds, so the strip does not
 * need one -- and a 2px line separating a transparent strip from the picture
 * is a stray mark rather than a separator.
 *
 * What has to stay true: the chips are all still there, and still legible
 * against whatever is behind them.
 */
import puppeteer from "puppeteer-core";

const fails = [];
const check = (cond, msg) => {
  console.log((cond ? "  ok   " : "  FAIL ") + msg);
  if (!cond) fails.push(msg);
};

const browser = await puppeteer.launch({
  executablePath: process.env.FP_CHROME || "/usr/bin/google-chrome",
  args: ["--no-sandbox", "--disable-gpu"],
});
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.emulate({
    name: "phone",
    userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      + "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile Safari/604.1",
    viewport: { width: 390, height: 844, isMobile: true, hasTouch: true },
  });
  await page.goto(process.env.FP_PAGE, { waitUntil: "domcontentloaded" });
  await new Promise((r) => setTimeout(r, 700));

  const seen = await page.evaluate(() => {
    gate.hidden = true; stage.hidden = false; showHud();
    const hud = document.getElementById("hud");
    const style = getComputedStyle(hud);
    const chips = [...hud.children]
      .filter((c) => getComputedStyle(c).display !== "none"
                     && c.getBoundingClientRect().width > 0);
    // Something behind the text, and enough of it to read against a picture.
    const filled = (el) => {
      const m = /rgba?\(([^)]+)\)/.exec(getComputedStyle(el).backgroundColor);
      if (!m) return false;
      const parts = m[1].split(",").map((n) => parseFloat(n));
      return parts.length < 4 || parts[3] > 0.3;
    };
    // A wrapper counts if everything it holds is filled. #zoom and #vol are
    // exactly that: a div around a button and its slider, carrying no fill of
    // their own and needing none, because the controls inside carry theirs.
    const legible = (el) => {
      if (filled(el)) return true;
      const inner = [...el.children]
        .filter((c) => getComputedStyle(c).display !== "none"
                       && c.getBoundingClientRect().width > 0);
      return inner.length > 0 && inner.every(legible);
    };
    const opaque = chips.filter(legible);
    return {
      bg: style.backgroundColor,
      border: style.borderBottomWidth,
      chips: chips.length,
      legible: opaque.length,
      stacked: getComputedStyle(document.getElementById("stage")).flexDirection,
    };
  });

  check(/rgba\(0, 0, 0, 0\)|transparent/.test(seen.bg),
        `the strip paints nothing behind the chips: ${seen.bg}`);
  check(seen.border === "0px",
        `and draws no rule under itself: ${seen.border}`);
  check(seen.chips > 2,
        `the whole row is still laid out, not a couple of it: ${seen.chips}`);
  check(seen.legible === seen.chips,
        `and every chip still has something behind its own text:`
        + ` ${seen.legible} of ${seen.chips}`);
  check(seen.stacked === "column",
        "upright is still the stacked layout, which this does not change");
  check(errors.length === 0, "no script errors: " + errors.join(" | "));
} finally {
  await browser.close();
}

console.log("");
if (fails.length) { console.log(`chipstrip: ${fails.length} FAILED`); process.exit(1); }
console.log("chipstrip: all ok");
