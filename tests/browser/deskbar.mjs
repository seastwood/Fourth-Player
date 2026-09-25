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

  const one = await page.evaluate(() => ({
    bar: !document.getElementById("desk-bar").hidden,
    swap: getComputedStyle(document.getElementById("desk-swap")).display,
    // Direct children only: the pointer menu lives in the bar and has
    // buttons of its own, which are not buttons in the corner.
    buttons: Array.from(document.getElementById("desk-bar").children)
      .filter((n) => n.tagName === "BUTTON").map((b) => b.id),
    right: Math.round(innerWidth
      - document.getElementById("desk-swap").getBoundingClientRect().right),
  }));
  check(one.bar, "the bar is there for an admin");
  check(one.swap !== "none", "and the one button is showing");
  check(one.buttons.join(",") === "desk-swap",
        "which is the only button in it -- three standing over a thumbstick "
        + "was three too many, and there is nothing left to open: "
        + one.buttons.join(","));
  check(one.right <= 16, "and it stays in the corner");

  // What it offers is whichever one is not in front, and what it draws has to
  // agree -- there are no labelled buttons left to explain it.
  const offering = await page.evaluate(() => ({
    said: document.getElementById("desk-swap").getAttribute("aria-label"),
    kb: !document.querySelector("#desk-swap .desk-swap-kb").hidden,
    pad: !document.querySelector("#desk-swap .desk-swap-pad").hidden,
    padUp: !document.getElementById("touch").hidden,
  }));
  check(offering.padUp, "the controller is up to begin with");
  check(offering.said === "Show the keyboard" && offering.kb && !offering.pad,
        "so the button offers the keyboard, and draws one: "
        + JSON.stringify(offering));

  // Asking for the keyboard is asking for the keyboard and mouse, and the host
  // decides that. The same was true of the button this replaced; it is the
  // controller that needs nobody's permission.
  await page.tap("#desk-swap");
  await new Promise((r) => setTimeout(r, 250));
  const asked = await page.evaluate(() => window.__asked);
  check(asked.length === 1 && asked[0].t === "desk" && asked[0].take === true,
        "tapping for the keyboard asks the host for the desk: "
        + JSON.stringify(asked));
  check(await page.evaluate(() => !document.getElementById("touch").hidden),
        "and nothing changes on the glass until the answer comes");

  // With no controller up and no keyboard, it offers the controller -- which
  // is what somebody reaches for far more often than the other two.
  await page.evaluate(() => {
    window.__asked = [];
    setController(false);
    deskPaintKeys();
  });
  const offersPad = await page.evaluate(() => ({
    said: document.getElementById("desk-swap").getAttribute("aria-label"),
    pad: !document.querySelector("#desk-swap .desk-swap-pad").hidden,
  }));
  check(offersPad.said === "Show the controller" && offersPad.pad,
        "with neither up it offers the controller: "
        + JSON.stringify(offersPad));

  await page.tap("#desk-swap");
  await new Promise((r) => setTimeout(r, 250));
  const back = await page.evaluate(() => ({
    padUp: !document.getElementById("touch").hidden,
    asked: window.__asked.length,
    layout: (() => { try { return localStorage.getItem("fp:layout"); } catch (_) { return null; } })(),
  }));
  check(back.padUp, "and tapping it brings the controller back");
  check(back.asked === 0,
        "without asking the host for anything at all -- routing a guest's own "
        + "controller through 'may I have the keyboard' was a way of refusing "
        + "them their own pad: " + back.asked);
  check(back.layout === "nintendo",
        "leaving the remembered controller choice alone: " + back.layout);

  // The pointer is behind a hold, not a button of its own.
  const held = await page.evaluate(async () => {
    const menu = document.getElementById("cursor-menu");
    const before = menu.hidden;
    const button = document.getElementById("desk-swap");
    button.dispatchEvent(new PointerEvent("pointerdown",
                                          { bubbles: true, pointerType: "touch" }));
    await new Promise((r) => setTimeout(r, 700));
    return { before, after: menu.hidden };
  });
  check(held.before, "the pointer menu starts closed");
  check(!held.after,
        "and holding the button opens it -- the pointer is something reached "
        + "for occasionally, and a permanent button over a thumbstick was "
        + "more than it was worth");

  // Tapping the picture puts it away again.
  await page.tap("#screen");
  await new Promise((r) => setTimeout(r, 250));
  check(await page.evaluate(() => document.getElementById("cursor-menu").hidden),
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
    // Opening the pointer menu is not closing the keyboard.
    cursorMenuOpen(true); await wait(40);
    const afterMenu = deskWantKeyboard;
    cursorMenuOpen(false); await wait(40);
    // The one button, with the keyboard up: it offers the controller, and
    // taking it closes the keyboard.
    const button = document.getElementById("desk-swap");
    button.dispatchEvent(new PointerEvent("pointerdown",
                                          { bubbles: true, pointerType: "touch" }));
    button.dispatchEvent(new PointerEvent("pointerup",
                                          { bubbles: true, pointerType: "touch" }));
    button.click(); await wait(40);
    const afterSwap = deskWantKeyboard;
    return { opened, afterBlurs, afterMenu, afterSwap };
  });
  check(kb.opened, "the keyboard opens");
  check(kb.afterBlurs,
        "and survives losing focus over and over -- a double tap on the "
        + "picture makes several, and it used to give up after three");
  check(kb.afterMenu, "opening the pointer menu leaves it up");
  check(!kb.afterSwap,
        "and the one button closes it, because with the keyboard up what it "
        + "offers is the controller");

  // Nothing over the dock may refuse a touch's default.
  //
  // Twice now something has: first a page-wide double-tap-to-zoom guard, then
  // a handler on the strip meant to stop the keyboard closing when a key was
  // tapped. Both cancelled the native pan, so the key row would not scroll
  // sideways, and both suppressed the synthesised click, so the three buttons
  // and the collapse arrow did nothing at all -- the whole dock inert, and
  // only while the keyboard was up, which is the one time it is on screen.
  //
  // The checks above could not see it, because .click() fires the handler
  // directly and never goes near the touch pipeline. These dispatch a real
  // cancelable touchstart and ask whether anybody refused it.
  const guard = await page.evaluate(async () => {
    const wait = (ms) => new Promise((q) => setTimeout(q, ms));
    deskHeld = true; deskSend = () => {};
    deskShowKeyboard(true); await wait(50);
    const refused = (id) => {
      const node = document.getElementById(id);
      const touch = new Touch({ identifier: 1, target: node,
                                clientX: 10, clientY: 10 });
      const event = new TouchEvent("touchstart",
                                   { bubbles: true, cancelable: true,
                                     touches: [touch], targetTouches: [touch],
                                     changedTouches: [touch] });
      node.dispatchEvent(event);
      return event.defaultPrevented;
    };
    return { keys: refused("desk-keys"), bar: refused("desk-bar"),
             swap: refused("desk-swap"), up: deskWantKeyboard };
  });
  check(guard.up, "with the keyboard up, which is when the row is on screen");
  check(!guard.keys,
        "a touch on the key row is not refused, so the row can still scroll "
        + "sideways");
  check(!guard.bar, "nor one on the bar");
  check(!guard.swap, "nor one on the button itself");

  // And the whole way through, by tapping rather than clicking.
  const before = await page.evaluate(() => {
    deskHeld = true; deskSend = () => {};
    deskShowKeyboard(true);
    return deskWantKeyboard;
  });
  await page.tap("#desk-swap");
  await new Promise((r) => setTimeout(r, 300));
  const after = await page.evaluate(() => deskWantKeyboard);
  check(before && !after,
        "and a real tap on the button still works it while the keyboard is "
        + "up -- which is the one time the dock is on screen, and the one "
        + "time a touch guard could make the whole thing inert");

  check(errors.length === 0, "no script errors: " + errors.slice(0, 2));
} finally {
  await browser.close();
}

console.log();
if (fails.length) { console.log(fails.length + " FAILED"); process.exit(1); }
console.log("all good");
