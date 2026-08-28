"""Every function the page calls is a function the page has.

describePad() was called from two places and defined in none. It threw on every
call, and the symptom was not an error anybody saw -- it was a controller that
connected and never got named, on a phone, where the fallback prompt that made
it look fine on a desktop is never shown.

Nothing here type-checks anything. It reads the calls out of the client scripts
and asks whether each name is defined somewhere, which is the whole of what was
wrong.
"""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB = os.path.join(ROOT, "web")

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


sources = {name: open(os.path.join(WEB, name)).read()
           for name in ("app.js", "frame.js")}
everything = "\n".join(sources.values())

# Anything the browser, the language or the test harness provides. A name here
# is a promise that something else defines it; a name missing from here that is
# genuinely global shows up as a failure and gets added.
PROVIDED = {
    # language and built-ins
    "Array", "Object", "String", "Number", "Boolean", "Math", "JSON", "Date",
    "Error", "Map", "Set", "Promise", "RegExp", "Uint8Array", "DataView",
    "ArrayBuffer", "TextEncoder", "TextDecoder", "parseInt", "parseFloat",
    "isNaN", "isFinite", "encodeURIComponent", "decodeURIComponent", "atob",
    "btoa", "structuredClone", "queueMicrotask", "Symbol", "BigInt",
    # browser
    "fetch", "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "requestAnimationFrame", "cancelAnimationFrame", "alert", "confirm",
    "WebSocket", "RTCPeerConnection", "RTCRtpReceiver", "MediaStream",
    "AbortController", "Event", "CustomEvent", "URL", "URLSearchParams",
    "Blob", "FileReader", "Image", "Audio", "getComputedStyle", "matchMedia",
    "reportError",
    # ours, from the other file
    "showTouch",
}

print("every call resolves to a definition")
defined = set(PROVIDED)
for pattern in (r"function\s+([A-Za-z_$][\w$]*)",
                r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=",
                r"([A-Za-z_$][\w$]*)\s*:\s*(?:async\s+)?function",
                r"\bclass\s+([A-Za-z_$][\w$]*)"):
    defined.update(re.findall(pattern, everything))

# Calls, minus anything reached through a dot -- those are methods on some
# object and not this file's business.
called = set()
for match in re.finditer(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(", everything):
    name = match.group(1)
    if name in ("if", "for", "while", "switch", "catch", "return", "function",
                "typeof", "new", "await", "case", "do", "else", "async", "of",
                "in", "delete", "void", "yield"):
        continue
    called.add(name)

missing = sorted(called - defined)
check(not missing, "no call has a missing definition: " + (", ".join(missing) or "none"))

print("a controller gets a readable name")
node = shutil.which("node") or shutil.which("nodejs")
if not node:
    print("  --   node is not installed, so describePad cannot be run")
else:
    body = sources["app.js"]
    start = body.index("function describePad")
    end = body.index("\nfunction forgetPad")
    harness = """
    let padName = "";
    function paintPicker() {}
    %s
    const cases = [
      // What real browsers report, and what a person should read.
      ["Xbox Wireless Controller (STANDARD GAMEPAD Vendor: 045e Product: 02fd)",
       "Xbox Wireless Controller"],
      ["Pro Controller (Vendor: 057e Product: 2009)", "Pro Controller"],
      ["054c-05c4-Wireless Controller", "Wireless Controller"],
      ["", "Controller"],
    ];
    const out = [];
    for (const [id, want] of cases) {
      describePad({ id });
      out.push([id, padName, want]);
    }
    console.log(JSON.stringify(out));
    """ % body[start:end]
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    if result.returncode != 0:
        check(False, "describePad ran: " + result.stderr.strip()[:200])
    else:
        import json
        for id_, got, want in json.loads(result.stdout):
            check(got == want,
                  "%r reads as %r" % (id_ or "(no id)", got))
        check(all(len(got) <= 28 for _, got, _ in json.loads(result.stdout)),
              "no name is long enough to break the chip")

print(("FAILED: %d" % len(fails)) if fails else "test_calls: all ok")
sys.exit(1 if fails else 0)
