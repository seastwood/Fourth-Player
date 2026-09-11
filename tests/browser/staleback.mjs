/* A page that comes back from being frozen must not go on showing what it
   last heard.

   A phone freezes a backgrounded tab without closing its socket, so anything
   the host broadcasts in that window is delivered to a page that is not
   running, and is gone by the time it is. The page then comes back with its
   socket still open -- which is the one path that gets no welcome -- and
   keeps whatever notice it had. "Controls paused" over a game that had
   plainly started, with closing and reopening the app the only cure. */
import puppeteer from "puppeteer-core";
const fails = [];
const check = (cond, msg) => {
  console.log((cond ? "  ok   " : "  FAIL ") + msg);
  if (!cond) fails.push(msg);
};
const browser = await puppeteer.launch({
  executablePath: process.env.FP_CHROME || "/usr/bin/google-chrome",
  args: ["--no-sandbox", "--disable-gpu"] });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.emulate({ name: "phone", userAgent: "iPhone Safari",
    viewport: { width: 390, height: 844, isMobile: true, hasTouch: true } });
  await page.goto(process.env.FP_PAGE, { waitUntil: "domcontentloaded" });
  await new Promise((r) => setTimeout(r, 700));

  const out = await page.evaluate(async () => {
    gate.hidden = true; stage.hidden = false; ended = false;
    window.__sent = [];
    send = (m) => window.__sent.push(m);
    // A socket and a peer that both still claim to be up, which is exactly
    // what a frozen tab wakes with.
    socket = { readyState: WebSocket.OPEN };
    pc = { connectionState: "connected" };

    // The host held the controls while this page was frozen.
    holdInput({ held: true, why: "a menu is in front" });
    const isHeld = () => document.documentElement.classList.contains("held");
    const whileAway = { held: isHeld() };

    window.__sent = [];
    cameBack();
    const asked = window.__sent.map((m) => m.t);

    // And the answer says the hold is over.
    stateFrom({ t: "state", hold: { held: false, why: "" } });
    return { whileAway, asked, after: isHeld() };
  });

  check(out.whileAway.held,
        "the page is holding when it goes away, as the host told it to");
  check(out.asked.includes("state"),
        "coming back asks the host where it stands, because this path gets no "
        + "welcome: " + JSON.stringify(out.asked));
  check(out.after === false,
        "and the answer lifts it, rather than waiting for the app to be "
        + "closed and reopened");

  check(errors.length === 0, "no script errors: " + errors.slice(0, 2));
} finally { await browser.close(); }
if (fails.length) { console.log("\n" + fails.length + " FAILED"); process.exit(1); }
console.log("\nall good");
