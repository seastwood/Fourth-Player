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
const guard = app.slice(app.indexOf('video.addEventListener("wheel", (event) => {\n    if (!deskHeld'),
                        app.indexOf("contextmenu"));
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
function pointAt({ u, v, zoom, panX, panY, w = 800, h = 450, left = 100, top = 50 }) {
  const scope = {
    video: { getBoundingClientRect: () => ({ left, top, width: w, height: h }) },
    pictureBox: () => ({ width: w, height: h }),
    panX, panY, zoom, cursorU: u, cursorV: v,
  };
  return new Function(...Object.keys(scope), body + "; return cursorClientPoint();")
    (...Object.values(scope));
}

// cursorFollow sets panX = -(u - 0.5) * width * zoom, which puts the cursor in
// the middle. The point must agree.
let z = 2, u = 0.8, v = 0.3;
let at = pointAt({ u, v, zoom: z, panX: -(u - 0.5) * 800 * z, panY: -(v - 0.5) * 450 * z });
check(Math.abs(at.x - (100 + 400)) < 1e-9 && Math.abs(at.y - (50 + 225)) < 1e-9,
      `a followed cursor is the middle of the element: ${at.x}, ${at.y}`);

// Unpanned, the cursor sits where its fraction says.
at = pointAt({ u: 0.5, v: 0.5, zoom: 1, panX: 0, panY: 0 });
check(at.x === 500 && at.y === 275, `centre maps to the centre: ${at.x}, ${at.y}`);
at = pointAt({ u: 1, v: 1, zoom: 1, panX: 0, panY: 0 });
check(at.x === 900 && at.y === 500, `the far corner maps to the far corner: ${at.x}, ${at.y}`);

// It has to move when the cursor does, which is the whole complaint.
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
