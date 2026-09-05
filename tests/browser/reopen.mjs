/* The actual thing: log in with "remember this device" ticked, close the page,
   open it again. A real browser against the real host on the box, so
   localStorage, the startup resume and the host's device check are all the
   shipping ones. */
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
  const counter = Buffer.alloc(8);
  counter.writeUInt32BE(Math.floor(step / 2 ** 32), 0);
  counter.writeUInt32BE(step % 2 ** 32, 4);
  const d = crypto.createHmac("sha1", key).update(counter).digest();
  const o = d[d.length - 1] & 15;
  const n = ((d[o] & 0x7f) << 24 | d[o + 1] << 16 | d[o + 2] << 8 | d[o + 3]) % 1000000;
  return String(n).padStart(6, "0");
}

const b = await puppeteer.launch({
  executablePath: "/usr/bin/google-chrome",
  args: ["--no-sandbox", "--disable-gpu", "--ignore-certificate-errors"],
});

async function openApp() {
  const p = await b.newPage();          // same browser: same localStorage
  await p.setViewport({ width: 390, height: 844, isMobile: true });
  await p.goto(LINK, { waitUntil: "domcontentloaded" });
  await new Promise((r) => setTimeout(r, 2500));
  return p;
}

console.log("first visit: join, then log in and ask to be remembered");
let p = await openApp();
await p.evaluate((pin) => {
  document.getElementById("pin").value = pin;
  document.getElementById("pin-form").dispatchEvent(new Event("submit", { cancelable: true }));
}, PIN);
await new Promise((r) => setTimeout(r, 5000));
check(await p.evaluate(() => gate.hidden === true), "joined the session");

await p.evaluate((otp) => {
  showTab("session");
  document.getElementById("login-open").click();
  document.getElementById("login-user").value = "probe";
  document.getElementById("login-pass").value = "throwaway-probe-pw";
  document.getElementById("login-code").value = otp;
  document.getElementById("login-remember").checked = true;
  document.getElementById("login-form").dispatchEvent(new Event("submit", { cancelable: true }));
}, code());
await new Promise((r) => setTimeout(r, 2500));

const after = await p.evaluate(() => ({
  name: account && account.name, can: account && account.can,
  stored: !!localStorage.getItem("fp-device"),
}));
check(after.name === "probe", "logged in: " + JSON.stringify(after));
check(after.stored, "and the device token is kept in this browser");

console.log("\nnow close the app and open it again");
await p.close();
p = await openApp();
await new Promise((r) => setTimeout(r, 4000));
const back = await p.evaluate(() => ({
  gate: gate.hidden, name: account && account.name,
  fresh: account && account.fresh, can: account && account.can,
  says: (document.getElementById("login-as") || {}).textContent,
}));
check(back.gate === true, "it rejoined without asking for the PIN");
check(back.name === "probe",
      "and logged straight back into the account: " + JSON.stringify(back.name));
check(back.fresh === false,
      "saying no code was presented, so kick and lock still ask for one");
check((back.can || []).includes("steam"), "with its capabilities: " + back.can);
check(/probe/.test(back.says || ""), "and the panel says so: " + back.says);

console.log("\nand logging out stops it coming back");
await p.evaluate(() => document.getElementById("login-out").click());
await new Promise((r) => setTimeout(r, 1500));
check(await p.evaluate(() => !localStorage.getItem("fp-device")),
      "logging out forgets the device");
await p.close();
p = await openApp();
await new Promise((r) => setTimeout(r, 4000));
check(await p.evaluate(() => account === null),
      "so reopening after a log out is nobody again");

await b.close();
console.log("");
if (fails.length) { console.log(fails.length + " FAILED"); process.exit(1); }
console.log("all good");
