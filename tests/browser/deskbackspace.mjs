/* Backspace from a phone keyboard.
 *
 * It did nothing on iOS. The capture field was always left empty, and Safari
 * raises no `beforeinput` at all for a deletion with nothing to delete -- so
 * there was no event to map to a Backspace and the key was silently dead, on
 * phones only. A desktop never noticed: the global keydown listener catches
 * Backspace there, and a phone keyboard produces no keydown worth reading.
 *
 * The rule now: there is always exactly one deletable character behind the
 * caret, it is invisible, and nothing read out of the field ever contains it.
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const from = app.indexOf("const DESK_SENTINEL");
const until = app.indexOf("/* Characters, in batches");
if (from < 0 || until < 0) {
  console.log("FAIL could not find the sentinel helpers in app.js");
  process.exit(1);
}

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

/* A contenteditable with just enough DOM to be armed and read. */
function makeField() {
  return { textContent: "", _selected: null };
}
function harness(activeIsField = true) {
  const field = makeField();
  const document = {
    get activeElement() { return activeIsField ? field : null; },
    createRange: () => ({
      selectNodeContents(node) { this.node = node; },
      collapse(toStart) { this.collapsed = toStart ? "start" : "end"; },
    }),
  };
  const window = {
    getSelection: () => ({
      removeAllRanges() {},
      addRange(r) { field._selected = r.collapsed; },
    }),
  };
  const body = app.slice(from, until)
    + "; return { DESK_SENTINEL, deskFieldText, deskArmField, deskFieldClear };";
  const fns = new Function("document", "window", body)(document, window);
  return { field, fns };
}

let { field, fns } = harness();
const S = fns.DESK_SENTINEL;

check(S.length === 1 && S.charCodeAt(0) === 0x200b,
      "the sentinel is a single zero-width space, so nothing is visible");

// -- there is always something to delete ----------------------------------
fns.deskArmField(field);
check(field.textContent === S,
      "an armed field holds exactly one deletable character");
check(field._selected === "end",
      "with the caret after it -- in front of it is a dead Backspace too");

// -- and clearing leaves it armed, never empty ----------------------------
field.textContent = "hello";
fns.deskFieldClear(field);
check(field.textContent === S,
      "clearing arms the field rather than emptying it");
check(field.textContent !== "",
      "never empty, which is the state a phone cannot report a backspace from");

// -- nothing downstream ever sees the sentinel ----------------------------
field.textContent = S;
check(fns.deskFieldText(field) === "",
      "a field holding only the sentinel reads as nothing typed");
field.textContent = S + "abc";
check(fns.deskFieldText(field) === "abc",
      "and typed text reads back without it");
field.textContent = "a" + S + "b" + S;
check(fns.deskFieldText(field) === "ab",
      "wherever the keyboard happens to leave copies of it");
field.textContent = S + "line\nbreak\r";
check(fns.deskFieldText(field) === "linebreak",
      "newlines are still stripped as they always were");

// -- it must not steal the caret when the field is not focused ------------
({ field, fns } = harness(false));
field.textContent = "";
fns.deskArmField(field);
check(field.textContent === fns.DESK_SENTINEL,
      "an unfocused field is still armed with the character");
check(field._selected === null,
      "but its caret is left alone, so arming never steals focus");

// -- the wiring the fix depends on ----------------------------------------
check(/how\.indexOf\("delete"\) === 0/.test(app),
      "a delete from the phone keyboard is still mapped to a key");
check(/setTimeout\(\(\) => deskArmField\(field\), 0\)/.test(app),
      "and the sentinel is put back after the event, not during it");
check(/deskArmField\(field\);\s*\n\s*measureLift/.test(app),
      "focus arms it too, since the clear happens before the focus");

console.log("");
if (fails) { console.log(`deskbackspace: ${fails} FAILED`); process.exit(1); }
console.log("deskbackspace: all ok");
