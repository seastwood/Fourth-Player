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

# What is inside a string is not code. CSS carries function calls of its own
# -- calc(), translate(), url() -- and a page that builds a transform out of
# numbers has those in string literals, where this audit read them as calls
# into JavaScript that nothing defined. Comments go the same way: an example
# written in prose is not a call either.
def code_only(text):
    """The source with string literals and comments blanked out.

    One left-to-right scan rather than a pass per kind, because the passes
    were order-dependent and the order was wrong. Line comments were stripped
    before strings, so the `//` inside

        new WebSocket(`${scheme}//${location.host}/ws`)

    was read as a comment and deleted, leaving an unclosed backtick. That was
    survivable while the page had exactly one of those; the moment a second
    one appeared, the orphaned quote paired with it and sixty thousand
    characters of code -- and every definition in them -- vanished from this
    audit. It reported two dozen perfectly good functions as undefined.

    A scanner cannot get that wrong: inside a string, `//` is text; outside
    one, it is a comment.
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        here = text[i]
        if here == "/" and text.startswith("/*", i):
            shut = text.find("*/", i + 2)
            i = n if shut < 0 else shut + 2
            out.append(" ")
        elif here == "/" and text.startswith("//", i):
            shut = text.find("\n", i)
            i = n if shut < 0 else shut
            out.append(" ")
        elif here in "'\"`":
            quote, i = here, i + 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                # Only a template literal may cross a line; the other two
                # ending at one is what stops a stray quote eating the file.
                if text[i] == "\n" and quote != "`":
                    break
                i += 1
            out.append(" ")
        else:
            out.append(here)
            i += 1
    return "".join(out)


everything = code_only(everything)

print("every call resolves to a definition")
defined = set(PROVIDED)
for pattern in (r"function\s+([A-Za-z_$][\w$]*)",
                r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=",
                r"([A-Za-z_$][\w$]*)\s*:\s*(?:async\s+)?function",
                # A class method: `heard(event) {`. It is a definition and it
                # reads exactly like a call, which is how three perfectly real
                # methods came to be reported as called and never defined.
                r"(?m)^\s{2,}(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^()]*\)\s*\{",
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

# A handler passed by name is a reference, not a call, so the check above --
# which looks for `name(` -- cannot see it. fitGutter() was deleted along with
# the one call to it, and three `addEventListener("resize", fitGutter)` lines
# were left behind. Evaluating that argument threw a ReferenceError at load,
# and every line of the script after it never ran: the guest could still join
# and receive video, because that code came earlier in the file, but the whole
# back half of the client was dead. Nothing failed loudly; it just stopped.
print("\nevery handler passed by name exists too")
handlers = set()
for match in re.finditer(
        # The event name is a string, and strings are blanked out above, so
        # what is left to match between the parentheses is nothing at all.
        r"(?:add|remove)EventListener\s*\(\s*,\s*"
        r"([A-Za-z_$][\w$]*)\s*[,)]", everything):
    handlers.add(match.group(1))
for match in re.finditer(
        r"(?:setTimeout|setInterval|requestAnimationFrame)\s*\(\s*"
        r"([A-Za-z_$][\w$]*)\s*[,)]", everything):
    handlers.add(match.group(1))
handlers -= {"function", "async", "true", "false", "null", "undefined"}
orphans = sorted(handlers - defined)
check(not orphans,
      "no handler is passed by a name nothing defines: "
      + (", ".join(orphans) or "none"))

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
    // describePad also loads whatever mapping is saved against the new name,
    // which needs a browser. Naming is what is under test here.
    function loadPadMap() {}
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
