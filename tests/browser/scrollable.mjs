/* That the panels inside the stage can actually be scrolled by touch.
 *
 * `touch-action` is intersected down the ancestor chain, so a descendant can
 * never re-enable a gesture an ancestor forbade. `.stage` said `none`, which
 * met `pan-y` on .tab-panel, .shelf and .chat-log and won -- so not one panel
 * inside the stage could be scrolled with a finger, and the comment in the
 * stylesheet asserted the opposite. Reported as "currently I have no way to
 * scroll with a mobile touchscreen device".
 *
 * Checked two ways, because the rule-level half is what was wrong last time:
 * the effective value is computed down the live chain, and then a real touch
 * drag is dispatched and the scroll position looked at.
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

  // In a session, with a panel open and more in it than fits.
  const box = await page.evaluate(() => {
    gate.hidden = true; stage.hidden = false;
    openPads();
    const panel = document.querySelector(".tab-panel");
    panel.innerHTML = "";
    for (let i = 0; i < 60; i++) {
      const p = document.createElement("p");
      p.textContent = "row " + i;
      p.style.margin = "18px 0";
      panel.appendChild(p);
    }
    const r = panel.getBoundingClientRect();
    return { x: Math.round(r.left + r.width / 2),
             y: Math.round(r.top + r.height / 2),
             scrollable: panel.scrollHeight > panel.clientHeight + 10,
             h: Math.round(r.height) };
  });
  check(box.scrollable,
        `the panel really does have more in it than fits (height ${box.h})`);

  // -- what the browser will actually allow, down the live chain -----------
  const chain = await page.evaluate(() => {
    const panel = document.querySelector(".tab-panel");
    const seen = [];
    for (let el = panel; el; el = el.parentElement) {
      const value = getComputedStyle(el).touchAction;
      if (value && value !== "auto") {
        seen.push((el.id ? "#" + el.id : "." + (el.className || el.tagName))
                  + " = " + value);
      }
    }
    return seen;
  });
  console.log("     chain:", chain.join("  <-  ") || "(nothing restricts it)");
  check(!chain.some((s) => / = none$/.test(s)),
        "nothing from the panel up to the root forbids panning outright");
  check(!/#stage = none/.test(chain.join(" ")),
        "and the stage in particular does not, which is what broke this");

  // -- and that the panel is a working scroll container ---------------------
  //
  // Not a test of touch-action, and it must not be read as one: touches
  // synthesised through CDP do not go through the compositor path that
  // enforces it. Measured -- with `.stage` set back to `none`, the chain check
  // below fails and this drag still scrolls, 172 pixels of it. So this says
  // "there is something here that scrolls when dragged" and the chain check
  // above says "a finger will be allowed to". Both are needed and neither
  // covers the other.
  const session = await page.target().createCDPSession();
  const touch = (type, y) => session.send("Input.dispatchTouchEvent", {
    type,
    touchPoints: type === "touchEnd" ? [] : [{ x: box.x, y, radiusX: 6, radiusY: 6 }],
  });
  await touch("touchStart", box.y + 120);
  for (let step = 1; step <= 6; step++) await touch("touchMove", box.y + 120 - step * 20);
  await touch("touchEnd", box.y);
  await new Promise((r) => setTimeout(r, 400));

  const moved = await page.evaluate(
    () => document.querySelector(".tab-panel").scrollTop);
  check(moved > 0,
        "the panel scrolls when dragged, so there is a scroll container here"
        + ` for the chain above to permit: scrollTop ${Math.round(moved)}`);

  check(errors.length === 0, "no script errors: " + errors.join(" | "));
} finally {
  await browser.close();
}

console.log("");
if (fails.length) {
  console.log(`scrollable: ${fails.length} FAILED`);
  process.exit(1);
}
console.log("scrollable: all ok");
