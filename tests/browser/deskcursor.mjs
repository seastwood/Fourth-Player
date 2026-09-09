/* The three buttons and the trackball cursor, against a running host.
 *
 * Not in run.sh: it needs an open session and an account with `desk`.
 *
 *   fourth-player admin add deskprobe --can desk
 *   fourth-player reshare
 *   LINK=... PIN=... USER=... PASS=... SECRET=... node tests/browser/deskcursor.mjs
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

  check(await page.evaluate(() => document.getElementById("desk-bar").hidden),
        "a guest with no account is offered none of this");

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
  await page.evaluate(() => showTab("game"));

  const bar = await page.evaluate(() => ({
    shown: !document.getElementById("desk-bar").hidden,
    order: Array.from(document.querySelectorAll("#desk-bar button")).map((b) => b.id),
    held: deskHeld,
  }));
  check(bar.shown, "an account that holds desk is offered the bar at once");
  check(!bar.held, "before it has taken anything");
  check(bar.order.join(",") === "desk-pad,desk-cursor,desk-kb",
        "controller, cursor, keyboard, in that order: " + bar.order.join(","));

  // Pressing cursor asks for the desk and then does what was asked.
  await page.evaluate(() => document.getElementById("desk-cursor").click());
  await wait(2500);
  const on = await page.evaluate(() => ({
    held: deskHeld, cursor: cursorOn, driving: cursorDriving(),
    pad: document.getElementById("touch").hidden,
    lit: document.getElementById("desk-cursor").classList.contains("is-on"),
  }));
  check(on.held, "one press takes the desk");
  check(on.cursor && on.driving, "and turns the cursor on, in the same press");
  check(on.pad, "the controller goes away, because the finger cannot do both");
  check(on.lit, "and the button says so");

  /* A drag. The page owns the pointer's position, so what it sends can be
     read straight back out of the messages. */
  const dragged = await page.evaluate(async () => {
    const sent = [];
    const real = deskSend;
    deskSend = (list) => sent.push(...list);
    cursorU = 0.5; cursorV = 0.5;
    const box = document.getElementById("screen").getBoundingClientRect();
    const at = (x, y, type, id) => document.getElementById("screen")
      .dispatchEvent(new PointerEvent(type, { pointerId: id, pointerType: "touch",
        clientX: x, clientY: y, bubbles: true, cancelable: true }));
    const x0 = box.left + box.width / 2, y0 = box.top + box.height / 2;
    at(x0, y0, "pointerdown", 1);
    for (let i = 1; i <= 5; i++) at(x0 + i * 8, y0 + i * 4, "pointermove", 1);
    at(x0 + 40, y0 + 20, "pointerup", 1);
    const after = { u: cursorU, v: cursorV, sent };
    deskSend = real;
    return after;
  });
  const points = dragged.sent.filter((m) => m.t === "p");
  check(points.length >= 5, "a drag sends positions: " + points.length);
  check(dragged.u > 0.5 && dragged.v > 0.5,
        "rightwards and downwards moved it that way: "
        + dragged.u.toFixed(3) + "," + dragged.v.toFixed(3));
  check(points.every((m) => Number.isInteger(m.x) && m.x >= 0 && m.x <= 32767),
        "every position is a whole number on the agreed scale");

  // The edges hold.
  const edge = await page.evaluate(() => {
    const sent = [];
    const real = deskSend;
    deskSend = (list) => sent.push(...list);
    cursorU = 0.5; cursorV = 0.5;
    cursorMove(9, 9);
    const far = { u: cursorU, v: cursorV };
    cursorMove(-99, -99);
    const near = { u: cursorU, v: cursorV };
    deskSend = real;
    return { far, near };
  });
  check(edge.far.u === 1 && edge.far.v === 1, "it stops at the far edge");
  check(edge.near.u === 0 && edge.near.v === 0, "and at the near one");

  // A tap is a click where the pointer already is, not a move to the finger.
  const tapped = await page.evaluate(async () => {
    const sent = [];
    const real = deskSend;
    deskSend = (list) => sent.push(...list);
    cursorU = 0.25; cursorV = 0.25;
    const box = document.getElementById("screen").getBoundingClientRect();
    const at = (x, y, type) => document.getElementById("screen")
      .dispatchEvent(new PointerEvent(type, { pointerId: 2, pointerType: "touch",
        clientX: x, clientY: y, bubbles: true, cancelable: true }));
    at(box.left + 10, box.top + 10, "pointerdown");
    at(box.left + 10, box.top + 10, "pointerup");
    await new Promise((r) => setTimeout(r, 50));
    const after = { sent, u: cursorU, v: cursorV };
    deskSend = real;
    return after;
  });
  check(tapped.sent.some((m) => m.t === "b" && m.b === 0 && m.d === 1),
        "a tap clicks: " + JSON.stringify(tapped.sent));
  check(tapped.u === 0.25 && tapped.v === 0.25,
        "and does not drag the pointer to the finger");

  // Zoomed in, the view follows the pointer and stops at the picture's edge.
  const followed = await page.evaluate(async () => {
    const real = deskSend;
    deskSend = () => {};
    zoom = 2; panX = 0; panY = 0; applyZoom();
    cursorU = 0.5; cursorV = 0.5; cursorFollow();
    const middle = { panX, panY };
    cursorU = 0.65; cursorFollow();
    const moved = { panX, panY };
    cursorU = 1; cursorFollow();
    const far = { panX, panY };
    const picture = pictureBox();
    const room = panRoom(picture.width, picture.box.width, zoom);
    zoom = 1; applyZoom();
    deskSend = real;
    return { middle, moved, far, room };
  });
  check(Math.abs(followed.middle.panX) < 0.5,
        "a pointer in the middle needs no pan: " + followed.middle.panX);
  check(followed.moved.panX < -1,
        "moving it right pushes the picture left, keeping it centred: "
        + followed.moved.panX.toFixed(1));
  check(Math.abs(followed.far.panX + followed.room) < 0.5,
        "and at the far edge the view stops exactly there, letting the "
        + "pointer walk on: " + followed.far.panX.toFixed(1)
        + " against a limit of " + (-followed.room).toFixed(1));

  // A flick leaves it coasting; a finger back down stops it dead.
  const flicked = await page.evaluate(async () => {
    const real = deskSend;
    deskSend = () => {};
    cursorU = 0.5; cursorV = 0.5;
    const screen = document.getElementById("screen");
    const box = screen.getBoundingClientRect();
    const at = (x, y, type) => screen.dispatchEvent(new PointerEvent(type,
      { pointerId: 7, pointerType: "touch", clientX: x, clientY: y,
        bubbles: true, cancelable: true }));
    const x0 = box.left + 40, y0 = box.top + box.height / 2;
    at(x0, y0, "pointerdown");
    for (let i = 1; i <= 4; i++) at(x0 + i * 22, y0, "pointermove");
    at(x0 + 88, y0, "pointerup");
    const started = coasting !== 0;
    const justAfter = cursorU;
    await new Promise((r) => setTimeout(r, 120));
    const drifted = cursorU;
    at(box.left + 200, y0, "pointerdown");
    const stopped = coasting === 0;
    at(box.left + 200, y0, "pointerup");
    deskSend = real;
    return { started, justAfter, drifted, stopped };
  });
  check(flicked.started, "a flick leaves the pointer coasting");
  check(flicked.drifted > flicked.justAfter,
        "and it keeps going after the finger has gone: "
        + flicked.justAfter.toFixed(3) + " -> " + flicked.drifted.toFixed(3));
  check(flicked.stopped, "a finger back on the glass stops it dead");

  // The controller button takes it all back.
  await page.evaluate(() => document.getElementById("desk-pad").click());
  await wait(600);
  const back = await page.evaluate(() => ({
    cursor: cursorOn, driving: cursorDriving(),
    pad: document.getElementById("touch").hidden,
    coasting,
  }));
  check(!back.cursor && !back.driving, "the controller button puts the cursor away");
  check(!back.pad, "and brings the controller back");
  check(!back.coasting, "with nothing left coasting");

  await page.evaluate(() => document.getElementById("desk-take").click());
  await wait(1200);
  check(errors.length === 0, "no script errors throughout: " + errors.slice(0, 2));
} finally {
  await browser.close();
}

console.log();
if (fails.length) { console.log(fails.length + " FAILED"); process.exit(1); }
console.log("all good");
