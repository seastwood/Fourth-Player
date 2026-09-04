"""Who else is in the room, and how their connection is doing.

Chat told you what people said and nothing about who they were. "Who is on
player 2" and "is it me or is it him" were questions the room asked out loud
and the page could not answer, and the second one matters: a guest whose
buttons feel late has no way to tell a bad connection from a bad game, and
neither does anybody trying to help them.

The measuring has to happen at the guest's end. Round trip time and lost
packets are properties of the path to *them*; the host sees only its own half
and would report every guest as perfect. So each page measures itself, sends
the numbers, and the host is the place they are collected and handed back out
-- which means the numbers arriving here are somebody else's word, and are
clamped rather than believed.

The other thing worth holding still is what this list is allowed to say. The
host's own roster may carry anything, because it is for the person who owns
the television. This one goes to every guest, so it carries names, seats and
connection quality, and nothing that says which machine or which network
anybody is on.
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


# ---------------------------------------------------------------- the host --

import asyncio                                                  # noqa: E402

try:
    from fourthplayer.config import Config
    from fourthplayer.session import LiveSession, GuestConnection
    HAVE_HOST = True
except ModuleNotFoundError as exc:
    # session reaches the pad layer, which needs evdev. The host half of this
    # runs on the console; the drawing half runs anywhere.
    print("SKIPPED the host half: %s" % exc)
    HAVE_HOST = False


class FakePad:
    def __init__(self, name="pad"):
        self.name = name
        self.path = "/dev/input/eventX"

    def release_all(self):
        pass

    def forget(self, sender=None):
        pass

    def adopt_new_sender(self, sender=None):
        pass


class FakePads(list):
    def __init__(self, pads):
        super().__init__(pads)
        self.names = [p.name for p in pads]

    def name_for(self, index):
        return self[index].name

    def release(self, index):
        return True


def a_session(count=2):
    loop = asyncio.new_event_loop()
    session = LiveSession(Config(), loop)
    session.pads = FakePads([FakePad("Fourth Player 1"),
                             FakePad("Fourth Player 2"),
                             FakePad("Fourth Player 3")])
    guests = []
    for slot in range(count):
        guest = GuestConnection(session, slot, socket=None)
        guest.label = "Guest %d" % (slot + 1)
        session.guests[slot] = guest
        guests.append(guest)
    return session, guests, loop


if HAVE_HOST:
    print("the list the guests get")
    session, guests, loop = a_session()
    rows = session.people()
    check(len(rows) == 2, "one row per guest, got %d" % len(rows))
    check([r["slot"] for r in rows] == [0, 1], "in seat order")
    check(rows[0]["pad_name"] == "Fourth Player 1",
          "the controller is named the way the seat picker names it, got %r"
          % rows[0]["pad_name"])
    check(rows[0]["rtt"] is None,
          "a guest who has not reported yet has no ping rather than a made-up one")

    print("\nnothing in it says where anybody is")
    flat = json.dumps(rows).lower()
    for secret in ("address", "token", "ip", "socket", "candidate", "invite"):
        check(secret not in flat, "no %r anywhere in the list" % secret)

    print("\na guest's own measurements, which are their word and not gospel")
    session.set_health(guests[0], {"rtt": 42.4, "loss": 1.25, "fps": 59.6})
    row = session.people()[0]
    check(row["rtt"] == 42.4, "a plain number is kept, got %r" % row["rtt"])
    check(row["fps"] == 59.6, "and so is the frame rate")

    session.set_health(guests[0], {"rtt": "nonsense", "loss": None, "fps": []})
    row = session.people()[0]
    check(row["rtt"] is None and row["loss"] is None and row["fps"] is None,
          "anything that is not a number becomes nothing, got %r" % row)

    session.set_health(guests[0], {"rtt": 10 ** 9, "loss": -50, "fps": 10 ** 6})
    row = session.people()[0]
    check(row["rtt"] == 9999, "an absurd ping is clamped, got %r" % row["rtt"])
    check(row["loss"] == 0, "and so is a negative loss, got %r" % row["loss"])
    check(row["fps"] == 240, "and an impossible frame rate, got %r" % row["fps"])

    session.set_health(guests[0], {"rtt": float("nan"), "loss": 1, "fps": 30})
    check(session.people()[0]["rtt"] is None, "NaN is not a ping")

    print("\nthe list is pushed when it changes, not only when asked for")
    sent = []
    session.on_notice = sent.append
    session.drop(1)
    check(any(m.get("t") == "people" for m in sent),
          "somebody leaving tells everybody still here")
    left = [m for m in sent if m.get("t") == "people"][-1]["people"]
    check([r["slot"] for r in left] == [0], "and the one who left is gone from it")
    loop.close()

print("\nthe two messages this needs the server to understand")
server = open(os.path.join(ROOT, "fourthplayer", "server.py")).read()
check('kind == "people"' in server,
      "a guest can ask who is here")
check('kind == "health"' in server,
      "and can report how their own connection is doing")
check("set_health(guest, message)" in server,
      "which is passed through the clamp rather than stored as sent")

# -------------------------------------------------------------- the client --

source = open(os.path.join(ROOT, "web", "app.js")).read()
page = open(os.path.join(ROOT, "web", "index.html")).read()
style = open(os.path.join(ROOT, "web", "style.css")).read()

print("\nthe panel it is drawn into")
check('id="chat-people"' in page, "the strip is in the chat panel")
check('id="chat-person"' in page, "and so is the detail it opens")
strip = re.search(r"\.chat-people \{([^}]*)\}", style)
check(bool(strip), "the strip has a rule of its own")
if strip:
    check("overflow-x: auto" in strip.group(1),
          "it scrolls sideways rather than wrapping, so a fourth guest does "
          "not push the conversation off a phone")
    check("flex: 0 0 auto" in strip.group(1),
          "and it is not part of the log's own scrolling")
check(".chat-person-chip" in style and "min-height: 2rem" in style,
      "the chips are tall enough to be tapped")

if not shutil.which("node"):
    print("\nSKIPPED the drawing: node is not installed")
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


harness = """
'use strict';
%(code)s

// Enough of a document to draw into and read back.
function makeNode(tag) {
  return {
    tagName: tag, className: "", type: "", hidden: false,
    children: [], attrs: {}, text: "", handlers: {},
    set textContent(v) { this.text = v; this.children = []; },
    get textContent() {
      return this.text + this.children.map((c) => c.textContent).join("");
    },
    set innerHTML(v) { if (!v) this.children = []; },
    get innerHTML() { return ""; },
    appendChild(c) { this.children.push(c); return c; },
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(k, fn) { (this.handlers[k] = this.handlers[k] || []).push(fn); },
    click() { (this.handlers["click"] || []).forEach((fn) => fn({})); },
  };
}
global.document = {
  createElement: makeNode,
  createTextNode: (t) => ({ textContent: t, children: [], className: "" }),
};
const boxes = { "chat-people": makeNode("div"), "chat-person": makeNode("div") };
global.el = (id) => boxes[id];
global.mySlot = 1;

function chips() { return boxes["chat-people"].children; }
function facts() {
  const out = {};
  const walk = (node) => {
    (node.children || []).forEach((c) => {
      if (c.tagName === "dl") {
        for (let i = 0; i + 1 < c.children.length; i += 2) {
          out[c.children[i].textContent] = c.children[i + 1].textContent;
        }
      } else walk(c);
    });
  };
  walk(boxes["chat-person"]);
  return out;
}

global.people = [
  { slot: 0, name: "Ada", pad: 0, pad_name: "Fourth Player 1", player: 1,
    here: true, seconds: 95, rtt: 18, loss: 0.4, fps: 60, driving: false, held: 0 },
  { slot: 1, name: "Bob", pad: 1, pad_name: "Fourth Player 2", player: null,
    here: true, seconds: 4000, rtt: 210, loss: 9, fps: 41, driving: true, held: 12 },
  { slot: 2, name: "Cy", pad: 2, pad_name: "Fourth Player 3", player: 3,
    here: false, seconds: 12, rtt: null, loss: null, fps: null,
    driving: false, held: 0 },
];
global.personOpen = null;

paintPeople();
const out = {
  count: chips().length,
  labels: chips().map((c) => c.textContent),
  classes: chips().map((c) => c.className),
  bands: chips().map((c) => (c.children[0] || {}).className || ""),
  detailHiddenAtFirst: boxes["chat-person"].hidden,
  bands_direct: [pingBand(18), pingBand(210), pingBand(null), pingBand(90)],
  times: [saidTime(95), saidTime(4000), saidTime(12)],
};
// Somebody taps the second chip.
chips()[1].click();
out.openFacts = facts();
out.openClasses = chips().map((c) => c.className);
// And taps it again.
chips()[1].click();
out.closedAfterSecondTap = boxes["chat-person"].hidden;
// Ada, who has no player port bound.
chips()[0].click();
out.adaFacts = facts();
console.log(JSON.stringify(out));
""" % {"code": "\n\n".join([lift("pingBand"), lift("saidTime"),
                            lift("paintPeople"), lift("paintPerson")])}

proc = subprocess.run([shutil.which("node"), "-e", harness],
                      capture_output=True, text=True)
if proc.returncode != 0:
    print("  FAIL  node could not run it:\n" + proc.stderr.strip()[-1500:])
    sys.exit(1)
out = json.loads(proc.stdout.strip().splitlines()[-1])

print("\nthe strip")
check(out["count"] == 3, "one chip per person, got %d" % out["count"])
check(out["labels"][0] == "Ada", "named, got %r" % out["labels"][0])
check("(you)" in out["labels"][1] and "(you)" not in out["labels"][0],
      "and this page's own row says so, got %r" % out["labels"])
check("is-away" in out["classes"][2],
      "somebody with no picture is dimmed rather than dropped from the list")
check("is-away" not in out["classes"][0], "and everybody else is not")
check(out["detailHiddenAtFirst"] is True,
      "nothing is expanded until somebody asks -- the strip alone is the "
      "compact thing that was wanted")

print("\nthe ping, as a colour before it is a number")
check(out["bands_direct"] == ["is-good", "is-poor", "", "is-fair"],
      "under 50 good, 90 fair, 210 poor, unmeasured blank; got %s"
      % out["bands_direct"])
check(out["bands"][2] == "chat-person-ping ",
      "a guest who has not reported gets no colour rather than a green one")

print("\ntapping one open")
facts_open = out["openFacts"]
check(facts_open.get("Controller") == "Fourth Player 2",
      "it says which controller they are on, got %r" % facts_open.get("Controller"))
check(facts_open.get("Ping") == "210 ms", "and their ping, got %r"
      % facts_open.get("Ping"))
check(facts_open.get("Picture lost") == "9.0%", "and what they are losing")
check(facts_open.get("Frame rate") == "41 per second", "and the frame rate")
check("screen" in json.dumps(facts_open).lower(),
      "and that this one is allowed to use the screen directly")
check("12" in (facts_open.get("Presses held back") or ""),
      "and the presses that went nowhere, which is the answer to 'my "
      "controller did nothing'")
check("is-open" in out["openClasses"][1], "the chip shows as the open one")
check(out["closedAfterSecondTap"] is True, "tapping it again closes it")

print("\nand somebody the game has not given a player number")
check("not bound" in (out["adaFacts"].get("In the game") or "").lower()
      or out["adaFacts"].get("In the game") == "Player 1",
      "says so rather than inventing one, got %r"
      % out["adaFacts"].get("In the game"))
check(out["times"] == ["2 min", "1h 7m", "12s"],
      "how long they have been here reads in the right unit, got %s"
      % out["times"])

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("test_people: all ok")
