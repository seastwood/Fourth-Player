/* Choosing what the game is told your controller is.
 *
 * The letters printed on a pad only mean anything if the game agrees which pad
 * it is: a game reads the controller's identity and names its buttons from it.
 * The page already had two ways to paper over that -- pick a different layout,
 * or swap A/B and X/Y -- and this is the one that fixes the cause.
 *
 * Two things here are easy to get wrong and invisible when they are:
 *
 *   * The host's answer to a choice carries no list of kinds, only the value.
 *     Treating that as "the host offers nothing" hides the control the instant
 *     somebody uses it. The first version of this did exactly that.
 *   * One kind is not a choice, and a dropdown that cannot be changed is worse
 *     than no dropdown.
 */
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
let bad = 0;
const check = (cond, what) => {
  console.log((cond ? "  ok   " : "  FAIL ") + what);
  if (!cond) bad += 1;
};

const body = src.slice(src.indexOf("const PAD_KIND_NAMES"),
                       src.indexOf("function joined("));

function page(allowed = true) {
  const nodes = {
    "pads-kind-row": { hidden: true },
    "pads-kind": {
      dataset: {}, value: "", innerHTML: "", kids: [],
      appendChild(o) { this.kids.push(o); },
      addEventListener(_n, fn) { this.fire = fn; },
    },
  };
  const sent = [];
  const run = new Function("nodes", "sent", "allowed", `
    const el = (id) => nodes[id];
    // One setting for the machine, so the control is only drawn for an
    // account trusted with the machine's settings.
    const may = () => allowed;
    const document = { activeElement: null,
                       createElement: () => ({ value: "", textContent: "" }) };
    const socket = { readyState: 1, send: (t) => sent.push(JSON.parse(t)) };
    ${body}
    return { padKindFrom, tellHostPadKind, watchPadKind };
  `)(nodes, sent, allowed);
  return { nodes, sent, run };
}

console.log("the host names the kinds, so the page carries no list of its own");
let { nodes, sent, run } = page();
run.padKindFrom(["xbox360", "ds4"], "xbox360");
check(nodes["pads-kind-row"].hidden === false, "two kinds shows the control");
check(nodes["pads-kind"].kids.length === 2,
      "one option each, built from what the host sent: "
      + nodes["pads-kind"].kids.length);
check(nodes["pads-kind"].value === "xbox360",
      "and it shows what the seat currently is");

console.log("\nthe host's answer changes the value without hiding the control");
run.padKindFrom(null, "ds4");
check(nodes["pads-kind-row"].hidden === false,
      "still shown -- the answer carries no list, and treating that as 'no "
      + "kinds offered' is what hid it");
check(nodes["pads-kind"].value === "ds4", "and the value moved");

console.log("\none kind is not a choice");
({ nodes, run } = page());
run.padKindFrom(["xbox360"], "xbox360");
check(nodes["pads-kind-row"].hidden === true, "so nothing is shown");
({ nodes, run } = page());
run.padKindFrom([], "xbox360");
check(nodes["pads-kind-row"].hidden === true, "and neither is none");

console.log("\nand it is not drawn for somebody who may not change it");
// It moves everybody's controller and is remembered on the host, so it is
// gated like the picture settings. Hidden rather than disabled: a control
// that cannot be used is a question somebody spends time on. This is drawing
// only -- the host refuses the message regardless of what a page sends.
({ nodes, run } = page(false));
run.padKindFrom(["xbox360", "ds4"], "xbox360");
check(nodes["pads-kind-row"].hidden === true,
      "a guest with no say does not see it");

console.log("\nthe offered list survives until an account arrives");
// The list comes with the welcome and signing in happens later, so an admin
// who logs in after joining would otherwise never see the control: the kinds
// had already been and gone while nobody was allowed to look.
({ nodes, run } = page(true));
run.padKindFrom(["xbox360", "ds4"], "xbox360");
run.padKindFrom(null, "ds4");
check(nodes["pads-kind-row"].hidden === false
      && nodes["pads-kind"].kids.length === 2,
      "a later call with no list keeps the one it was given");

console.log("\nand with no list at all it asks, rather than staying hidden");
// The reported fault: the list arrived once with the welcome, so a resume, a
// reconnect or signing in after joining left the control invisible for the
// rest of the session. Asking is cheap and sending no kind is a read.
({ nodes, sent, run } = page(true));
run.padKindFrom(null, "ds4");
check(sent.length === 1 && sent[0].t === "padkind"
      && sent[0].kind === undefined,
      "it asks, with no kind -- which the host treats as a question: "
      + JSON.stringify(sent));
check(nodes["pads-kind-row"].hidden === true,
      "and stays hidden until the answer comes, rather than showing an empty "
      + "dropdown");
run.padKindFrom(["xbox360", "ds4"], "ds4");
check(nodes["pads-kind-row"].hidden === false
      && nodes["pads-kind"].value === "ds4",
      "then appears, set to what the host said");

console.log("\nchoosing tells the host, once");
({ nodes, sent, run } = page());
run.padKindFrom(["xbox360", "ds4"], "xbox360");
run.tellHostPadKind("ds4");
check(sent.length === 1 && sent[0].t === "padkind" && sent[0].kind === "ds4",
      "one message naming the kind: " + JSON.stringify(sent));
run.tellHostPadKind("xbox360");
check(sent.length === 1,
      "and choosing what it already is sends nothing -- every send unplugs a "
      + "controller and plugs another in, which a game notices");

console.log("\nthe change handler is attached once, not per call");
({ nodes, run } = page());
run.watchPadKind();
const first = nodes["pads-kind"].fire;
run.watchPadKind();
check(nodes["pads-kind"].fire === first && nodes["pads-kind"].dataset.wired,
      "a second call adds no second listener");

console.log("\nand the page asks for it when it joins");
const joined = src.slice(src.indexOf("function joined("),
                         src.indexOf("function joined(") + 1200);
check(joined.includes("padKindFrom(message.pad_kinds, message.pad_kind)"),
      "joined() builds it from what the host sent");
check(joined.includes("watchPadKind()"),
      "and wires the control, or choosing would do nothing at all");
check(src.includes('case "padkind":'),
      "and the host's answer has a home in the dispatch");

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
