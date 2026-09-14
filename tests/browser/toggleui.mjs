/* Choosing which buttons latch, on the real page.
 *
 * The logic is covered by padtoggle.mjs against the lifted source. This is
 * the other half: that the mode button exists, that clicking a cell in the
 * grid while it is on marks that button and writes it down, that the mark is
 * visible, and that "Use defaults" takes it away again. Those are all things
 * a rule-level check would pass on a page nobody can actually use.
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
  await page.setViewport({ width: 1100, height: 900 });
  await page.goto(process.env.FP_PAGE, { waitUntil: "domcontentloaded" });
  await new Promise((r) => setTimeout(r, 700));

  // In a session with the controls panel open, set up for a controller.
  await page.evaluate(() => {
    gate.hidden = true;
    stage.hidden = false;
    padName = "Test Pad";
    openPads();
    buildPadsGrid();
  });

  const start = await page.evaluate(() => ({
    hasButton: !!el("pads-toggles"),
    pressed: el("pads-toggles").getAttribute("aria-pressed"),
    noteHidden: el("pads-toggle-note").hidden,
    marked: document.querySelectorAll("#pads-grid .key.is-toggle").length,
  }));
  check(start.hasButton, "the panel offers a way to choose latching buttons");
  check(start.pressed === "false" && start.noteHidden,
        "which is off to begin with, and says nothing until it is on");
  check(start.marked === 0, "and nothing latches on a controller never set up");

  // Turn the mode on and click RT (index 7).
  await page.evaluate(() => el("pads-toggles").click());
  const on = await page.evaluate(() => ({
    pressed: el("pads-toggles").getAttribute("aria-pressed"),
    label: el("pads-toggles").textContent.trim(),
    noteHidden: el("pads-toggle-note").hidden,
  }));
  check(on.pressed === "true" && !on.noteHidden,
        "turning it on says so and explains what the grid now means");
  check(on.label === "Done",
        `and the button becomes the way out of it: ${on.label}`);

  await page.evaluate(() => el("key7").click());
  const picked = await page.evaluate(() => ({
    marked: Array.from(document.querySelectorAll("#pads-grid .key.is-toggle"))
      .map((c) => c.id),
    stored: localStorage.getItem("fp-padtoggle:Test Pad"),
    // The mark has to be visible, not merely present in the class list.
    edge: getComputedStyle(el("key7")).borderLeftWidth,
    plain: getComputedStyle(el("key6")).borderLeftWidth,
  }));
  check(JSON.stringify(picked.marked) === '["key7"]',
        `clicking a button marks that one and only that one: ${picked.marked}`);
  check(picked.stored === "[7]",
        `and writes it down under the controller's name: ${picked.stored}`);
  check(picked.edge !== picked.plain,
        `the mark is actually drawn: ${picked.edge} against ${picked.plain}`);

  // Clicking it again takes it back off.
  await page.evaluate(() => el("key7").click());
  const off = await page.evaluate(() => ({
    marked: document.querySelectorAll("#pads-grid .key.is-toggle").length,
    stored: localStorage.getItem("fp-padtoggle:Test Pad"),
  }));
  check(off.marked === 0 && off.stored === null,
        "clicking it again unmarks it and forgets it rather than storing []");

  // While the mode is on, a click must not start the learning walk instead.
  await page.evaluate(() => { el("key7").click(); });
  const notLearning = await page.evaluate(() => learnTarget);
  check(notLearning < 0,
        `a click in this mode picks a toggle rather than starting to learn `
        + `that button: learnTarget ${notLearning}`);

  // And "Use defaults" clears it, the way it clears the map and the sticks.
  await page.evaluate(() => {
    el("pads-toggles").click();          // back out of the mode
    el("pads-reset").click();
  });
  const reset = await page.evaluate(() => ({
    stored: localStorage.getItem("fp-padtoggle:Test Pad"),
    marked: document.querySelectorAll("#pads-grid .key.is-toggle").length,
    mode: el("pads-toggles").getAttribute("aria-pressed"),
  }));
  check(reset.stored === null && reset.marked === 0,
        "\"Use defaults\" undoes the latching too, not most of it");
  check(reset.mode === "false", "and leaves the mode off");

  // ---- the on-screen controller ----------------------------------------
  //
  // Asked for directly: "I want this to work for on screen control buttons as
  // well, and it should show the button as visually pressed if it is
  // toggled." The glass is where it matters most -- a thumb cannot rest on a
  // button there while doing anything else.
  console.log("");
  const built = await page.evaluate(() => {
    showTouch(true);
    closePads();
    localStorage.setItem("fp-padtoggle:Test Pad", JSON.stringify([0]));
    ownPadName = "Test Pad";
    return document.querySelectorAll(".tbtn[data-button]").length;
  });
  check(built > 0, `the on-screen pad has buttons to press: ${built}`);

  const tap = (bit, down) => page.evaluate(
    ([b, d]) => { setBit(b, d); return latchMask(ownLatch); }, [bit, down]);

  let mask = await tap(0, true);
  let shown = await page.evaluate(() => {
    const b = document.querySelector('.tbtn[data-button="0"]');
    return { held: b.classList.contains("held"),
             pressed: b.getAttribute("aria-pressed") };
  });
  check(mask === 1, `tapping a latching button on the glass holds it: mask ${mask}`);
  check(shown.held, "and the button is drawn pressed");
  check(shown.pressed === "true", "and says so to a screen reader");

  mask = await tap(0, false);
  shown = await page.evaluate(() =>
    document.querySelector('.tbtn[data-button="0"]').classList.contains("held"));
  check(mask === 1 && shown,
        "taking the finger off leaves it down, which is the whole point");

  // The frame has to carry it, not just the glass.
  const inFrame = await page.evaluate(() => ({
    latch: latchMask(ownLatch),
    plain: touchButtons,
  }));
  check(inFrame.latch === 1,
        "the latch is what puts it in the frame, so the game is told it is down");
  check(inFrame.plain === 0,
        "and the ordinary held-button bit is not set as well, which would be "
        + "two sources for one button");

  mask = await tap(0, true);
  await tap(0, false);
  shown = await page.evaluate(() =>
    document.querySelector('.tbtn[data-button="0"]').classList.contains("held"));
  check(mask === 0 && !shown, "tapping it again lets go, and stops drawing it");

  // The d-pad calls setBit on every pointermove while a thumb slides about on
  // it, so "still held" arrives over and over and only the first is a press.
  await page.evaluate(() => {
    localStorage.setItem("fp-padtoggle:Test Pad", JSON.stringify([12]));
    clearLatch(ownLatch);
  });
  let flips = 0, was = 0;
  for (let frame = 0; frame < 30; frame++) {
    const now = await tap(12, true);
    if (now !== was) flips++;
    was = now;
  }
  check(flips === 1,
        `thirty frames of a thumb held on the d-pad flip it once: ${flips}`);
  const arm = await page.evaluate(() =>
    document.querySelector('.dpad-arm[data-dir="up"]').classList.contains("held"));
  check(arm, "and the d-pad arm is drawn held too, not only the round buttons");

  // A button that does not latch is untouched by any of this.
  await page.evaluate(() => {
    localStorage.setItem("fp-padtoggle:Test Pad", JSON.stringify([0]));
    clearLatch(ownLatch);
    touchButtons = 0;
  });
  const plain = await page.evaluate(() => {
    setBit(1, true);
    const down = touchButtons;
    setBit(1, false);
    return { down, up: touchButtons, latch: latchMask(ownLatch) };
  });
  check(plain.down === 2 && plain.up === 0 && plain.latch === 0,
        "a button nobody asked to latch is held while touched and let go after");

  // Putting the page away must not leave anything held on the television.
  await page.evaluate(() => { setBit(0, true); releaseAllTouch(); });
  const after = await page.evaluate(() => ({
    latch: latchMask(ownLatch),
    held: document.querySelectorAll(".tbtn.held, .dpad-arm.held").length,
  }));
  check(after.latch === 0 && after.held === 0,
        "releasing everything lets go of the latch and stops drawing it");

  // The mark has to be visible, not merely in the class list.
  const look = await page.evaluate(() => {
    const b = document.querySelector('.tbtn[data-button="0"]');
    const plainStyle = getComputedStyle(b).boxShadow;
    b.classList.add("held");
    const heldStyle = getComputedStyle(b).boxShadow;
    b.classList.remove("held");
    return { plainStyle, heldStyle };
  });
  check(look.heldStyle !== look.plainStyle && look.heldStyle !== "none",
        `a latched button is actually drawn differently: ${look.heldStyle}`);

  check(errors.length === 0, "no script errors: " + errors.join(" | "));
} finally {
  await browser.close();
}

console.log("");
if (fails.length) {
  console.log(`toggleui: ${fails.length} FAILED`);
  process.exit(1);
}
console.log("toggleui: all ok");
