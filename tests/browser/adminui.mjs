/* The Admin tab: that it exists, that it is only for people who can use it,
   and that the picture controls say what the host said.

   It exists because the admin controls used to live under Account, beside
   somebody's own name and password. Two different questions asked by two
   different people, in one drawer. */
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
  await page.emulate({ name: "phone",
    userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile Safari/604.1",
    viewport: { width: 390, height: 844, isMobile: true, hasTouch: true } });
  await page.goto(process.env.FP_PAGE, { waitUntil: "domcontentloaded" });
  await new Promise((r) => setTimeout(r, 700));

  const guest = await page.evaluate(() => {
    gate.hidden = true; stage.hidden = false;
    account = null;
    paintSession();
    return { tab: !document.getElementById("tab-admin-pick").hidden };
  });
  check(!guest.tab, "somebody with no account is not shown the Admin tab");

  const onlyKick = await page.evaluate(() => {
    account = { name: "a", can: ["kick"], primary: false, fresh: true };
    paintSession();
    return { tab: !document.getElementById("tab-admin-pick").hidden,
             kick: !document.getElementById("session-kick").hidden,
             stream: !document.getElementById("session-stream").hidden,
             grant: !document.getElementById("session-grant").hidden };
  });
  check(onlyKick.tab, "an account given one admin power is shown the tab");
  check(onlyKick.kick, "and the part it was given");
  check(!onlyKick.stream && !onlyKick.grant,
        "and nothing it was not: the panel is the union of what you may do");

  const full = await page.evaluate(() => {
    window.__sent = [];
    send = (m) => window.__sent.push(m);
    account = { name: "seth", can: ["kick", "lock", "slots", "grant", "desk",
                                    "reshare", "stream"],
                primary: true, fresh: true };
    paintSession();
    showTab("admin");
    return { stream: !document.getElementById("session-stream").hidden,
             asked: window.__sent.map((m) => m.t),
             onAdmin: !document.getElementById("tab-admin").hidden,
             account: !document.getElementById("tab-session").hidden };
  });
  check(full.stream, "an account with stream sees the picture controls");
  check(full.asked.includes("stream"),
        "opening the tab asks the host what the picture is doing now, rather "
        + "than trusting what it last saw: " + JSON.stringify(full.asked));
  check(full.onAdmin && !full.account,
        "and the Admin tab is its own panel, not a corner of Account");

  // The host's answer drives every control, bounds included.
  const painted = await page.evaluate(() => {
    paintStream({ width: 1280, height: 720, fps: 60, bitrate_kbps: 4000,
                  jitter_ms: 60, queue_ms: 60, cpb_ms: 100, codec: "auto",
                  sending: "1280x720", playing: "h265",
                  encoder: "vah265enc", hardware: true,
                  sizes: [1080, 720, 540, 480],
                  limits: { height: [480, 1080], fps: [15, 60],
                            bitrate_kbps: [500, 20000], jitter_ms: [0, 300],
                            queue_ms: [10, 500], cpb_ms: [20, 1000] } });
    const g = (id) => document.getElementById(id);
    return { size: g("stream-size").value, fps: g("stream-fps").value,
             sizes: [...g("stream-size").options].map((o) => o.value),
             bitrate: g("stream-bitrate").value,
             bitrateMax: g("stream-bitrate").max,
             jitterMax: g("stream-jitter").max,
             now: g("stream-now").textContent,
             applyOff: g("stream-apply").disabled };
  });
  check(painted.size === "720" && painted.fps === "60",
        "the controls show what the host says is running");
  check(painted.sizes.join(",") === "1080,720,540,480",
        "the sizes offered are the host's, not a list baked into the page");
  check(painted.bitrateMax === "20000" && painted.jitterMax === "300",
        "and so are the bounds, so the page cannot offer a number the host "
        + "would clamp");
  check(/1280x720/.test(painted.now) && /vah265enc/.test(painted.now)
        && /card/.test(painted.now),
        "it says what is actually going out and what is encoding it: "
        + painted.now);
  check(painted.applyOff,
        "and Apply is dead until something changes -- every apply costs the "
        + "room a second of picture");

  const changed = await page.evaluate(() => {
    window.__sent = [];
    const fps = document.getElementById("stream-fps");
    fps.value = "30";
    fps.dispatchEvent(new Event("change", { bubbles: true }));
    const before = document.getElementById("stream-apply").disabled;
    document.getElementById("stream-apply").click();
    return { before, sent: window.__sent };
  });
  check(!changed.before, "changing one wakes Apply up");
  check(changed.sent.length === 1 && changed.sent[0].t === "stream"
        && changed.sent[0].settings.fps === 30,
        "and it sends what was asked for: " + JSON.stringify(changed.sent));

  const back = await page.evaluate(() => {
    document.getElementById("stream-jitter").value = "300";
    document.getElementById("stream-reset").click();
    return document.getElementById("stream-jitter").value;
  });
  check(back === "60", "and there is a way back to what it was: " + back);

  check(errors.length === 0, "no script errors: " + errors.slice(0, 2));
} finally { await browser.close(); }
if (fails.length) { console.log("\n" + fails.length + " FAILED"); process.exit(1); }
console.log("\nall good");
