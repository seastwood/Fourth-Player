/* The keyboard-and-mouse desk, on a laptop.
 *
 * Three things reported from a real desktop, all of them about a pointer that
 * is locked to the picture:
 *
 *   "the extra utility buttons lay over the video stream"
 *   "zooming with the trackpad zooms into one spot and doesn't follow the
 *    cursor"
 *   "scrolling behaves unpredictably, like scrolling and zooming happen at the
 *    same time"
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const css = readFileSync(new URL("../../web/style.css", import.meta.url), "utf8");

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

// -- one gesture, one meaning ---------------------------------------------
//
// The capture-phase listener sends the notch to the host; the page's own wheel
// listener zooms the picture. preventDefault does not stop the second from
// running, so a single scroll did both at once.
// From the listener to the next "contextmenu" AFTER it, not the first one in
// the file. It used to be the first, and a contextmenu listener added
// elsewhere -- the corner button's right-click, further up -- inverted the
// slice and emptied it, so three checks about the wheel failed with nothing
// about the wheel having changed.
const wheelAt = app.indexOf(
  'video.addEventListener("wheel", (event) => {\n    if (!deskHeld');
const guard = app.slice(wheelAt, app.indexOf("contextmenu", wheelAt));
check(/if \(event\.ctrlKey\) return;/.test(guard),
      "a trackpad pinch is let through to the zoom, not sent to the host");
check(/event\.stopPropagation\(\);/.test(guard),
      "and an ordinary scroll is claimed outright, so it cannot also zoom");
check(guard.indexOf("if (event.ctrlKey) return;") < guard.indexOf("deskWheeled"),
      "the pinch leaves before anything is sent, rather than after");

// -- zooming towards the console's pointer ---------------------------------
//
// Under a lock, clientX and clientY stop moving. cursorClientPoint is the
// inverse of cursorFollow, so these are exact rather than approximate.
const from = app.indexOf("function cursorClientPoint");
const body = app.slice(from, app.indexOf("\n}", from) + 2);
/* The browser's own rect, which is the part this got wrong.
 *
 * The video carries `transform: translate(panX, panY) scale(zoom)` with the
 * origin at its centre, and getBoundingClientRect reports the box *after*
 * that. So the pan is already inside the rect, and the width is already
 * scaled. The first version of this helper handed the function an
 * untransformed box and the function added the pan itself -- the two mistakes
 * cancelled, the test passed, and the mark was out by exactly the pan on a
 * real screen. Which is why there is a zoomed *and* panned case below. */
function rectOf({ left, top, w, h, panX, panY, zoom }) {
  const cx = left + w / 2 + panX, cy = top + h / 2 + panY;
  return { left: cx - (w * zoom) / 2, top: cy - (h * zoom) / 2,
           width: w * zoom, height: h * zoom };
}

function pointAt({ u, v, zoom, panX, panY, w = 800, h = 450, left = 100, top = 50 }) {
  const scope = {
    video: { getBoundingClientRect: () => rectOf({ left, top, w, h, panX, panY, zoom }) },
    pictureBox: () => ({ width: w, height: h }),
    panX, panY, zoom, cursorU: u, cursorV: v,
  };
  return new Function(...Object.keys(scope), body + "; return cursorClientPoint();")
    (...Object.values(scope));
}

/* Where the mark belongs, worked out independently: the picture is centred in
   the element and scales with it, so the cursor sits that fraction from the
   centre, scaled, and then the whole thing is panned. */
const expect = ({ u, v, zoom, panX, panY, w = 800, h = 450, left = 100, top = 50 }) => ({
  x: left + w / 2 + panX + (u - 0.5) * w * zoom,
  y: top + h / 2 + panY + (v - 0.5) * h * zoom,
});

const near = (a, b) => Math.abs(a - b) < 1e-9;
function agrees(name, c) {
  const got = pointAt(c), want = expect(c);
  check(near(got.x, want.x) && near(got.y, want.y),
        `${name}: ${got.x.toFixed(1)}, ${got.y.toFixed(1)}`
        + ` (wanted ${want.x.toFixed(1)}, ${want.y.toFixed(1)})`);
}

agrees("whole picture, no pan", { u: 0.25, v: 0.75, zoom: 1, panX: 0, panY: 0 });
agrees("zoomed, still centred", { u: 0.25, v: 0.75, zoom: 2.5, panX: 0, panY: 0 });
// The one that was wrong on a real screen, and could not be wrong in the old
// test because the old test cancelled the error out.
agrees("zoomed AND panned", { u: 0.8, v: 0.2, zoom: 3, panX: -220, panY: 130 });
agrees("panned only", { u: 0.5, v: 0.5, zoom: 1, panX: 60, panY: -40 });

// A cursor the picture is centred on lands in the middle of the element,
// which is what cursorFollow arranges.
let z = 2, u = 0.8, v = 0.3;
let at = pointAt({ u, v, zoom: z, panX: -(u - 0.5) * 800 * z, panY: -(v - 0.5) * 450 * z });
check(near(at.x, 500) && near(at.y, 275),
      `a followed cursor is the middle of the element: ${at.x}, ${at.y}`);

// And it still has to move when the cursor does.
const a = pointAt({ u: 0.2, v: 0.5, zoom: 1, panX: 0, panY: 0 });
const b = pointAt({ u: 0.7, v: 0.5, zoom: 1, panX: 0, panY: 0 });
check(b.x > a.x, `moving the cursor right moves the zoom target right: ${a.x} -> ${b.x}`);

check(/deskCaptured\(\) \? cursorClientPoint\(\)/.test(app),
      "and the wheel zoom uses it while the pointer is locked");
check(/cursorU = Math\.max\(0, Math\.min\(1, cursorU \+ dx/.test(app),
      "which is kept up to date from the motion already being sent");
check(/if \(deskCaptured\(\)\) \{ cursorU = cursorV = 0\.5; \}/.test(app),
      "and started from the middle on each capture, bounding any drift");

// -- the buttons over the picture -----------------------------------------
check(/\.stage\.locked \.desk-dock \{[^}]*opacity: 0/.test(css),
      "the utility buttons fade while the pointer is captured");
check(/\.stage\.locked \.desk-dock \{[^}]*pointer-events: none/.test(css),
      "and take no clicks while faded, so a ghost cannot be pressed");
check(/stage\.classList\.toggle\("locked", deskCaptured\(\)\)/.test(app),
      "driven by the lock itself, so Escape brings them straight back");

console.log("");
if (fails) { console.log(`deskdesktop: ${fails} FAILED`); process.exit(1); }
console.log("deskdesktop: all ok");
