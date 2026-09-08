/* A real admin taking a real keyboard and mouse, against a running host.
 *
 * Not in run.sh: it needs an open session, an account with `desk`, and it
 * types on the console. Run it by hand, from a machine with Chrome:
 *
 *   fourth-player admin add deskprobe --can desk
 *   fourth-player reshare
 *   LINK=... PIN=... USER=deskprobe PASS=... SECRET=... node tests/browser/desk.mjs
 *
 * What it is for: the unit tests stub the device and the page, so the one
 * thing they cannot show is that a keystroke leaves a browser, crosses the
 * reliable data channel and arrives at a uinput device. Run tests/live's
 * deskwatch.py on the box at the same time to see the other end.
 */
import puppeteer from "puppeteer-core";
import crypto from "crypto";

const { LINK, PIN, USER, PASS, SECRET } = process.env;

function code(offset = 0) {
  const bits = SECRET.replace(/=+$/, "").split("").map((ch) =>
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567".indexOf(ch).toString(2).padStart(5, "0")).join("");
  const key = Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
  const step = Math.floor(Date.now() / 30000) + offset;
  const c = Buffer.alloc(8);
  c.writeUInt32BE(Math.floor(step / 2 ** 32), 0); c.writeUInt32BE(step % 2 ** 32, 4);
  const d = crypto.createHmac("sha1", key).update(c).digest();
  const o = d[d.length - 1] & 15;
  return String(((d[o] & 0x7f) << 24 | d[o+1] << 16 | d[o+2] << 8 | d[o+3]) % 1000000)
    .padStart(6, "0");
}

const fails = [];
const check = (cond, msg) => {
  console.log((cond ? "  ok   " : "  FAIL ") + msg);
  if (!cond) fails.push(msg);
};
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({
  executablePath: "/usr/bin/google-chrome",
  args: ["--no-sandbox", "--disable-gpu", "--ignore-certificate-errors",
         "--autoplay-policy=no-user-gesture-required"],
});

try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(LINK, { waitUntil: "domcontentloaded" });
  await wait(2500);
  await page.evaluate((pin) => {
    document.getElementById("pin").value = pin;
    document.getElementById("pin-form")
      .dispatchEvent(new Event("submit", { cancelable: true }));
  }, PIN);
  await wait(6000);

  const joined = await page.evaluate(() => ({
    pads: !!(typeof input !== "undefined" && input && input.readyState === "open"),
    desk: !!(typeof deskChannel !== "undefined" && deskChannel
             && deskChannel.readyState === "open"),
    video: !!(document.getElementById("screen").srcObject),
  }));
  check(joined.pads, "the pad channel still opens, with a second one beside it");
  check(joined.desk, "and the desk channel opens too");
  check(joined.video, "and the picture still arrives");

  await page.evaluate((u, p, c) => {
    showTab("session");
    document.getElementById("login-open").click();
    document.getElementById("login-user").value = u;
    document.getElementById("login-pass").value = p;
    document.getElementById("login-code").value = c;
    document.getElementById("login-form")
      .dispatchEvent(new Event("submit", { cancelable: true }));
  }, USER, PASS, code());
  await wait(2500);

  const seen = await page.evaluate(() => ({
    name: account && account.name,
    block: !document.getElementById("session-desk").hidden,
    label: document.getElementById("desk-take").textContent.trim(),
  }));
  check(seen.name === USER, "logged in as the probe account: " + seen.name);
  check(seen.block, "an account with desk is offered the keyboard and mouse");
  check(/Take/.test(seen.label), "and is offered it as something to take");

  await page.evaluate(() => document.getElementById("desk-take").click());
  await wait(2000);

  const held = await page.evaluate(() => ({
    held: deskHeld,
    label: document.getElementById("desk-take").textContent.trim(),
  }));
  check(held.held, "taking it is granted to an account that may");
  check(/back/.test(held.label), "and the button now offers to give it back");

  /* Sent through deskSend rather than through the capture, because headless
     Chrome will not grant a pointer lock and the capture is the one part a
     person has to try by hand anyway. Everything past this call -- the
     channel, the host's check, the decode, the device -- is the real thing. */
  const sent = await page.evaluate(() => {
    if (!deskHeld) return "not holding it";
    deskSend([{ t: "k", c: "KeyF", d: 1 }, { t: "k", c: "KeyF", d: 0 },
              { t: "k", c: "KeyP", d: 1 }, { t: "k", c: "KeyP", d: 0 }]);
    deskSend([{ t: "m", dx: 25, dy: -10 }, { t: "w", dx: 0, dy: 2 }]);
    deskSend([{ t: "b", b: 2, d: 1 }, { t: "b", b: 2, d: 0 }]);
    return "";
  });
  check(sent === "", "typed, moved, scrolled and right-clicked: " + (sent || "sent"));
  await wait(1500);

  await page.evaluate(() => document.getElementById("desk-take").click());
  await wait(1500);
  const back = await page.evaluate(() => deskHeld);
  check(!back, "and giving it back is taken");

  check(errors.length === 0, "no script errors throughout: " + errors.slice(0, 2));
} finally {
  await browser.close();
}

console.log();
if (fails.length) { console.log(fails.length + " FAILED"); process.exit(1); }
console.log("all good");
