/* Where things actually are, upright, on a phone-shaped screen.
 *
 * This exists because a stylesheet that asserts correctly can still lay out
 * wrongly. An earlier attempt at this layout passed every rule-level check and
 * shipped broken: the pad had two `position` declarations, the later one won,
 * so it stayed in flow and -- with the stage no longer a flex column -- floated
 * to the top of the screen over the chips with the picture behind it. It was
 * reported as "the controller now sits too high, I see no video stream, and
 * only two of the top chip buttons are visible".
 *
 * So these are measurements, not properties: the picture fills the stage, the
 * pad is at the bottom, the pad is below the chips, and the chips are all
 * there. Each one is a sentence from that report, turned round.
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

  // In a session, upright, with the on-screen controller up.
  await page.evaluate(() => {
    gate.hidden = true; stage.hidden = false;
    try { localStorage.setItem("fp:layout", "nintendo"); } catch (_) {}
    applyLayoutChoice("nintendo");
    showTouch(true);
    showHud();
  });
  await new Promise((r) => setTimeout(r, 400));

  const box = await page.evaluate(() => {
    const r = (el) => { const b = el.getBoundingClientRect();
      return { top: b.top, bottom: b.bottom, left: b.left, right: b.right,
               w: b.width, h: b.height }; };
    const hud = document.getElementById("hud");
    const chips = Array.from(hud.children).filter(
      (c) => getComputedStyle(c).display !== "none"
             && c.getBoundingClientRect().width > 0);
    return {
      stage: r(document.getElementById("stage")),
      video: r(document.getElementById("screen")),
      touch: r(document.getElementById("touch")),
      hud: r(hud),
      chips: chips.length,
      touchBg: getComputedStyle(document.getElementById("touch")).backgroundColor,
      touchTaps: getComputedStyle(document.getElementById("touch")).pointerEvents,
    };
  });

  const near = (a, b, slack = 2) => Math.abs(a - b) <= slack;

  // "I see no video stream" -- the picture must cover the whole stage.
  check(near(box.video.h, box.stage.h),
        `the picture is as tall as the stage: ${Math.round(box.video.h)}`
        + ` against ${Math.round(box.stage.h)}`);
  check(near(box.video.top, box.stage.top),
        "and starts at the top of it");
  check(near(box.video.bottom, box.stage.bottom),
        "and reaches the bottom, behind the controller");

  // "the controller now sits too high" -- it belongs at the bottom.
  check(near(box.touch.bottom, box.stage.bottom, 3),
        `the controller is at the bottom of the stage:`
        + ` ${Math.round(box.touch.bottom)} against`
        + ` ${Math.round(box.stage.bottom)}`);
  check(box.touch.top > box.stage.top + box.stage.h / 2,
        "and sits in the lower half of the screen, not up by the chips:"
        + ` its top is ${Math.round(box.touch.top)}`);

  // "only two of the top chip buttons are visible" -- the pad must not be
  // over them, and they must all be laid out.
  check(box.touch.top >= box.hud.bottom,
        `the controller starts below the chip row: ${Math.round(box.touch.top)}`
        + ` against ${Math.round(box.hud.bottom)}`);
  check(box.chips > 2,
        `every chip in the row is laid out, not just a couple: ${box.chips}`);

  // The panel behind the pad, which is what was reported in the first place.
  check(/rgba\(0, 0, 0, 0\)|transparent/.test(box.touchBg),
        `the controller paints no panel behind itself: ${box.touchBg}`);
  // And its empty space must not eat drags meant for the picture.
  check(box.touchTaps === "none",
        "its empty space lets taps through to the picture");

  check(errors.length === 0, "no script errors: " + errors.join(" | "));
} finally {
  await browser.close();
}

console.log("");
if (fails.length) {
  console.log(`portraitgeom: ${fails.length} FAILED`);
  process.exit(1);
}
console.log("portraitgeom: all ok");
