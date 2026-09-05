import puppeteer from "puppeteer-core";
const b = await puppeteer.launch({ executablePath: "/usr/bin/google-chrome",
  args: ["--no-sandbox", "--disable-gpu"] });
try {
  const p = await b.newPage();
  const errs = [];
  p.on("pageerror", (e) => errs.push("pageerror: " + String(e).slice(0, 200)));
  p.on("console", (m) => { if (m.type() === "error") errs.push("console: " + m.text().slice(0, 200)); });
  await p.goto("http://127.0.0.1:8731/index.html", { waitUntil: "domcontentloaded" });
  await new Promise((r) => setTimeout(r, 1500));
  const state = await p.evaluate(() => ({
    build: typeof CLIENT_BUILD !== "undefined" ? CLIENT_BUILD : "MISSING",
    padLoop: typeof startPadLoop === "function",
    sendFn: typeof send === "function",
    feedPath: typeof videoCodecs === "function",
    keyboard: typeof buttonForKey === "function",
  }));
  console.log("state:", JSON.stringify(state));
  console.log("errors:", errs.length ? errs : "none");
} finally { await b.close(); }
