/* Where the letterbox black stops, measured on a real page.
 *
 * A 16:9 stream inside an upright phone is a strip across the middle, and the
 * black either side of it used to run the whole height of the screen --
 * behind the row of chips at the top and behind the keyboard's buttons at the
 * bottom. The whole page was one black field with controls floating in it.
 *
 * It stops just under the chips and just above the buttons now. Two things
 * have to be true at once for that to be an improvement rather than a
 * regression, and only measuring shows it: the black has to actually stop
 * there, and the *picture* must not have been made any smaller to achieve it.
 * A stylesheet cannot be read for either.
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

/* What the page looks like at one size, with the picture a 16:9 stream. */
async function measure(page, size, pad) {
  await page.setViewport({ ...size, isMobile: true, hasTouch: true });
  await new Promise((r) => setTimeout(r, 250));
  return page.evaluate((pad) => {
    gate.hidden = true; stage.hidden = false;
    Object.defineProperty(video, "videoWidth",
                          { value: 1280, configurable: true });
    Object.defineProperty(video, "videoHeight",
                          { value: 720, configurable: true });
    account = { name: "s", can: [], primary: true, fresh: true };
    deskHeld = true; cursorOn = true; deskSend = () => {};
    applyLayoutChoice(pad ? "xbox" : "off");
    document.getElementById("hud").classList.add("show");
    deskPaintKeys();
    // The picture as it would be if fitPicture did nothing at all. Every
    // check about size compares against this rather than against a number
    // worked out by hand: the rule is "the picture is not made smaller", and
    // only the page can say what it would otherwise have been.
    const root = document.documentElement.style;
    root.setProperty("--picture-top", "0px");
    root.setProperty("--picture-bottom", "0px");
    const bare = pictureBox();
    const untouched = { width: bare.width, height: bare.height,
                        box: video.offsetHeight };
    fitPicture();
    const box = stage.getBoundingClientRect();
    const seen = video.getBoundingClientRect();
    const hud = document.getElementById("hud").getBoundingClientRect();
    const dock = document.getElementById("desk-dock").getBoundingClientRect();
    const pic = pictureBox();
    return {
      stage: { top: box.top, bottom: box.bottom,
               width: box.width, height: box.height },
      // The layout box, not the transformed one: nothing is zoomed here and
      // the two agree, but the habit is what keeps this honest.
      video: { top: video.offsetTop, height: video.offsetHeight,
               bottom: video.offsetTop + video.offsetHeight,
               painted: getComputedStyle(video).backgroundColor },
      rect: { top: seen.top, bottom: seen.bottom },
      hud: { bottom: hud.bottom, height: hud.height },
      dock: { top: dock.top, height: dock.height },
      picture: { width: pic.width, height: pic.height },
      stageBackground: getComputedStyle(stage).backgroundColor,
      untouched,
      pad: (() => {
        const it = document.getElementById("touch");
        return it && !it.hidden ? it.getBoundingClientRect().height : 0;
      })(),
    };
  }, pad);
}

try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(process.env.FP_PAGE, { waitUntil: "domcontentloaded" });
  await new Promise((r) => setTimeout(r, 700));

  console.log("upright, where the black used to fill the screen");
  const up = await measure(page, { width: 390, height: 844 }, false);

  // The picture is what it always was: full width, letterboxed. Stated first,
  // because every other check here is only worth having if this one holds.
  const wanted = 390 * 720 / 1280;
  check(Math.abs(up.picture.width - 390) < 1
        && Math.abs(up.picture.height - wanted) < 1,
        "the picture is exactly the size it was, full width and letterboxed: "
        + Math.round(up.picture.width) + "x" + Math.round(up.picture.height));

  check(up.rect.top >= up.hud.bottom - 1,
        "the black starts under the chips, not behind them: picture box top "
        + Math.round(up.rect.top) + " against a chip row ending at "
        + Math.round(up.hud.bottom));
  check(up.rect.bottom <= up.dock.top + 1,
        "and stops above the keyboard's buttons: picture box bottom "
        + Math.round(up.rect.bottom) + " against buttons starting at "
        + Math.round(up.dock.top));
  check(up.video.height < up.stage.height - 1,
        "so the picture's box is shorter than the screen, which is the whole "
        + "change: " + Math.round(up.video.height) + " of "
        + Math.round(up.stage.height));

  // And the stage paints nothing behind it, or none of the above would show.
  check(/rgba\(0,\s*0,\s*0,\s*0\)|transparent/.test(up.stageBackground),
        "the stage paints no black of its own, so the page's own background "
        + "is what shows either side: " + up.stageBackground);

  console.log("sideways, where there is no black to give away");
  const across = await measure(page, { width: 844, height: 390 }, false);
  check(Math.abs(across.video.height - across.stage.height) < 1,
        "the picture's box still fills the screen: "
        + Math.round(across.video.height) + " of "
        + Math.round(across.stage.height));
  check(Math.abs(across.picture.height - across.stage.height) < 1,
        "and so does the picture, which is never made smaller to tidy an "
        + "edge: " + Math.round(across.picture.height));

  // The case that broke it, and the reason this is measured rather than
  // worked out. Upright, the on-screen controller has the bottom of the
  // column, so the picture's box was never the stage -- and the first version
  // of fitPicture took the stage's height and subtracted the picture's fitted
  // height, which on a short screen with the pad up claimed hundreds of
  // pixels of black that did not exist. Handing away what is not there
  // squeezes the box below the picture and the *picture* gets smaller.
  // Reported as the video rendering very small and far too low.
  console.log("upright with the controller up, sharing a short screen");
  // Every size down to one where the chips and the buttons want more than the
  // whole of the black, because that is where this went wrong and there is no
  // way to know by hand which size that is on somebody's phone. The shorter
  // ones stand in for a real one: an address bar showing, a chip row wrapped
  // to three lines, a keyboard row at the bottom -- all of which come out of
  // the same column and none of which the stage's height knows about.
  for (const height of [844, 760, 660, 560, 480, 440]) {
    const tight = await measure(page, { width: 390, height }, true);
    check(tight.pad > 100,
          height + ": the controller really is in the column, taking "
          + Math.round(tight.pad) + " of it");
    // The one rule. Not a number: whatever the picture would have been with
    // no insets at all is what it must still be.
    check(Math.abs(tight.picture.width - tight.untouched.width) < 1
          && Math.abs(tight.picture.height - tight.untouched.height) < 1,
          height + ": the picture is not one pixel smaller for it: "
          + Math.round(tight.picture.width) + "x"
          + Math.round(tight.picture.height) + " against "
          + Math.round(tight.untouched.width) + "x"
          + Math.round(tight.untouched.height));
    check(tight.video.height >= tight.picture.height - 1,
          height + ": and its box still holds it: "
          + Math.round(tight.video.height) + " for "
          + Math.round(tight.picture.height));
    check(tight.video.top <= tight.hud.bottom + 1,
          height + ": pushed down by no more than the chips: box top "
          + Math.round(tight.video.top) + ", chips end "
          + Math.round(tight.hud.bottom));
  }

  check(errors.length === 0, "no script errors: " + errors.join(" | "));
} finally {
  await browser.close();
}

console.log(fails.length ? "\n" + fails.length + " FAILED" : "\nall good");
process.exit(fails.length ? 1 : 0);
