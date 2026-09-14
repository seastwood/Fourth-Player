/* That the game list keeps loading as you scroll, instead of stopping on
 * "Loading more..." and staying there for ever.
 *
 * An IntersectionObserver reports *crossings*, not states. The end-of-list
 * marker is already intersecting when it asks for a chunk, and drawing that
 * chunk pushes it down -- but only by the height of the chunk. Where that is
 * less than the 600px rootMargin the marker is still inside the observer's
 * reach afterwards, nothing has crossed anything, and no further callback is
 * ever delivered. The list stops dead, and scrolling cannot rescue it: the
 * marker is the last thing in the list, so there is nothing below it to
 * scroll towards and no crossing left to cause.
 *
 * How tall a chunk comes out decides whether a machine ever sees this, and
 * the answer is counter-intuitive -- the *wider* the window, the worse. The
 * grid is auto-fill at minmax(8.5rem, 1fr), so more width means more columns,
 * fewer rows per chunk, and a shorter chunk. Measured on this page, 300 games,
 * scrolling to the bottom ten times:
 *
 *     3840x1000   25 cols   2 rows    462px   96 -> 96 -> 96 ... stuck at 96
 *     2560x900    16 cols   3 rows    716px   96 -> 144 -> ... -> 300
 *     1920x1080   12 cols   4 rows    950px   96 -> 144 -> ... -> 300
 *     1440x900     9 cols   6 rows   1420px   48 -> 96 -> ... -> 300
 *
 * So this runs wide, and asserts the precondition rather than trusting it --
 * at 1920x1080 the bug does not reproduce at all, which is worth knowing
 * before anyone decides this test could use a more ordinary viewport.
 */
import puppeteer from "puppeteer-core";

const fails = [];
const check = (cond, msg) => {
  console.log((cond ? "  ok   " : "  FAIL ") + msg);
  if (!cond) fails.push(msg);
};

const TOTAL = 300;
const MARGIN = 600;              // the observer's rootMargin, as written
// Wide enough that a chunk is two rows. A 4K monitor, or an ultrawide.
const WIDE = { width: 3840, height: 1000 };
const PHONE = { width: 390, height: 844, isMobile: true, hasTouch: true };

const browser = await puppeteer.launch({
  executablePath: process.env.FP_CHROME || "/usr/bin/google-chrome",
  args: ["--no-sandbox", "--disable-gpu"],
});

/* Fill the shelf the way the host does. art:true takes the same branch of
 * makeCard a real library does -- the box keeps its 3/4 aspect whether the
 * picture arrives or not, so the card is the same height either way. */
const fillShelf = (page, n) => page.evaluate((total) => {
  gate.hidden = true;
  stage.hidden = false;
  const games = [];
  for (let i = 0; i < total; i++) {
    games.push({ id: "g" + i, label: "Game number " + i, short: "SNES",
                 system: "snes", players: 2, bucket: "2", saved: false,
                 art: true });
  }
  el("browser").hidden = false;
  // policy has to be something other than "off": launchPolicy closes the
  // browser outright for "off", and then there is nothing to scroll.
  paintShelf({ games, systems: [], policy: "open" });
}, n);

const drawn = (page) => page.evaluate(() => {
  const marker = document.querySelector("#shelf .shelf-end");
  return {
    cards: document.querySelectorAll("#shelf .card").length,
    markerHidden: marker ? marker.hidden : null,
    markerText: marker ? marker.textContent.trim() : null,
  };
});

/* Scroll to the bottom repeatedly, and report where it got to. Returns as
 * soon as the target is reached; gives up after enough rounds that a working
 * list would certainly have finished. */
async function scrollToEnd(page, want) {
  let seen = -1;
  for (let round = 0; round < 40; round++) {
    const now = await drawn(page);
    if (now.cards >= want) return now;
    if (now.cards === seen) await new Promise((r) => setTimeout(r, 250));
    seen = now.cards;
    await page.evaluate(() => { el("shelf").scrollTop = el("shelf").scrollHeight; });
    await new Promise((r) => setTimeout(r, 200));
  }
  return drawn(page);
}

try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.setViewport(WIDE);
  await page.goto(process.env.FP_PAGE, { waitUntil: "domcontentloaded" });
  await new Promise((r) => setTimeout(r, 700));

  await fillShelf(page, TOTAL);
  await new Promise((r) => setTimeout(r, 1500));

  const chunk = await page.evaluate(() => SHELF_CHUNK);

  // -- the precondition, computed rather than caught mid-cascade ------------
  //
  // Measuring where the marker sits right now would race the cascade this is
  // testing, so work it out from the grid: how far one chunk moves the
  // marker, against how far the observer can see past the fold. Less means a
  // draw can never carry the marker out of reach, so a draw can never cause
  // the crossing that would ask for the next one. That is the trap, stated as
  // a number rather than hoped for.
  const geometry = await page.evaluate((n) => {
    const shelf = el("shelf");
    const card = shelf.querySelector(".card");
    const style = getComputedStyle(shelf);
    const columns = style.gridTemplateColumns.split(/\s+/).length;
    const rows = Math.ceil(n / columns);
    const gap = parseFloat(style.rowGap) || 0;
    return {
      columns, rows,
      chunkHeight: Math.round(rows * (card.getBoundingClientRect().height + gap)),
    };
  }, chunk);
  console.log(`     grid: ${geometry.columns} columns, so one chunk is `
              + `${geometry.rows} rows / ${geometry.chunkHeight}px, `
              + `against a ${MARGIN}px margin`);
  check(geometry.chunkHeight < MARGIN,
        "this window is one where drawing a chunk cannot push the marker out "
        + "of the observer's reach, so no crossing can ever follow a draw -- "
        + "which is the case that used to stop dead");

  // Reported rather than checked, deliberately. The broken code also draws
  // more than one chunk here -- shelfMarker() appendChild()s the marker on
  // every draw, and moving it is itself worth one crossing -- so "more than a
  // chunk is drawn" cannot tell the two apart and has no business being a
  // check. Measured: 96 broken, 192 fixed. The discriminating checks are the
  // three below.
  const settled = await drawn(page);
  console.log(`     drawn before anything is scrolled: ${settled.cards} of `
              + `${TOTAL}, one chunk being ${chunk}`);

  // -- and scrolling reaches the end, which is the reported symptom ---------
  const end = await scrollToEnd(page, TOTAL);
  check(end.cards === TOTAL,
        `scrolling down reaches the end of the list: ${end.cards} of ${TOTAL}`);
  check(end.markerHidden === true,
        "and the marker is hidden once there is nothing left to load, rather "
        + `than reading "${end.markerText}" for ever`);

  // -- a filtered list is a new list, with a new marker --------------------
  //
  // filterShelf disconnects the observer and empties the shelf, so the
  // replacement marker has to be picked up as well as the first one.
  await page.evaluate(() => { el("q").value = "Game number 1"; filterShelf(); });
  await new Promise((r) => setTimeout(r, 1500));
  const matching = await page.evaluate(() => shelfShown.length);
  // Stated so this cannot pass on an empty list: a filter that matched
  // nothing would satisfy "everything is drawn" without drawing anything,
  // which is the exact shape of a test that passes on broken code.
  check(matching > chunk,
        `the filter leaves more than one chunk to draw: ${matching} matching`);
  const filtered = await scrollToEnd(page, matching);
  check(filtered.cards === matching,
        "and a filtered list goes on loading past its first chunk too: "
        + `${filtered.cards} of ${matching}`);

  // -- a phone was never broken; keep it that way -------------------------
  //
  // Switching isMobile reloads the page in Chrome, so everything above is
  // gone by here and the shelf has to be filled again. Measured, after this
  // silently emptied the list once and the checks below passed on nothing.
  await page.setViewport(PHONE);
  await new Promise((r) => setTimeout(r, 500));
  await fillShelf(page, TOTAL);
  await new Promise((r) => setTimeout(r, 1200));
  const narrow = await scrollToEnd(page, TOTAL);
  check(narrow.cards === TOTAL,
        `a narrow window still reaches the end too: ${narrow.cards} of ${TOTAL}`);

  check(errors.length === 0, "no script errors: " + errors.join(" | "));
} finally {
  await browser.close();
}

console.log("");
if (fails.length) {
  console.log(`shelfmore: ${fails.length} FAILED`);
  process.exit(1);
}
console.log("shelfmore: all ok");
