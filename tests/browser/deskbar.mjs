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

  check(errors.length === 0, "no script errors: " + errors.slice(0, 2));
} finally {
  await browser.close();
}

console.log();
if (fails.length) { console.log(fails.length + " FAILED"); process.exit(1); }
console.log("all good");
