/* Being asked for an authenticator code, and being able to answer.
 *
 * The whole shape of the fault this exists for: logged in by a remembered
 * device, tap "use the mouse", the host asks for a code -- and there has to
 * be somewhere to type it that a person can actually see. Twice this looked
 * correct in every way a test that reads the DOM would check, because the
 * form was drawn, was not display:none, and had no size: once because the
 * code field only existed inside the logged-out login form, and once because
 * the panel holding it was the pads panel and the wrong one was opened.
 *
 * So this measures *height*, not visibility flags. A form with no height is
 * a form that is not there.
 *
 * Not in run.sh: it needs an open session and an account with `desk`.
 *
 *   fourth-player admin add uiprobe --can desk
 *   fourth-player reshare
 *   LINK=... PIN=... USER=... PASS=... SECRET=... node tests/browser/deskcode.mjs
 */
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
  const chain = [];
  let node = document.getElementById("login-again-code");
  while (node && node !== document.documentElement) {
    const cs = getComputedStyle(node);
    const r = node.getBoundingClientRect();
    chain.push({ id: node.id || null, cls: (node.className && node.className.baseVal !== undefined
                   ? node.className.baseVal : node.className) || null,
                 hidden: node.hidden === true, display: cs.display,
                 vis: cs.visibility, h: Math.round(r.height), w: Math.round(r.width),
                 overflow: cs.overflow, maxH: cs.maxHeight });
    node = node.parentElement;
  }
  return { chain, waiting: !!waitingOnCode,
           tabSession: !document.getElementById("tab-session").hidden,
           loginIn: look("login-in"), again: look("login-again"),
           field: look("login-again-code"), note: (document.getElementById("login-again-note")||{}).textContent };
});
console.log("after tapping cursor: field height",
            seen.chain[0].h, " note:", seen.note);

// Wait for a step this account has not spent, then answer.
const spent = code();
while (code() === spent) await wait(2000);
await p.evaluate((c) => {
  document.getElementById("login-again-code").value = c;
  document.getElementById("login-again")
    .dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
}, code());
await wait(3500);
const done = await p.evaluate(() => ({
  fresh: account && account.fresh, held: deskHeld, cursor: cursorOn,
  waiting: !!waitingOnCode,
  panelShut: document.getElementById("pads").hidden,
  driving: cursorDriving(),
}));
console.log("after entering the code:", JSON.stringify(done));
console.log("errors:", errs.slice(0, 3));
await b.close();
