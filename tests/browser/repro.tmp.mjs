import puppeteer from "puppeteer-core";
import crypto from "crypto";
const { LINK, PIN, USER, PASS, SECRET } = process.env;
function code(off = 0) {
  const bits = SECRET.replace(/=+$/, "").split("").map((ch) =>
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567".indexOf(ch).toString(2).padStart(5, "0")).join("");
  const key = Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
  const step = Math.floor(Date.now() / 30000) + off;
  const c = Buffer.alloc(8);
  c.writeUInt32BE(Math.floor(step / 2 ** 32), 0); c.writeUInt32BE(step % 2 ** 32, 4);
  const d = crypto.createHmac("sha1", key).update(c).digest();
  const o = d[d.length - 1] & 15;
  return String(((d[o] & 0x7f) << 24 | d[o+1] << 16 | d[o+2] << 8 | d[o+3]) % 1000000).padStart(6, "0");
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const b = await puppeteer.launch({ executablePath: "/usr/bin/google-chrome",
  args: ["--no-sandbox", "--disable-gpu", "--ignore-certificate-errors"] });
const p = await b.newPage();
await p.setCacheEnabled(false);
const errs = []; p.on("pageerror", (e) => errs.push(String(e)));
await p.emulate({ name: "phone", userAgent: "iPhone Safari",
  viewport: { width: 390, height: 844, isMobile: true, hasTouch: true } });
await p.goto(LINK, { waitUntil: "domcontentloaded" });
await wait(2500);
await p.evaluate((pin) => { document.getElementById("pin").value = pin;
  document.getElementById("pin-form").dispatchEvent(new Event("submit", { cancelable: true })); }, PIN);
await wait(6000);
// Log in properly, asking to be remembered.
await p.evaluate((u, pw, c) => { showTab("session");
  document.getElementById("login-open").click();
  document.getElementById("login-user").value = u;
  document.getElementById("login-pass").value = pw;
  document.getElementById("login-code").value = c;
  document.getElementById("login-remember").checked = true;
  document.getElementById("login-form").dispatchEvent(new Event("submit", { cancelable: true })); },
  USER, PASS, code());
await wait(3000);
console.log("logged in:", await p.evaluate(() => account && account.name),
            " device stored:", await p.evaluate(() => { try { return !!localStorage.getItem("fp-device"); } catch (_) { return null; } }));

// Reload: now it is a remembered device, so not fresh -- the user's state.
await p.reload({ waitUntil: "domcontentloaded" });
await wait(8000);
const state = await p.evaluate(() => ({
  build: CLIENT_BUILD, name: account && account.name,
  fresh: account && account.fresh, bar: !document.getElementById("desk-bar").hidden,
  hasForm: !!document.getElementById("login-again"),
}));
console.log("after reload:", JSON.stringify(state));

await p.evaluate(() => { deskOpen(true); });
await wait(200);
await p.tap("#desk-cursor");
await wait(2500);
const seen = await p.evaluate(() => {
  const look = (n) => { const e = document.getElementById(n);
    if (!e) return "MISSING";
    const cs = getComputedStyle(e);
    const r = e.getBoundingClientRect();
    return { hidden: e.hidden, display: cs.display, visible: r.width > 0 && r.height > 0,
             top: Math.round(r.top), h: Math.round(r.height) }; };
  return { waiting: !!waitingOnCode,
           tabSession: !document.getElementById("tab-session").hidden,
           loginIn: look("login-in"), again: look("login-again"),
           field: look("login-again-code"), note: (document.getElementById("login-again-note")||{}).textContent };
});
console.log("after tapping cursor:", JSON.stringify(seen, null, 1));
console.log("errors:", errs.slice(0, 3));
await b.close();
