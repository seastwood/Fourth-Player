"""Guests saying things to each other, and to the room.

Guests can watch each other play and cannot say a word, which is a strange way
to spend an evening together. This is a line of text from whoever is holding a
pad to everybody -- including the television, because the person on the sofa is
playing too and a conversation they cannot see is one happening about them.

What is held still here is mostly about somebody else's words reaching other
people's screens, which is the one thing in this program that has never been
true before. The text is cut to length, stripped of the control characters
that make a line lie about how long it is, and rate limited per sender, and it
is *not* escaped on the way through -- the page escapes for a DOM and the
overlay draws with Cairo, and a string escaped for one is wrong in the other.

Also here: the card on the television says which key answers it, and that key
does something. An instruction on a screen that does nothing is worse than no
instruction.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


class FakeSession:
    """Just enough of LiveSession to exercise say() and recent_chat()."""

    def __init__(self, source, clock):
        self.chat = []
        self._chat_id = 0
        self._chat_at = {}
        self._now = clock
        self.said = []
        ns = {"time": __import__("time"), "log": _Log(), "self": None}
        exec(source, ns)
        self.say = ns["say"].__get__(self)
        self.recent_chat = ns["recent_chat"].__get__(self)

    def notify(self, message):
        self.said.append(message)


class _Log:
    def info(self, *args):
        pass


source = open(os.path.join(ROOT, "fourthplayer", "session.py")).read()
CHAT_KEEP = int(re.search(r"CHAT_KEEP = (\d+)", source).group(1))
CHAT_LIMIT = int(re.search(r"CHAT_LIMIT = (\d+)", source).group(1))
CHAT_GAP = float(re.search(r"CHAT_GAP = ([\d.]+)", source).group(1))


def lift(name):
    start = source.index("    def %s(" % name)
    end = source.index("\n    def ", start + 10)
    return "\n".join(line[4:] for line in source[start:end].splitlines())


now = [1000.0]
session = FakeSession(lift("say") + "\n\n" + lift("recent_chat"),
                      lambda: now[0])
session.CHAT_KEEP, session.CHAT_LIMIT, session.CHAT_GAP = (
    CHAT_KEEP, CHAT_LIMIT, CHAT_GAP)
# The constants live in the module the methods came from.
import builtins  # noqa: E402
builtins.CHAT_KEEP, builtins.CHAT_LIMIT, builtins.CHAT_GAP = (
    CHAT_KEEP, CHAT_LIMIT, CHAT_GAP)

print("what a guest says reaches everybody")
said = session.say("Ada", "hello all", slot=0)
check(said and said["text"] == "hello all", "the message is kept")
check(session.said and session.said[-1]["t"] == "chat",
      "and pushed to every page, rather than waiting to be asked for")
check(said["from"] == "Ada" and said["slot"] == 0,
      "with who said it, and from which seat")

print("and what it does not let through")
now[0] += 10
check(session.say("Ada", "   ", slot=0) is None,
      "an empty line is not a message")
now[0] += 10
long_one = session.say("Ada", "x" * (CHAT_LIMIT + 200), slot=0)
check(len(long_one["text"]) == CHAT_LIMIT,
      "a long one is cut to %d characters rather than refused" % CHAT_LIMIT)
now[0] += 10
sneaky = session.say("Ada", "one\nline\r\nreally  two", slot=0)
check(sneaky["text"] == "one line really two",
      "newlines and runs of space are flattened: a line that says it is one "
      "line has to be one line, whatever the box it was typed into allowed")

print("and how fast it lets it through")
now[0] += 10
check(session.say("Ada", "first", slot=0) is not None, "one message is fine")
check(session.say("Ada", "second", slot=0) is None,
      "a second one straight after is dropped: %.1fs is the gap" % CHAT_GAP)
check(session.say("Bob", "mine", slot=1) is not None,
      "but somebody else is not held up by it -- the limit is per person, "
      "not per room")
now[0] += CHAT_GAP + 0.1
check(session.say("Ada", "again", slot=0) is not None,
      "and the wait is over when it is over")

print("the television speaks too, and is not rate limited")
before = len(session.chat)
check(session.say("Television", "dinner is ready") is not None
      and session.say("Television", "seriously, dinner") is not None,
      "the room has no slot, so nothing throttles it -- and nobody in the "
      "room is a stranger to the people it is talking to")
check(len(session.chat) == before + 2, "both were kept")

print("what a page joining late is given")
first = session.chat[0]["id"]
check(len(session.recent_chat(0)) == len(session.chat),
      "everything, when it asks for everything")
check(all(m["id"] > first for m in session.recent_chat(first)),
      "and only what is new, when it says what it has")
for i in range(CHAT_KEEP + 20):
    now[0] += 1
    session.say("Ada", "line %d" % i, slot=0)
check(len(session.chat) == CHAT_KEEP,
      "and no more than %d lines are ever kept: this is an evening's "
      "conversation, not a record of one" % CHAT_KEEP)

print("nothing escapes anybody's words on the way through")
now[0] += 10
tagged = session.say("Ada", "<b>bold</b> & <script>alert(1)</script>", slot=0)
check("<b>" in tagged["text"] and "&amp;" not in tagged["text"],
      "the host passes them on as typed, because escaping belongs where the "
      "medium is known")
app = open(os.path.join(ROOT, "web", "app.js")).read()
heard = app.split("function heardChat")[1].split("\nfunction ")[0]
check(".textContent = message.text" in heard and ".innerHTML" not in heard,
      "and the page puts them in the DOM as text, never as markup -- this is "
      "the one place another person's words reach this page")

print("the unread dot")
page = open(os.path.join(ROOT, "web", "index.html")).read()
check('<span id="chatnew" class="chat-new" hidden></span>' in page,
      "is a dot with nothing written in it: whether there is something to "
      "read is the question from across a room, and how many is answered by "
      "opening it")
badge = app.split("function paintChatBadge")[1].split("\n}")[0]
check("aria-label" in badge and "new message" in badge,
      "and the count goes on the button's label, because a dot is nothing at "
      "all to a screen reader")
check("hidden = chatUnread === 0" in badge,
      "shown exactly when something is unread")
heard = app.split("function heardChat")[1].split("\nfunction ")[0]
check("showNotice" not in heard,
      "and a message arriving does not put a banner over the picture: "
      "somebody is watching a game, and a box over it is an interruption "
      "whether or not the message was urgent")
check("pulseChatDot()" in heard,
      "the dot says one just came, instead")
css = open(os.path.join(ROOT, "web", "style.css")).read()
check("chat-beat" in css and "prefers-reduced-motion: no-preference" in css,
      "with an animation, and none at all for somebody who asked for none")

print("the room can reach it with Kodi closed, which is when it matters")
overlay_src = open(os.path.join(ROOT, "fourthplayer", "overlay.py")).read()
check("from .chatkey import ChatKey" in overlay_src,
      "the overlay watches the keyboard itself rather than waiting for Kodi "
      "to pass a keypress on -- kodi-retrobox closes Kodi to run a game, so "
      "a Kodi shortcut is one that works everywhere except while playing")
compose = overlay_src.split("def open_composer")[1].split("\n    def ")[0]
check("grab_keyboard" in compose,
      "opening the composer takes the keyboard")
grab = overlay_src.split("def grab_keyboard")[1].split("\n    def ")[0]
check("udev" in grab and "X grab" in grab,
      "at the kernel and not through X, and why is written down: RetroArch's "
      "input driver here is udev, so it reads the devices directly and an X "
      "grab is invisible to it -- `h` is its reset key, and a message with an "
      "h in it restarted somebody's game")
chatkey_src = open(os.path.join(ROOT, "fourthplayer", "chatkey.py")).read()
check("device.grab()" in chatkey_src and "EVIOCGRAB" in chatkey_src,
      "which means an exclusive grab on the devices themselves")
check("dies with it" in chatkey_src,
      "given back by the kernel if this process dies, which is the one "
      "failure nobody could undo from a controller")
key_handler = overlay_src.split("def take_typing")[1].split("\n    def ")[0]
check("KEY_ESC" in key_handler and "close_composer" in key_handler,
      "escape lets go of it")
check("translate_keyboard_state" in key_handler,
      "and the letters come from the layout somebody chose rather than a "
      "table that would have been right for one keyboard")
check("COMPOSE_IDLE" in overlay_src,
      "and so does time: nothing that holds a keyboard may hold it for ever, "
      "and the person it would happen to is playing a game")
check("self.grabbed" in overlay_src.split("def draw_composer")[1][:600],
      "a grab that failed is said on the card rather than left to be "
      "discovered when a letter loads a save state")

print("the room can see it and answer it")
overlay = open(os.path.join(ROOT, "fourthplayer", "overlay.py")).read()
check("def draw_chat" in overlay,
      "the television shows what was said, rather than the guests talking "
      "about somebody who cannot hear them")
check("REPLY_KEY" in overlay, "and says which keys open it")

print("and the Kodi half is gone, because two of them fought")
addon = open(os.path.join(ROOT, "addons", "script.fourthplayer", "main.py")).read()
panels = open(os.path.join(ROOT, "addons", "script.fourthplayer",
                           "panels.py")).read()
client = open(os.path.join(ROOT, "addons", "script.fourthplayer",
                           "fpclient.py")).read()
check("chat" not in addon and "show_chat" not in panels and "chat" not in client,
      "no chat window in the add-on: the overlay grabs those keys at the "
      "kernel, so a Kodi keymap on the same combination opened both at once")
check(not os.path.exists(os.path.join(ROOT, "system",
                                      "fourth-player-keymap.xml")),
      "and no keymap ships any more")
install = open(os.path.join(ROOT, "install", "install.sh")).read()
check("keymaps" not in install,
      "nor is one installed -- there is nothing left for it to open")

print("the composer stays until somebody closes it")
compose_keys = overlay.split("def take_typing")[1].split("\n    def ")[0]
check("self.typing = \"\"" in compose_keys and "close_composer" not in
      compose_keys.split("KEY_ENTER")[1].split("continue")[0],
      "Enter sends and stays: a conversation is not one message, and closing "
      "on send means pressing the shortcut again for every line")
watch = overlay.split("def watch_keys")[1].split("\n    def ")[0]
# Just the idle branch: the exception handler below it closes the composer on
# purpose, and reading to the end of the function swept that up too.
idle = watch.split("COMPOSE_IDLE")[1].split("except Exception")[0]
check("release_keyboard()" in idle and "close_composer()" not in idle,
      "and a long silence hands the keyboard back without closing the window: "
      "somebody reading is not somebody who has finished")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("all good")
