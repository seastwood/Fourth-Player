/* Two real browsers against the live host: an admin in a Steam game, and then
   somebody with no account joining. Real WebRTC, so neither is swept for
   having no media -- which is what a socket-only client gets wrong. */
import puppeteer from "puppeteer-core";
import crypto from "crypto";

const LINK = process.env.LINK, PIN = process.env.PIN, SECRET = process.env.SECRET;
const fails = [];
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails.push(m); };

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

async function browser() {
  return puppeteer.launch({ executablePath: "/usr/bin/google-chrome",
    args: ["--no-sandbox", "--disable-gpu", "--ignore-certificate-errors",
           "--autoplay-policy=no-user-gesture-required"] });
}
async function join(b, name) {
  const p = await b.newPage();
  await p.setViewport({ width: 390, height: 844, isMobile: true });
  await p.evaluateOnNewDocument(() => {
    window.__seen = [];
    const push = (m) => window.__seen.push(m);
    const orig = WebSocket.prototype.addEventListener;
    WebSocket.prototype.addEventListener = function (kind, fn, ...rest) {
      if (kind === "message") {
        return orig.call(this, kind, (e) => {
          try { push(JSON.parse(e.data)); } catch (_) {}
          return fn(e);
        }, ...rest);
      }
      return orig.call(this, kind, fn, ...rest);
    };
  });
  await p.goto(LINK, { waitUntil: "domcontentloaded" });
  await new Promise((r) => setTimeout(r, 2500));
  await p.evaluate((pin, who) => {
    document.getElementById("pin").value = pin;
    document.getElementById("who").value = who;
    document.getElementById("pin-form").dispatchEvent(new Event("submit", { cancelable: true }));
  }, PIN, name);
  await new Promise((r) => setTimeout(r, 6000));
  return p;
}
const state = (p) => p.evaluate(() => ({
  slot: mySlot, gate: gate.hidden,
  account: account && account.name,
  held: document.documentElement.classList.contains("held"),
  holds: window.__seen.filter((m) => m.t === "hold").slice(-3),
  errors: window.__seen.filter((m) => m.t === "error").slice(-2),
}));

const bAdmin = await browser();
console.log("admin joins and logs in");
const admin = await join(bAdmin, "Admin");
check((await state(admin)).gate === true, "admin joined");
await admin.evaluate((otp) => {
  showTab("session"); document.getElementById("login-open").click();
  document.getElementById("login-user").value = "probe";
  document.getElementById("login-pass").value = "throwaway-probe-pw";
  document.getElementById("login-code").value = otp;
  document.getElementById("login-form").dispatchEvent(new Event("submit", { cancelable: true }));
}, code());
await new Promise((r) => setTimeout(r, 2500));
check((await state(admin)).account === "probe", "admin logged in");

console.log("\nadmin starts Broforce");
await admin.evaluate(() => { send({ t: "games" }); });
await new Promise((r) => setTimeout(r, 1500));
const started = await admin.evaluate(() => {
  const row = shelfRows.find((g) => g.label === "Broforce");
  const id = row ? row.id : null;
  if (id) send({ t: "launch", game: id });
  return id;
});
check(!!started, "found Broforce in the admin's list: " + started);
await new Promise((r) => setTimeout(r, 40000));
let s = await state(admin);
console.log("   admin after launch:", JSON.stringify(s));
check(s.held === false, "the admin's controller is live in the Steam game");

console.log("\nsomebody with no account joins");
const bOther = await browser();
const other = await join(bOther, "Other");
const os = await state(other);
console.log("   other:", JSON.stringify(os));
check(os.gate === true, "they got in");
check(os.held === true, "and their controller is held");

await new Promise((r) => setTimeout(r, 8000));
s = await state(admin);
console.log("   admin now:", JSON.stringify(s));
check(s.gate === true, "the admin is still in the session");
check(s.account === "probe", "still logged in");
check(s.held === false, "and their controller is still live");

console.log("\nthe other client leaves");
await bOther.close();
await new Promise((r) => setTimeout(r, 8000));
s = await state(admin);
console.log("   admin after they left:", JSON.stringify(s));
check(s.held === false, "admin still live");

await admin.evaluate(() => send({ t: "endgame" }));
await new Promise((r) => setTimeout(r, 8000));
await bAdmin.close();
console.log("");
if (fails.length) { console.log(fails.length + " FAILED"); process.exit(1); }
console.log("all good");
