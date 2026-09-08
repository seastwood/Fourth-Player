/* The phone keyboard, against a running host.
 *
 * Not in run.sh: it needs an open session and an account with `desk`. Same
 * setup as desk.mjs, and worth running with tests/live/deskwatch.py on the
 * box so the characters can be seen arriving at the device.
 *
 * A phone is emulated rather than assumed: touch, a phone viewport, and the
 * beforeinput events an on-screen keyboard actually produces -- which is the
 * whole point, because keydown from one of those carries nothing useful.
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
  await page.emulate({
    name: "phone",
    userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      + "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile Safari/604.1",
    viewport: { width: 390, height: 844, isMobile: true, hasTouch: true,
                deviceScaleFactor: 3 },
  });
  await page.goto(LINK, { waitUntil: "domcontentloaded" });
  await wait(2500);
  await page.evaluate((pin) => {
    document.getElementById("pin").value = pin;
    document.getElementById("pin-form")
      .dispatchEvent(new Event("submit", { cancelable: true }));
  }, PIN);
  await wait(6000);

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

  const before = await page.evaluate(() => ({
    kb: document.getElementById("desk-kb").hidden,
    row: document.getElementById("desk-keys").hidden,
  }));
  check(before.kb, "no keyboard button before anybody holds the desk");
  check(before.row, "and no key row either");

  await page.evaluate(() => document.getElementById("desk-take").click());
  await wait(2000);
  const holding = await page.evaluate(() => ({
    held: deskHeld,
    kb: document.getElementById("desk-kb").hidden,
    row: document.getElementById("desk-keys").hidden,
  }));
  check(holding.held, "the desk is taken");
  check(!holding.kb, "the keyboard button appears with it");
  check(holding.row, "and the key row waits until the keyboard is actually up");

  const raised = await page.evaluate(() => {
    document.getElementById("desk-kb").click();
    return { up: deskKeyboardUp(),
             row: document.getElementById("desk-keys").hidden,
             on: document.getElementById("desk-kb").classList.contains("is-on") };
  });
  check(raised.up, "tapping it focuses the field, which is what raises a keyboard");
  check(!raised.row, "the key row comes up with it");
  check(raised.on, "and the button shows it is on");

  /* What an on-screen keyboard actually sends. Not keydown: iOS and Android
     both report those with no usable code, which is the reason any of this
     exists. */
  const typed = await page.evaluate(() => {
    const sent = [];
    const real = deskSend;
    deskSend = (list) => sent.push(...list);
    const field = document.getElementById("desk-input");
    for (const [type, data] of [["insertText", "Hi!"],
                                ["deleteContentBackward", null],
                                ["insertLineBreak", null]]) {
      field.dispatchEvent(new InputEvent("beforeinput",
        { inputType: type, data, cancelable: true, bubbles: true }));
    }
    deskSend = real;
    return sent;
  });
  const chars = typed.filter((m) => m.t === "c").map((m) => m.ch).join("");
  check(chars === "Hi!", "typed characters go as characters: " + chars);
  check(typed.some((m) => m.t === "k" && m.c === "Backspace"),
        "a backspace goes as the key it is");
  check(typed.some((m) => m.t === "k" && m.c === "Enter"),
        "and so does a newline");

  const mods = await page.evaluate(() => {
    const sent = [];
    const real = deskSend;
    deskSend = (list) => sent.push(...list);
    const ctrl = document.querySelector('#desk-keys [data-mod="ControlLeft"]');
    ctrl.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    const latched = ctrl.classList.contains("is-latched");
    document.querySelector('#desk-keys [data-key="Tab"]')
      .dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    const after = { latched, spent: !ctrl.classList.contains("is-latched"), sent };
    ctrl.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    ctrl.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    after.locked = ctrl.classList.contains("is-locked");
    deskSend = real;
    return after;
  });
  check(mods.latched, "one tap on ctrl latches it");
  check(mods.sent.some((m) => m.c === "ControlLeft" && m.d === 1),
        "and holds it down on the console, which is what a modifier is");
  check(mods.spent, "the next key spends it");
  check(mods.sent.filter((m) => m.c === "ControlLeft" && m.d === 0).length === 1,
        "letting go exactly once: " + JSON.stringify(mods.sent));
  check(mods.locked, "and two taps lock it instead");

  await page.evaluate(() => document.getElementById("desk-take").click());
  await wait(1500);
  const gone = await page.evaluate(() => ({
    held: deskHeld,
    kb: document.getElementById("desk-kb").hidden,
    up: deskKeyboardUp(),
    mods: deskMods.size,
  }));
  check(!gone.held && gone.kb, "giving the desk back takes the button away");
  check(!gone.up, "and puts the keyboard away with it");
  check(gone.mods === 0, "leaving no modifier held on somebody's computer");

  check(errors.length === 0, "no script errors throughout: " + errors.slice(0, 2));
} finally {
  await browser.close();
}

console.log();
if (fails.length) { console.log(fails.length + " FAILED"); process.exit(1); }
console.log("all good");
