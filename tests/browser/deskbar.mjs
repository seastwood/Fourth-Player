/* The bar over the picture: that it collapses, that it opens, and that the
   controller button is not a desk control.

   Against the static page rather than a live host, because none of this needs
   one: what is being asked is whether a tap on a button does what the button
   says, with the on-screen controller underneath it. */
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

  // Joined, logged in as the primary admin, controller showing.
  await page.evaluate(() => {
    gate.hidden = true; stage.hidden = false;
    account = { name: "seth", can: [], primary: true, fresh: true };
    try { localStorage.setItem("fp:layout", "nintendo"); } catch (_) {}
    applyLayoutChoice("nintendo");
    deskPaintKeys();
    window.__asked = [];
    window.act = (m) => window.__asked.push(m);
  });

  const shut = await page.evaluate(() => ({
    bar: !document.getElementById("desk-bar").hidden,
    open: document.getElementById("desk-bar").classList.contains("is-open"),
    more: getComputedStyle(document.getElementById("desk-more")).display,
    pad: getComputedStyle(document.getElementById("desk-pad")).display,
  }));
  check(shut.bar, "the bar is there for an admin");
  check(!shut.open, "and starts collapsed");
  check(shut.more !== "none", "the toggle is what is showing");
  check(shut.pad === "none",
        "and the three are not, so nothing stands over a thumbstick");

  await page.tap("#desk-more");
  await new Promise((r) => setTimeout(r, 250));
  const open = await page.evaluate(() => ({
    open: document.getElementById("desk-bar").classList.contains("is-open"),
    pad: getComputedStyle(document.getElementById("desk-pad")).display,
    said: document.getElementById("desk-more").getAttribute("aria-expanded"),
    corner: (() => {
      const more = document.getElementById("desk-more").getBoundingClientRect();
      const pad = document.getElementById("desk-pad").getBoundingClientRect();
      return { moreRight: Math.round(innerWidth - more.right), leftOf: pad.left < more.left };
    })(),
  }));
  check(open.open && open.pad !== "none", "tapping it opens the three");
  check(open.said === "true", "and says so");
  check(open.corner.moreRight <= 16 && open.corner.leftOf,
        "the toggle stays in the corner and the rest open to its left");

  // The controller button must not ask anybody for permission.
  await page.tap("#desk-pad");
  await new Promise((r) => setTimeout(r, 250));
  const padded = await page.evaluate(() => ({
    hidden: document.getElementById("touch").hidden,
    asked: window.__asked.length,
    lit: document.getElementById("desk-pad").classList.contains("is-on"),
  }));
  check(padded.hidden, "the controller button hides the controller");
  check(padded.asked === 0,
        "without asking the host for anything at all: " + padded.asked);
  check(!padded.lit, "and stops being lit, because the pad is not showing");

  await page.tap("#desk-pad");
  await new Promise((r) => setTimeout(r, 250));
  const back = await page.evaluate(() => ({
    hidden: document.getElementById("touch").hidden,
    asked: window.__asked.length,
    layout: (() => { try { return localStorage.getItem("fp:layout"); } catch (_) { return null; } })(),
  }));
  check(!back.hidden, "and brings it back on a second tap");
  check(back.asked === 0, "still without asking for the desk");
  check(back.layout === "nintendo",
        "leaving the remembered controller choice alone: " + back.layout);

  // The cursor does ask, because that one really is the desk.
  await page.tap("#desk-cursor");
  await new Promise((r) => setTimeout(r, 250));
  const asked = await page.evaluate(() => window.__asked);
  check(asked.length === 1 && asked[0].t === "desk" && asked[0].take === true,
        "the cursor asks for the keyboard and mouse: " + JSON.stringify(asked));

  // Tapping the picture shuts the bar again.
  await page.evaluate(() => deskOpen(true));
  await page.tap("#screen");
  await new Promise((r) => setTimeout(r, 250));
  check(!(await page.evaluate(() =>
            document.getElementById("desk-bar").classList.contains("is-open"))),
        "tapping the picture shuts it");

  // A note about the desk must not cost the picture anything.
  const notes = await page.evaluate(async () => {
    stage.classList.add("immersive"); immersive = true;
    const before = video.offsetHeight;
    showToast("Somebody picked up the mouse.");
    await new Promise((r) => setTimeout(r, 100));
    const quiet = { h: video.offsetHeight, immersive,
                    taps: getComputedStyle(document.getElementById("toast"))
                            .pointerEvents };
    showNotice("<p>Something that needs reading.</p>", false);
    await new Promise((r) => setTimeout(r, 100));
    return { before, quiet, loudH: video.offsetHeight };
  });
  check(notes.quiet.h === notes.before,
        "a quiet note costs the picture no height: " + notes.before
        + " before, " + notes.quiet.h + " after");
  check(notes.quiet.immersive,
        "and does not drag the page out of the stripped-back view");
  check(notes.quiet.taps === "none",
        "and takes no taps, so dismissing it cannot cost anybody their keyboard");
  check(notes.loudH < notes.before,
        "while a banner still does both, for the things that have to be read: "
        + notes.loudH + " against " + notes.before);

  // "Controls paused" is the answer to a question a desk holder is not asking.
  const paused = await page.evaluate(async () => {
    hideNotice();
    stage.classList.add("immersive"); immersive = true;
    const tall = video.offsetHeight;
    deskHeld = false;
    holdInput({ held: true, driving: false });
    const guest = { h: video.offsetHeight,
                    banner: !document.getElementById("notice").hidden };
    hideNotice(); stage.classList.add("immersive"); immersive = true;
    deskHeld = true;
    holdInput({ held: true, driving: false });
    const admin = { h: video.offsetHeight, immersive,
                    banner: !document.getElementById("notice").hidden,
                    held: document.documentElement.classList.contains("held") };
    deskHeld = false; hideNotice();
    return { tall, guest, admin };
  });
  check(paused.guest.banner && paused.guest.h < paused.tall,
        "a guest whose controller is paused is still told why, banner and all: "
        + paused.guest.h + " against " + paused.tall);
  check(!paused.admin.banner && paused.admin.h === paused.tall
        && paused.admin.immersive,
        "somebody holding the keyboard and mouse is not, and keeps every pixel "
        + "of the picture: " + paused.admin.h);
  check(paused.admin.held,
        "though the page still knows the pad is held, so everything else that "
        + "says so is right");

  // The keyboard closes when it is asked to, and at no other time.
  const kb = await page.evaluate(async () => {
    const wait = (ms) => new Promise((q) => setTimeout(q, ms));
    const field = document.getElementById("desk-input");
    deskHeld = true; deskSend = () => {};
    deskShowKeyboard(true); await wait(50);
    const opened = deskWantKeyboard;
    // A double tap on the picture and a few taps on the buttons each blur it.
    for (let i = 0; i < 6; i++) { field.blur(); await wait(15); }
    const afterBlurs = deskWantKeyboard;
    document.getElementById("desk-more").click(); await wait(40);
    const afterArrow = deskWantKeyboard;
    document.getElementById("desk-cursor").click(); await wait(40);
    const afterCursor = deskWantKeyboard;
    document.getElementById("desk-kb").click(); await wait(40);
    const afterKeyboard = deskWantKeyboard;
    deskShowKeyboard(true); await wait(40);
    document.getElementById("desk-pad").click(); await wait(40);
    const afterController = deskWantKeyboard;
    return { opened, afterBlurs, afterArrow, afterCursor, afterKeyboard,
             afterController };
  });
  check(kb.opened, "the keyboard opens");
  check(kb.afterBlurs,
        "and survives losing focus over and over -- a double tap on the "
        + "picture makes several, and it used to give up after three");
  check(kb.afterArrow, "tapping the arrow that collapses the buttons leaves it up");
  check(kb.afterCursor, "so does tapping the cursor");
  check(!kb.afterKeyboard, "the keyboard button closes it");
  check(!kb.afterController, "and so does asking for the controller");

  check(errors.length === 0, "no script errors: " + errors.slice(0, 2));
} finally {
  await browser.close();
}

console.log();
if (fails.length) { console.log(fails.length + " FAILED"); process.exit(1); }
console.log("all good");
