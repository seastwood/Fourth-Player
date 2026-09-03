"""Which way round the screen sits, and who decides.

The manifest decided, and decided absolutely: `"orientation": "landscape"` is
obeyed by an installed Android app whatever the phone is doing -- turn it, turn
off its rotation lock, nothing. iOS ignores manifest orientation altogether, so
the same file produced a phone that turned and a phone that would not, and the
whole thing looked like an Android fault rather than a line somebody wrote.

What is held still here: the manifest no longer forces anything, all three
choices exist, only a named one is ever stored, and the control is not offered
where the browser cannot turn a screen at all. That last is not politeness --
a control that silently does nothing is worse than no control, because it
sends somebody looking for the reason it did not work.
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


source = open(os.path.join(ROOT, "web", "app.js")).read()
page = open(os.path.join(ROOT, "web", "index.html")).read()
manifest = json.load(open(os.path.join(ROOT, "web", "manifest.webmanifest")))

print("the manifest no longer decides")
check(manifest.get("orientation") in (None, "any"),
      "it asks for no particular orientation: %r" % manifest.get("orientation"))
check(manifest.get("display") == "fullscreen",
      "and still opens fullscreen, which is a separate question")

print("the choice is on the panel")
row = page.split('id="orient-row"')[1].split("</p>")[0]
for want in ("any", "landscape", "portrait"):
    check('value="%s"' % want in row, "%s is offered" % want)
check("hidden" in page.split('id="orient-row"')[0].rsplit("<p", 1)[-1]
      or 'id="orient-row" hidden' in page,
      "and the row starts hidden, to be shown only where it can work")
check("touch-only" not in row,
      "it is not tied to the on-screen pad: a guest with a controller still "
      "looks at the picture")

node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the browser half cannot be run.")
    sys.exit(1 if fails else 0)


def lift(name):
    start = source.index("function " + name + "(")
    depth = 0
    for j in range(source.index("{", start), len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[start:j + 1]
    raise AssertionError(name)


def constant(name):
    return re.search(r"^const " + name + r" = .*$", source, re.M).group(0)


HARNESS = "\n".join(
    [constant("ORIENT_KEY"), constant("ORIENTATIONS")]
    + [lift(n) for n in ("canTurn", "savedOrient", "applyOrient",
                         "paintOrient")]) + """
const job = JSON.parse(require("fs").readFileSync(0, "utf8"));

const store = Object.assign({}, job.stored);
const localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};

const locks = [];
let unlocked = 0;
const orientation = {
  lock: (want) => {
    locks.push(want);
    if (job.refuse) return Promise.reject(new Error("not an installed app"));
    if (job.throws) throw new Error("no");
    return Promise.resolve();
  },
  unlock: () => { unlocked += 1; },
};
const screen = job.canTurn ? { orientation } : {};
const window = { screen: job.canTurn ? screen : {} };

const note = { textContent: "" };
const row = { hidden: null };
const picker = { value: null };
const el = (id) => (id === "pads-orient-note" ? note
                    : id === "orient-row" ? row
                    : id === "pads-orient" ? picker : null);

if (job.apply !== undefined) applyOrient(job.apply);
paintOrient();
// The rejection lands a tick later, which is the whole point of it being a
// promise: the note it writes has to be read after that.
setTimeout(() => {
  process.stdout.write(JSON.stringify({
    locks, unlocked, note: note.textContent, hidden: row.hidden,
    picked: picker.value, saved: savedOrient() }));
}, 0);
"""


def run(**job):
    job.setdefault("canTurn", True)
    job.setdefault("stored", {})
    done = subprocess.run([node, "-e", HARNESS], input=json.dumps(job),
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(done.stderr[:600])
    return json.loads(done.stdout)


print("what is remembered")
out = run()
check(out["saved"] == "any", "nothing stored means the phone decides")
out = run(stored={"fp:orient": "portrait"})
check(out["saved"] == "portrait" and out["picked"] == "portrait",
      "a stored choice comes back, and the picker shows it")
out = run(stored={"fp:orient": "sideways"})
check(out["saved"] == "any",
      "and a value nobody wrote is not obeyed -- three answers, no fourth")

print("what it does")
out = run(apply="landscape")
check(out["locks"] == ["landscape"], "a named choice locks the screen")
check("landscape" in out["note"], "and says so")
out = run(apply="any")
# unlock() drops back to the *default*, and an installed app's default is the
# manifest it was installed with -- which is how an app added while the
# manifest still said landscape went back to landscape when told to follow the
# phone. "any" is a lock whose value is every orientation, so it overrides
# that instead of deferring to it.
check(out["locks"] == ["any"] and out["unlocked"] == 0,
      "following the phone locks to any, which beats a stale manifest")
check("rotation lock" in out["note"],
      "and says the phone's own lock is back in charge, which it is")
out = run(apply="any", refuse=True)
check(out["unlocked"] == 1,
      "a browser that refuses 'any' as a lock gets the old unlock instead")
check("rotation lock" in out["note"],
      "and is told the same thing, because the same thing happened")
out = run(apply="any", throws=True)
check(out["unlocked"] == 1, "and so does one that throws on it")

print("where it cannot work")
out = run(canTurn=False, apply="landscape")
check(out["hidden"] is True, "a browser with no orientation lock is not offered it")
check(out["locks"] == [] and out["note"] == "",
      "and nothing is attempted or claimed")
out = run(canTurn=True)
check(out["hidden"] is False, "a browser that has one is")

print("when it is refused")
out = run(apply="portrait", refuse=True)
check("installed app" in out["note"],
      "a tab is told the installed app is what can be held, not that it failed")
out = run(apply="portrait", throws=True)
check("will not turn" in out["note"],
      "and a browser that throws outright says that instead of nothing")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("all good")
