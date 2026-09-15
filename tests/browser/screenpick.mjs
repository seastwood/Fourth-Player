/* Choosing which of the host's screens is being sent.
 *
 * Asked for as: it would be handy to switch which monitor I am viewing if
 * there are multiples -- a chip button that only appears if there is more
 * than one.
 *
 * Two controls for one setting: a dropdown in the picture panel for choosing
 * deliberately, and a chip over the video for switching mid-game. Both are
 * hidden with a single screen, because a choice with one option is not a
 * choice and a button that cycles between one thing looks broken.
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

const from = app.indexOf("function paintScreens");
const until = app.indexOf("function paintStream(state)");
check(from > 0 && until > from, "paintScreens was found");

function harness() {
  const nodes = {};
  const make = (id) => (nodes[id] = {
    id, hidden: false, innerHTML: "", value: "", textContent: "", title: "",
    checked: false, dataset: {}, children: [],
    appendChild(c) { this.children.push(c); },
  });
  for (const id of ["stream-screen", "stream-screen-row", "screen-chip",
                    "stream-virtual", "stream-virtual-row"]) make(id);
  const document = {
    createElement: () => ({ value: "", textContent: "" }),
  };
  const fn = new Function("el", "document",
    app.slice(from, until) + "; return paintScreens;")(
      (id) => nodes[id] || null, document);
  return { nodes, paint: fn };
}

const two = [
  { index: 0, name: "\\\\.\\DISPLAY1", width: 1920, height: 1080, primary: true },
  { index: 1, name: "\\\\.\\DISPLAY11", width: 2560, height: 1440, primary: false },
];

console.log("one screen: neither control is offered");
let h = harness();
h.paint({ screens: [two[0]], monitor: -1 });
check(h.nodes["stream-screen-row"].hidden === true, "the dropdown row is hidden");
check(h.nodes["screen-chip"].hidden === true, "and so is the chip");

console.log("\nno screens at all -- a host that cannot choose");
h = harness();
h.paint({ monitor: -1 });
check(h.nodes["stream-screen-row"].hidden === true,
      "nothing is offered rather than an empty dropdown");
check(h.nodes["screen-chip"].hidden === true, "and no chip");

console.log("\ntwo screens: both appear");
h = harness();
h.paint({ screens: two, monitor: -1 });
check(h.nodes["stream-screen-row"].hidden === false, "the dropdown row shows");
check(h.nodes["screen-chip"].hidden === false, "and the chip shows");
check(h.nodes["screen-chip"].textContent === "Main screen",
      `with -1 it says the main one: "${h.nodes["screen-chip"].textContent}"`);

console.log("\nthe chip names the screen actually being sent");
h = harness();
h.paint({ screens: two, monitor: 1 });
check(h.nodes["screen-chip"].textContent === "Screen 2",
      `screen 1 is "Screen 2" to a human: "${h.nodes["screen-chip"].textContent}"`);
check(h.nodes["stream-screen"].value === "1", "and the dropdown agrees");

console.log("\nthe options say the size, which is what tells screens apart");
h = harness();
h.paint({ screens: two, monitor: -1 });
const labels = h.nodes["stream-screen"].children.map((c) => c.textContent);
check(labels.some((t) => t.includes("1920x1080")), "the 1080p one is named");
check(labels.some((t) => t.includes("2560x1440")), "and the 1440p one");
check(!labels.some((t) => t.includes("DISPLAY11")),
      "and not by device name, which means nothing to anybody");
check(labels.some((t) => t.includes("main")), "the main one says so");

console.log("\non the virtual screen the chip stands down");
// There is nothing to cycle between, and a switch that fought the virtual
// display toggle would be two controls disagreeing about one picture.
h = harness();
h.paint({ screens: two, monitor: -1, on_virtual_display: true });
check(h.nodes["screen-chip"].hidden === true, "the chip is hidden");
check(h.nodes["stream-screen-row"].hidden === false,
      "but the deliberate choice stays reachable, so somebody can set what to "
      + "come back to");

console.log("\nthe virtual switch is only offered where it could work");
h = harness();
h.paint({ screens: two, monitor: -1, can_virtual_display: false });
check(h.nodes["stream-virtual-row"].hidden === true,
      "hidden where the host cannot make one");
h = harness();
h.paint({ screens: two, monitor: -1, can_virtual_display: true,
          virtual_display: true });
check(h.nodes["stream-virtual-row"].hidden === false, "shown where it can");
check(h.nodes["stream-virtual"].checked === true, "and reflects the host");

console.log("\nthe chip cycles, and wraps");
const click = app.slice(app.indexOf('if (el("screen-chip"))'),
                        app.indexOf('if (el("pads-buzz-strength"))'));
check(/\(at \+ 1 \+ screens\.length\) % screens\.length/.test(click),
      "the next screen wraps round the end");
check(/screens\.length < 2/.test(click),
      "and does nothing with fewer than two");
check(/may\("stream"\)/.test(click),
      "and nothing without the capability, so the button is not a way round "
      + "the permission");
check(/\.\.\.streamFields\(\)/.test(click),
      "it sends the whole settings object, not just the screen -- a partial "
      + "one reads as 'set everything else back to what this page thinks'");

console.log("\nthe markup has both, and starts hidden");
check(/id="stream-screen-row"[^>]*hidden/.test(html),
      "the dropdown row starts hidden, so a single-screen host never flashes "
      + "a control it does not want");
check(/id="screen-chip"[^>]*hidden/.test(html), "and so does the chip");
check(/id="screen-chip"[\s\S]{0,120}<\/button>/.test(html),
      "the chip is a button, because it does something");

process.exit(fails ? 1 : 0);
