/* The gestures a finger has instead of buttons.
 *
 * A trackpad has no buttons, so it borrows gestures for them: two fingers for
 * the right button, and tap-then-tap-and-stay for holding the left one down
 * while dragging. Both are decided from the same stream of pointer events as
 * a plain tap and a pinch, which is why they are worth a test -- the first
 * version of them read the previous gesture's leftovers and turned two
 * fingers into a double tap and the tap after that into a right click.
 *
 * The gaps between the gestures below are not padding. Two taps inside three
 * hundred milliseconds are one gesture by definition, so a test that fires
 * them back to back is testing something nobody can do with a hand.
 */
import puppeteer from "puppeteer-core";
const b = await puppeteer.launch({ executablePath: "/usr/bin/google-chrome",
  args: ["--no-sandbox", "--disable-gpu"] });
const p = await b.newPage();
const errs = []; p.on("pageerror", (e) => errs.push(String(e)));
await p.emulate({ name: "phone", userAgent: "iPhone Safari",
  viewport: { width: 390, height: 844, isMobile: true, hasTouch: true } });
await p.goto(process.env.FP_PAGE, { waitUntil: "domcontentloaded" });
await new Promise((r) => setTimeout(r, 700));
const out = await p.evaluate(async () => {
  gate.hidden = true; stage.hidden = false;
  Object.defineProperty(video, "videoWidth", { value: 1280, configurable: true });
  Object.defineProperty(video, "videoHeight", { value: 720, configurable: true });
  account = { name: "s", can: [], primary: true, fresh: true };
  deskHeld = true; cursorOn = true;
  applyLayoutChoice("off"); deskPaintKeys();
  const sent = [];
  deskSend = (list) => sent.push(...list);
  const box = video.getBoundingClientRect();
  const cx = box.left + box.width / 2, cy = box.top + box.height / 2;
  let clock = 1000;
  const at = (type, x, y, id) => { clock += 1; video.dispatchEvent(
    new PointerEvent(type, { pointerId: id, pointerType: "touch", clientX: x,
      clientY: y, bubbles: true, cancelable: true })); };
  const mark = (name) => sent.push({ mark: name });

  await new Promise((r) => setTimeout(r, 400));
  mark("single tap");
  at("pointerdown", cx, cy, 1); at("pointerup", cx, cy, 1);

  await new Promise((r) => setTimeout(r, 400));
  mark("two-finger tap");
  at("pointerdown", cx - 20, cy, 2); at("pointerdown", cx + 20, cy, 3);
  at("pointerup", cx - 20, cy, 2); at("pointerup", cx + 20, cy, 3);

  await new Promise((r) => setTimeout(r, 400));
  mark("double tap and drag");
  at("pointerdown", cx, cy, 4); at("pointerup", cx, cy, 4);   // first tap
  at("pointerdown", cx, cy, 5);                                // second, held
  for (let i = 1; i <= 3; i++) at("pointermove", cx + i * 12, cy, 5);
  at("pointerup", cx + 36, cy, 5);
  return sent;
});
const fails = [];
const check = (cond, msg) => {
  console.log((cond ? "  ok   " : "  FAIL ") + msg);
  if (!cond) fails.push(msg);
};

const parts = {};
let now = null;
for (const m of out) {
  if (m.mark) { now = m.mark; parts[now] = []; continue; }
  if (m.t === "b") parts[now].push(`b${m.b}${m.d ? "-" : "+"}`);
  else if (m.t === "p") parts[now].push("move");
}
const said = (name) => (parts[name] || []).join(" ");

check(said("single tap") === "b0- b0+",
      "one finger, on and straight off, is a left click: " + said("single tap"));
check(said("two-finger tap") === "b2- b2+",
      "two fingers, on and straight off, are a right click: "
      + said("two-finger tap"));
const drag = said("double tap and drag");
check(/^b0- b0\+ b0- (move )+b0\+$/.test(drag),
      "tap, then tap and stay, holds the left button down through the drag "
      + "and lets go at the end: " + drag);
check(errs.length === 0, "no script errors: " + errs.slice(0, 2));

await b.close();
console.log();
if (fails.length) { console.log(fails.length + " FAILED"); process.exit(1); }
console.log("all good");
