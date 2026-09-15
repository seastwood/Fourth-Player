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
    checked: false, disabled: false, dataset: {}, children: [],
    classList: { toggle() {} },
    appendChild(c) { this.children.push(c); },
  });
  for (const id of ["stream-screen", "stream-screen-row", "screen-chip",
                    "stream-virtual", "stream-virtual-row",
                    "stream-virtual-note"]) make(id);
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
h.paint({ screens: [two[0]], monitor: "" });
check(h.nodes["stream-screen-row"].hidden === true, "the dropdown row is hidden");
check(h.nodes["screen-chip"].hidden === true, "and so is the chip");

console.log("\nno screens at all -- a host that cannot choose");
h = harness();
h.paint({ monitor: "" });
check(h.nodes["stream-screen-row"].hidden === true,
      "nothing is offered rather than an empty dropdown");
check(h.nodes["screen-chip"].hidden === true, "and no chip");

console.log("\ntwo screens: both appear");
h = harness();
h.paint({ screens: two, monitor: "" });
check(h.nodes["stream-screen-row"].hidden === false, "the dropdown row shows");
check(h.nodes["screen-chip"].hidden === false, "and the chip shows");
check(h.nodes["screen-chip"].textContent === "Main screen",
      `with -1 it says the main one: "${h.nodes["screen-chip"].textContent}"`);

console.log("\nthe chip names the screen actually being sent");
h = harness();
h.paint({ screens: two, monitor: "\\\\.\\DISPLAY11" });
check(h.nodes["screen-chip"].textContent === "Screen 2",
      `screen 1 is "Screen 2" to a human: "${h.nodes["screen-chip"].textContent}"`);
check(h.nodes["stream-screen"].value === "\\\\.\\DISPLAY11",
      `and the dropdown agrees: ${h.nodes["stream-screen"].value}`);

console.log("\nthe options say the size, which is what tells screens apart");
h = harness();
h.paint({ screens: two, monitor: "" });
const labels = h.nodes["stream-screen"].children.map((c) => c.textContent);
check(labels.some((t) => t.includes("1920x1080")), "the 1080p one is named");
check(labels.some((t) => t.includes("2560x1440")), "and the 1440p one");
check(!labels.some((t) => t.includes("DISPLAY11")),
      "and not by device name, which means nothing to anybody");
check(labels.some((t) => t.includes("main")), "the main one says so");

console.log("\nthe virtual screen is one of the screens, not a mode apart");
// It was left out of the list at first, on the reasoning that it is a mode
// rather than a screen. That is backwards in the only setup that has one: a
// machine with a single monitor and a virtual display is the commonest case
// with two screens to move between, and hiding the switch there left no way
// back to the real desktop.
const withVirtual = [
  two[0],
  { index: 1, name: "\\\\.\\DISPLAY12", width: 2560, height: 1440,
    primary: false, virtual: true },
];
h = harness();
h.paint({ screens: withVirtual, monitor: "\\\\.\\DISPLAY12",
          on_virtual_display: true });
check(h.nodes["screen-chip"].hidden === false,
      "the chip shows while the virtual display is on");
check(h.nodes["screen-chip"].textContent === "Virtual",
      `and says which it is: "${h.nodes["screen-chip"].textContent}"`);
const names = h.nodes["stream-screen"].children.map((c) => c.textContent);
check(names.some((t) => t.startsWith("Virtual display")),
      `the list names it plainly: ${JSON.stringify(names)}`);
check(names.some((t) => t.includes("Screen 1")),
      "and the real monitor is still there to switch back to");

console.log("\nthe virtual switch is always findable, and says why when it is not usable");
// It used to be hidden where the host could not make one. That meant "this
// machine cannot do it" and "the control has gone" looked identical -- which
// is what happened when the driver broke: the switch simply was not there,
// with nothing anywhere to say why, and the question came back as "where is
// the virtual display toggle?".
h = harness();
h.paint({ screens: two, monitor: "", can_virtual_display: false });
check(h.nodes["stream-virtual-row"].hidden === false,
      "the row is still there when the host cannot make one");
check(h.nodes["stream-virtual"].disabled === true, "but the switch is disabled");
check(/needs a restart|no virtual display driver/
        .test(h.nodes["stream-virtual-note"].textContent),
      `and it says why: "${h.nodes["stream-virtual-note"].textContent}"`);

h = harness();
h.paint({ screens: two, monitor: "", can_virtual_display: true,
          virtual_display: true, on_virtual_display: true });
check(h.nodes["stream-virtual-row"].hidden === false, "shown where it can");
check(h.nodes["stream-virtual"].disabled === false, "and usable");
check(h.nodes["stream-virtual"].checked === true, "and reflects the host");
check(/Turning it off removes it/
        .test(h.nodes["stream-virtual-note"].textContent),
      "and says that turning it off gets rid of them, which is the thing that "
      + "was asked for and was not obvious");

console.log("\nscreens are identified by name, not by position in a list");
// Windows renumbers the monitor list whenever a screen is added or removed,
// and making a virtual display does exactly that. An index chosen a moment
// earlier can point at a different screen by the time it is used -- which is
// why choosing one appeared to try and then stay put.
h = harness();
h.paint({ screens: two, monitor: "" });
const values = h.nodes["stream-screen"].children.map((c) => c.value);
check(values.includes("\\\\.\\DISPLAY1") && values.includes("\\\\.\\DISPLAY11"),
      `the options carry device names: ${JSON.stringify(values)}`);
check(!values.includes("0") && !values.includes("1"),
      "and not positions, which do not survive a display being added");
check(values[0] === "", "with the empty one meaning whichever is main");

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
