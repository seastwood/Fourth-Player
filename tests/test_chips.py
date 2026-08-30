"""The two chips that say the least.

The connection chip used to spell out "connected", which is a word a guest has
to read and finish before it tells them the one thing they wanted. It is a lit
pip now -- but the states that carry a *reason* still have to keep it, because
"no H.264" is why somebody has no picture and losing it to save four characters
would be a bad trade.

The clock used to sit there reading "no time limit" for a session that had
none: furniture that never changes and never will. Now it is simply absent.
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


node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("SKIPPED: node is not installed, so the browser half cannot be run.")
    sys.exit(0)

source = open(os.path.join(ROOT, "web", "app.js")).read()


def lift(name):
    """One function out of app.js, which touches the DOM at load."""
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


words = re.search(r"const LINK_WORDS = \{.*?\};", source, re.S).group(0)

HARNESS = words + "\n" + lift("setLink") + "\n" + lift("timeLeft") + "\n" \
    + lift("startClock") + "\n" + """
const nodes = {};
function el(id) {
  if (!nodes[id]) nodes[id] = { className: "", textContent: "", hidden: false,
                                title: "", attrs: {},
                                setAttribute(k, v) { this.attrs[k] = v; } };
  return nodes[id];
}
function setChip(id, text, kind) {
  const c = el(id);
  c.textContent = text;
  c.className = "chip" + (kind ? " " + kind : "");
  c.hidden = false;
}
let clockTimer = null;
function clearInterval() {}
function setInterval() { return 1; }

const out = {};
const job = JSON.parse(require("fs").readFileSync(0, "utf8"));
if (job.link) { setLink(job.link[0], job.link[1]); out.link = nodes.link; }
if ("clock" in job) { startClock(job.clock); out.clock = nodes.clock; }
process.stdout.write(JSON.stringify(out));
"""


def run(job):
    done = subprocess.run([node, "-e", HARNESS], input=json.dumps(job),
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(done.stderr[:500])
    return json.loads(done.stdout)


print("connected is a lit pip and not a word")
on = run({"link": ["ok", None]})["link"]
check(on["textContent"] == "",
      "nothing to read: %r" % on["textContent"])
check("ok" in on["className"] and "link" in on["className"],
      "and the class the pip is lit by: %r" % on["className"])
check(on["attrs"].get("aria-label") == "Connected",
      "still announced as connected: %r" % on["attrs"])
check(on["hidden"] is False, "and visible")

print("so is not connected, in the other colour")
for kind, said in (("warn", "Reconnecting"), ("", "Connecting"),
                   ("bad", "Not connected")):
    off = run({"link": [kind, None]})["link"]
    check(off["textContent"] == "", "%s says nothing" % (kind or "connecting"))
    check("ok" not in off["className"],
          "%s is not lit: %r" % (kind or "connecting", off["className"]))
    check(off["attrs"].get("aria-label") == said,
          "%s announces %r" % (kind or "connecting", off["attrs"].get("aria-label")))

print("but a reason is a reason, and is kept")
for detail in ("no H.264", "no video", "controller offline"):
    bad = run({"link": ["bad", detail]})["link"]
    check(bad["textContent"] == detail, "%r survives" % detail)
    check(bad["title"] == detail, "and is on the tooltip too")

print("a session with no limit has no clock at all")
none = run({"clock": None})["clock"]
check(none["hidden"] is True, "hidden: %r" % none["hidden"])
check("no time limit" not in none["textContent"].lower(),
      "and says nothing about limits: %r" % none["textContent"])

print("a session with one counts it down")
ticking = run({"clock": 630})["clock"]
check(ticking["hidden"] is False, "shown")
check(ticking["textContent"] == "10:30 left",
      "with the time on it: %r" % ticking["textContent"])
check("warn" not in ticking["className"], "calmly, with ten minutes to go")

soon = run({"clock": 90})["clock"]
check(soon["textContent"] == "1:30 left", "and %r" % soon["textContent"])
check("warn" in soon["className"], "loudly, with ninety seconds to go")

print()
if fails:
    print("FAILURES: %d" % len(fails))
    sys.exit(1)
print("test_chips: all ok")
