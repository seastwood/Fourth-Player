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
    const rows = [];
    for (const lift of [0, 300]) {
      // The strip is what the inset is measured from, so put it in the state
      // a keyboard of that height would put it in. Setting only the key row's
      // hidden flag used to be enough; once the keys and the buttons shared a
      // strip it stopped being, and this suite went on passing while
      // measuring nothing -- both of its cases became the no-keyboard one.
      const row = document.getElementById("desk-keys");
      const dock = document.getElementById("desk-dock");
      // No animation while measuring. The strip slides to its new position
      // over about a fifth of a second, and geometry read in the middle of
      // that is the old position -- which is how this measured 53 pixels
      // where 353 was meant, and would have gone on agreeing with itself.
      dock.style.transition = "none";
      row.hidden = lift === 0;
      dock.classList.toggle("has-keys", lift !== 0);
      deskLift = lift;
      document.documentElement.style.setProperty("--desk-lift", lift + "px");
      for (const z of [1, 2, 3, 4]) {
        for (const v of [0, 0.25, 0.5, 0.75, 1]) {
          zoom = z; cursorU = 0.5; cursorV = v;
          cursorFollow();
          const pic = pictureBox();
          const inset = bottomInset();
          const strip = { top: top, bottom: top + H - inset };
          const picTop = top + panY + H / 2 - (pic.height * z) / 2;
          const picBottom = picTop + pic.height * z;
          const pointer = picTop + v * pic.height * z;
          const middle = (strip.top + strip.bottom) / 2;
          rows.push({
            lift, z, v,
            // Black is any part of the strip the picture does not cover, on
            // an axis where the picture is big enough to have covered it.
            blackTop: Math.round(Math.max(0, picTop - strip.top)),
            blackBottom: Math.round(Math.max(0, strip.bottom - picBottom)),
            fits: pic.height * z <= (strip.bottom - strip.top) + 1,
            off: Math.round(pointer - middle),
            inset: Math.round(inset),
            room: Math.round(Math.max(0,
              (pic.height * z - (strip.bottom - strip.top)) / 2)),
            wanted: Math.round(Math.abs((v - 0.5) * pic.height * z)),
          });
        }
      }
    }
    const row = document.getElementById("desk-keys");
    row.hidden = true;
    document.getElementById("desk-dock").classList.remove("has-keys");
    deskLift = 0;
    document.documentElement.style.setProperty("--desk-lift", "0px");

    deskHeld = false; cursorOn = false;
    zoom = 2; panX = -99999; panY = -99999; applyZoom();
    const pic = pictureBox();
    return { rows, looking: { panX: Math.round(panX),
             limit: Math.round(panRoom(pic.width, pic.box.width, zoom)) } };
  });

  // No black, ever. That is the half of the rule that was asked for second:
  // at the edges the pointer gives up the middle rather than the picture
  // giving up the screen.
  // Proof that the keyboard cases measure a covered strip at all. Without it
  // this suite passed for a while while both of its cases were the same one.
  const measured = out.rows.filter((r) => r.lift === 300 && r.inset > 0);
  const insets = [...new Set(out.rows.map((r) => r.lift + ":" + r.inset))];
  check(measured.length > 0,
        "the keyboard cases actually measure something covering the bottom "
        + "(lift:inset seen = " + insets.join(", ") + ")");

  const bled = out.rows.filter((r) => !r.fits
                                      && (r.blackTop > 1 || r.blackBottom > 1));
  check(bled.length === 0,
        "a picture that fills the screen never lets black in at an edge, in "
        + out.rows.filter((r) => !r.fits).length + " such cases: "
        + (bled.length ? JSON.stringify(bled.slice(0, 2)) : "none"));

  // Centred wherever there is room to be.
  const shouldCentre = out.rows.filter((r) => r.wanted <= r.room);
  const missed = shouldCentre.filter((r) => Math.abs(r.off) > 1);
  check(shouldCentre.length > 0 && missed.length === 0,
        "the pointer is centred in all " + shouldCentre.length
        + " cases where the picture has room to move: "
        + (missed.length ? JSON.stringify(missed.slice(0, 2)) : "none missed"));

  // And where there is not, it walks towards the edge rather than dragging
  // the picture off with it.
  const atEdge = out.rows.filter((r) => r.wanted > r.room);
  check(atEdge.length > 0 && atEdge.every((r) => Math.abs(r.off) > 1),
        "and walks off-centre at the edges rather than showing black, in "
        + atEdge.length + " cases");

  // A picture smaller than the strip sits in the middle of it -- which is
  // where the keyboard comes in: the middle it is measured against moves.
  const small = out.rows.filter((r) => r.fits && r.v === 0.5);
  const wonky = small.filter((r) => Math.abs(r.blackTop - r.blackBottom) > 2);
  check(small.length > 0 && wonky.length === 0,
        "a picture shorter than the screen sits in the middle of what can be "
        + "seen, keyboard or no keyboard: "
        + (wonky.length ? JSON.stringify(wonky.slice(0, 2)) : "even on both sides"));

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
