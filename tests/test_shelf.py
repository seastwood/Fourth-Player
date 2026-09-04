"""A library of thousands of games, drawn a screenful at a time.

The shelf drew the first 400 matches and a line saying the rest were not
shown. On a library that size the line was the honest part: the other games
were unreachable except by narrowing the search until fewer than 400 matched.

It draws a chunk now, and the next chunk when somebody scrolls near the end.
The searching is unchanged and deliberately so: the whole library is on the
page, so a search still runs over all of it. It is the drawing that is
rationed, not the finding -- a card apiece for four thousand games costs a
phone seconds of stalled main thread, and the first screenful is all anybody
looks at before typing.

What matters and is easy to get wrong: changing the filter has to start the
list again rather than appending to what was there, and the marker that asks
for more has to end up after the cards it follows -- not stuck where it was
first inserted.
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


source = open(os.path.join(ROOT, "web", "app.js")).read()
css = open(os.path.join(ROOT, "web", "style.css")).read()


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


print("the shape of it")
check("slice(0, 400)" not in source, "the hard stop at 400 games is gone")
check("Showing the first 400" not in source,
      "and so is the line that explained it")
chunk = re.search(r"const SHELF_CHUNK = (\d+);", source)
check(bool(chunk), "a chunk size is stated in one place")
if chunk:
    size = int(chunk.group(1))
    check(20 <= size <= 120,
          "and it is a screenful or two rather than a page or everything: %d"
          % size)
check("IntersectionObserver" in source,
      "the next chunk is asked for by watching the end of the list")
check('rootMargin' in source,
      "with a margin, so the cards are there before the scroll reaches them "
      "and the list never visibly stops")
check(".shelf-end" in css, "and the marker has a rule of its own")

if not shutil.which("node"):
    print("\nSKIPPED the behaviour: node is not installed")
    sys.exit(1 if fails else 0)

harness = """
'use strict';
%(code)s

// Enough of a document to append to and count.
function node(tag) {
  const self = {
    tagName: tag, className: "", id: "", dataset: {}, children: [],
    hidden: false, textContent: "", scrollTop: 0,
    setAttribute() {}, addEventListener() {},
    appendChild(child) {
      // Appending something already here moves it to the end, as the DOM does
      // -- which is the whole reason the marker stays last.
      const at = self.children.indexOf(child);
      if (at >= 0) self.children.splice(at, 1);
      self.children.push(child);
      return child;
    },
    insertBefore(child, before) {
      const at = self.children.indexOf(before);
      const kids = child.__fragment || [child];
      self.children.splice(at < 0 ? self.children.length : at, 0, ...kids);
      return child;
    },
    querySelector(sel) {
      const want = sel.replace(".", "");
      return self.children.find((c) => c.className === want) || null;
    },
    set innerHTML(v) { if (!v) self.children = []; },
    get innerHTML() { return ""; },
  };
  return self;
}

const shelf = node("div");
const note = node("p");
const q = { value: "" }, fsystem = { value: "" }, fplayers = { value: "" };
global.el = (id) => ({ shelf, "browse-note": note, q, fsystem, fplayers }[id]);
global.document = {
  createElement: node,
  createDocumentFragment: () => {
    const frag = node("frag");
    frag.__fragment = frag.children;
    return frag;
  },
};
global.escapeText = (t) => String(t);
global.askFor = () => {};
global.IntersectionObserver = function (fn, opts) {
  this.seen = [];
  this.observe = (el) => { this.seen.push(el); };
  this.disconnect = () => { this.seen = []; };
  global.lastObserver = this;
};
global.window = { IntersectionObserver: global.IntersectionObserver };

let shelfShown = [], shelfDrawn = 0, shelfWatcher = null;
const shelfRows = [];
for (let i = 0; i < 1000; i++) {
  shelfRows.push({ id: "g" + i, label: (i %% 2 ? "Alpha " : "Beta ") + i,
                   short: "SNES", system: "snes", bucket: "1", players: 1 });
}
global.shelfRows = shelfRows;

function cards() {
  return shelf.children.filter((c) => c.className === "card").length;
}

const out = {};
filterShelf();
out.first = cards();
out.markerLast = shelf.children[shelf.children.length - 1].className;
out.note = note.textContent;
out.noteHidden = note.hidden;

drawMore();
out.second = cards();
out.markerStillLast = shelf.children[shelf.children.length - 1].className;

// Everything, one chunk at a time.
let guard = 0;
while (cards() < 1000 && guard++ < 200) drawMore();
out.all = cards();
out.markerHiddenAtEnd = shelf.children[shelf.children.length - 1].hidden;

// A search that matches half of them starts the list again.
q.value = "alpha";
filterShelf();
out.afterSearch = cards();
out.afterSearchNote = note.textContent;
out.scrolledBack = shelf.scrollTop;

q.value = "nothing matches this";
filterShelf();
out.none = cards();
console.log(JSON.stringify(out));
"""

code = "\n\n".join([lift("filterShelf"), lift("makeCard"), lift("drawMore"),
                    lift("shelfMarker"), lift("watchShelfEnd"),
                    lift("paintShelfCount"),
                    re.search(r"const SHELF_CHUNK = \d+;", source).group(0)])
proc = subprocess.run([shutil.which("node"), "-e", harness % {"code": code}],
                      capture_output=True, text=True)
if proc.returncode != 0:
    print("  FAIL  node could not run it:\n" + proc.stderr.strip()[-1200:])
    sys.exit(1)
out = json.loads(proc.stdout.strip().splitlines()[-1])
size = int(chunk.group(1))

print("\na thousand games, opened")
check(out["first"] == size,
      "one chunk is drawn, not a thousand cards; got %d" % out["first"])
check(out["markerLast"] == "shelf-end",
      "and the marker that asks for more is the last thing in the list")
check(out["note"] == "1000 games",
      "the count is the whole match, not what happens to be drawn; got %r"
      % out["note"])

print("\nscrolling to the end")
check(out["second"] == size * 2, "the next chunk is appended, got %d" % out["second"])
check(out["markerStillLast"] == "shelf-end",
      "and the marker moves down to the end again rather than being buried "
      "in the middle of the cards it was inserted before")
check(out["all"] == 1000, "eventually every game is reachable, got %d" % out["all"])
check(out["markerHiddenAtEnd"] is True,
      "and the marker takes itself away at the bottom, rather than saying "
      "'loading more' under the last game for ever")

print("\nand searching starts the list again")
check(out["afterSearch"] == size,
      "one chunk of the new list, not the old cards with new ones after them; "
      "got %d" % out["afterSearch"])
check(out["afterSearchNote"] == "500 games",
      "counted over the whole library rather than the part drawn; got %r"
      % out["afterSearchNote"])
check(out["scrolledBack"] == 0,
      "and back to the top: the list underneath has changed")
check(out["none"] == 0, "a search that matches nothing draws nothing")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_shelf: all ok")
