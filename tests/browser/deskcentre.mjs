/* Where the pointer sits on the screen while somebody is driving.
 *
 * The rule: the pointer stays in the middle of what can be *seen*, which is
 * not the middle of the video element whenever a keyboard is covering the
 * bottom of it.
 *
 * This exists because the arithmetic here was wrong three separate times and
 * every one of them looked fine until it was measured. Reading the code will
 * not tell you whether a pointer lands in the middle of a phone screen; only
 * putting numbers through it will.
 */
import puppeteer from "puppeteer-core";

const fails = [];
const check = (cond, msg) => {
  console.log((cond ? "  ok   " : "  FAIL ") + msg);
  if (!cond) fails.push(msg);
};

const browser = await puppeteer.launch({
  executablePath: "/usr/bin/google-chrome",
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

  const out = await page.evaluate(() => {
    gate.hidden = true; stage.hidden = false;
    // A 16:9 stream, which on an upright phone is letterboxed to about a
    // quarter of the height of the screen. That is the shape that broke it.
    Object.defineProperty(video, "videoWidth", { value: 1280, configurable: true });
    Object.defineProperty(video, "videoHeight", { value: 720, configurable: true });
    account = { name: "s", can: [], primary: true, fresh: true };
    deskHeld = true; cursorOn = true; deskSend = () => {};
    applyLayoutChoice("off"); deskPaintKeys();

    const top = video.offsetTop, H = video.offsetHeight;
    const driving = [];
    for (const lift of [0, 300]) {
      deskLift = lift;
      const want = top + (H - lift) / 2;
      for (const z of [1, 2, 3]) {
        for (const v of [0, 0.1, 0.5, 0.9, 1]) {
          zoom = z; cursorU = 0.5; cursorV = v;
          cursorFollow();
          const pic = pictureBox();
          const y = top + panY + H / 2 + (v - 0.5) * pic.height * z;
          driving.push({ lift, z, v, off: Math.round(y - want) });
        }
      }
    }

    // And with nobody driving, the picture behaves exactly as it always did:
    // it may not be dragged off its own edges.
    deskLift = 0; deskHeld = false; cursorOn = false;
    zoom = 2; panX = -99999; panY = -99999; applyZoom();
    const pic = pictureBox();
    const looking = {
      panX: Math.round(panX),
      limit: Math.round(panRoom(pic.width, pic.box.width, zoom)),
      panY: Math.round(panY),
    };
    return { driving, looking, H, top };
  });

  const strayed = out.driving.filter((r) => Math.abs(r.off) > 1);
  check(strayed.length === 0,
        "the pointer is in the middle of what can be seen in all "
        + out.driving.length + " cases: "
        + (strayed.length ? JSON.stringify(strayed.slice(0, 3)) : "none stray"));

  const withKeyboard = out.driving.filter((r) => r.lift === 300);
  check(withKeyboard.length > 0 && withKeyboard.every((r) => Math.abs(r.off) <= 1),
        "including with a keyboard covering the bottom 300px, which moves the "
        + "middle it is measured against");

  check(Math.abs(out.looking.panX) === out.looking.limit && out.looking.limit > 0,
        "and with nobody driving, the picture still stops at its own edge: "
        + out.looking.panX + " against a limit of " + out.looking.limit);

  check(errors.length === 0, "no script errors: " + errors.slice(0, 2));
} finally {
  await browser.close();
}

console.log();
if (fails.length) { console.log(fails.length + " FAILED"); process.exit(1); }
console.log("all good");
