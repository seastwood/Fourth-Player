/* An account that has been given the Steam game, holding a button down and
   letting it go, over and over, while the game runs. The point is not what the
   page thinks -- it is whether the presses arrive at the virtual pad while
   Steam's own window flickers in and out of the foreground. */
import puppeteer from "puppeteer-core";
import crypto from "crypto";

const LINK = process.env.LINK, PIN = process.env.PIN, SECRET = process.env.SECRET;
function code(offset = 0) {
  const bits = SECRET.replace(/=+$/, "").split("").map((ch) =>
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567".indexOf(ch).toString(2).padStart(5, "0")).join("");
  const key = Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
  const step = Math.floor(Date.now() / 30000) + offset;
  const c = Buffer.alloc(8);
  c.writeUInt32BE(Math.floor(step / 2 ** 32), 0); c.writeUInt32BE(step % 2 ** 32, 4);
  const d = crypto.createHmac("sha1", key).update(c).digest();
  const o = d[d.length - 1] & 15;
  return String(((d[o] & 0x7f) << 24 | d[o+1] << 16 | d[o+2] << 8 | d[o+3]) % 1000000).padStart(6, "0");
}

const b = await puppeteer.launch({ executablePath: "/usr/bin/google-chrome",
  args: ["--no-sandbox", "--disable-gpu", "--ignore-certificate-errors"] });
const p = await b.newPage();
await p.setViewport({ width: 390, height: 844, isMobile: true });
await p.goto(LINK, { waitUntil: "domcontentloaded" });
await new Promise((r) => setTimeout(r, 2500));
await p.evaluate((pin) => {
  document.getElementById("pin").value = pin;
  document.getElementById("pin-form").dispatchEvent(new Event("submit", { cancelable: true }));
}, PIN);
await new Promise((r) => setTimeout(r, 6000));
console.log("joined, slot", await p.evaluate(() => mySlot));

await p.evaluate((otp) => {
  showTab("session"); document.getElementById("login-open").click();
  document.getElementById("login-user").value = "probe";
  document.getElementById("login-pass").value = "throwaway-probe-pw";
  document.getElementById("login-code").value = otp;
  document.getElementById("login-form").dispatchEvent(new Event("submit", { cancelable: true }));
}, code());
await new Promise((r) => setTimeout(r, 2500));
console.log("logged in as", await p.evaluate(() => account && account.name));

// The keyboard as the controller: a real path through the page's own pad loop.
await p.evaluate(() => { keyboardOn = true; showTab("controls"); });

// Press and release, twice a second, for a minute and a half.
const started = Date.now();
let sent = 0;
while (Date.now() - started < 95000) {
  await p.evaluate(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyZ", bubbles: true }));
  });
  await new Promise((r) => setTimeout(r, 220));
  await p.evaluate(() => {
    window.dispatchEvent(new KeyboardEvent("keyup", { code: "KeyZ", bubbles: true }));
  });
  await new Promise((r) => setTimeout(r, 280));
  sent += 1;
  if (sent === 60) {
    console.log("  --- a client with no account joins now ---");
    const b2 = await puppeteer.launch({ executablePath: "/usr/bin/google-chrome",
      args: ["--no-sandbox", "--disable-gpu", "--ignore-certificate-errors"] });
    const p2 = await b2.newPage();
    await p2.goto(process.env.LINK, { waitUntil: "domcontentloaded" });
    await new Promise((r) => setTimeout(r, 2500));
    await p2.evaluate((pin) => {
      document.getElementById("pin").value = pin;
      document.getElementById("pin-form").dispatchEvent(new Event("submit", { cancelable: true }));
    }, process.env.PIN);
    globalThis.__other = b2;
  }
  if (sent % 20 === 0) {
    const s = await p.evaluate(() => ({
      held: document.documentElement.classList.contains("held"),
      account: account && account.name,
    }));
    console.log("  sent %d presses  page-held=%s account=%s",
                sent, s.held, s.account);
  }
}
console.log("sent", sent, "presses in total");
await p.evaluate(() => send({ t: "endgame" }));
await new Promise((r) => setTimeout(r, 6000));
await b.close();
