/* The pad getting out of a zoomed picture's way, in a real browser.
 *
 * Read from the rendered page rather than the stylesheet, because the claim is
 * about geometry: does the picture actually reach the bottom of the screen
 * with the pad over it, and does it stop short of the pad when it is not
 * zoomed. A CSS rule can be present and beaten by specificity, by a media
 * query that does not match, or by a `position` further down the file -- all
 * three have happened in this stylesheet.
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

  await page.evaluate(() => {
    gate.hidden = true; stage.hidden = false;
    account = { name: "seth", can: [], primary: true, fresh: true };
    try { localStorage.setItem("fp:layout", "nintendo"); } catch (_) {}
    applyLayoutChoice("nintendo");
    setController(true);
  });
  await new Promise((r) => setTimeout(r, 300));

  // `getBoundingClientRect` includes transforms, so videoBottom is where the
  // picture is actually painted -- and videoBox is the layout box it was given,
  // which must not move.
  const shape = () => page.evaluate(() => {
    const t = document.querySelector(".touch");
    const v = document.querySelector("video");
    const style = getComputedStyle(t);
    return {
      padTop: t.getBoundingClientRect().top,
      padVisible: !t.hidden && style.display !== "none",
      videoBottom: v.getBoundingClientRect().bottom,
      videoBox: v.offsetHeight,
      videoTopBox: v.offsetTop,
      position: style.position,
      background: style.backgroundColor,
      zoomed: document.getElementById("stage").classList.contains("zoomed"),
    };
  });

  console.log("upright and not zoomed, the pad sits under the picture");
  const flat = await shape();
  check(flat.padVisible, "the pad is on screen");
  check(!flat.zoomed, "and the stage does not call itself zoomed");
  check(flat.position === "relative" || flat.position === "static",
        "the pad is in the flow: " + flat.position);
  check(flat.videoBottom <= flat.padTop + 1,
        "so the picture stops where the pad starts (" + Math.round(flat.videoBottom)
        + " <= " + Math.round(flat.padTop) + ")");

  console.log("\nzoomed in, the picture runs under the pad and nothing moves");
  await page.evaluate(() => { zoom = 2.5; applyZoom(); });
  await new Promise((r) => setTimeout(r, 200));
  const big = await shape();
  check(big.zoomed, "the stage says it is zoomed");
  check(big.padVisible, "the pad is still there to be pressed");
  check(big.position === flat.position,
        "the pad has NOT moved out of the flow: " + big.position);
  // The fault this replaced. Floating the pad let the picture's box grow into
  // the freed space, so a view somebody had lined up by dragging jumped to the
  // middle of a taller box the moment they zoomed. Where the picture sits is
  // theirs to decide.
  check(big.videoBox === flat.videoBox && big.videoTopBox === flat.videoTopBox,
        "and the picture's own box is untouched, so nothing jumps: "
        + flat.videoBox + "x@" + flat.videoTopBox + " -> "
        + big.videoBox + "x@" + big.videoTopBox);
  check(/rgba\(0, 0, 0, 0\)|transparent/.test(big.background),
        "with no background of its own: " + big.background);
  check(big.videoBottom > big.padTop + 1,
        "so the picture now reaches past the top of the pad ("
        + Math.round(big.videoBottom) + " > " + Math.round(big.padTop) + ")");

  console.log("\nand zooming back out puts it back");
  await page.evaluate(() => { zoom = 1; applyZoom(); });
  await new Promise((r) => setTimeout(r, 200));
  const back = await shape();
  check(!back.zoomed, "the stage stops calling itself zoomed");
  check(back.videoBottom <= back.padTop + 1,
        "and the picture stops at the pad again");
  check(back.videoBox === flat.videoBox,
        "with the same box it started in");

  check(errors.length === 0, "no script errors: " + errors.join(" | "));
} finally {
  await browser.close();
}

console.log();
if (fails.length) {
  console.log("FAILURES: " + fails.length);
  for (const f of fails) console.log("  " + f);
  process.exit(1);
}
console.log("all good");
