"""The on-screen card: a QR to join by, and a light that says we are sharing.

Two jobs, and the second one matters more. The QR is convenience -- photograph
the television, send the picture. The badge it shrinks into is a tally light:
for as long as this machine can be watched and driven from outside the house,
something on the screen says so. A sharing session that can run invisibly is a
worse thing to own than one that cannot.

It draws with GTK as an override-redirect, keep-above, RGBA window, which is
the same approach kodi-retrobox's `jsm-hud` uses and records as verified over
fullscreen Kodi, Quake III and Warcraft III. That choice is load-bearing rather
than stylistic. From `ra_holdbar.py` in that repository:

    "an ordinary always-on-top window makes xfwm4 stop treating RetroArch as an
     unredirected fullscreen window, which lets the XFCE panel pop out over the
     game"

A small badge in the corner is exactly the shape that triggers that, and the
symptom -- the desktop panel appearing over a game -- looks nothing like its
cause. Override-redirect goes around the window manager entirely, so xfwm4
never reconsiders the game underneath.

Runs as its own process on purpose: a wedged overlay must not be able to take
the server, or anybody's game, down with it.
"""

import argparse
import io
import json
import os
import socket
import sys
import time
import traceback

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango, PangoCairo  # noqa: E402
import cairo  # noqa: E402
import qrcode  # noqa: E402

from .approve import Shoulders  # noqa: E402
from .chatkey import ChatKey  # noqa: E402

CONTROL_SOCKET = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "fourth-player.sock")

POLL_SECONDS = 1
# How long the full card stays up before it shrinks to the badge. Long enough
# to photograph and send, short enough not to sit on the game.
CARD_SECONDS = 120
# How long "somebody joined" stays up. Long enough to read from a sofa without
# looking for it, short enough not to sit on a game.
JOINED_SECONDS = 5.0
# How long a line of chat stays on the television. Long enough to read twice
# -- somebody is playing a game while it appears, and the first read is
# usually "something appeared" rather than the words.
CHAT_SECONDS = 9.0
# How long a composer nobody is typing into stays open. It holds the keyboard
# while it is up, so it must not be able to hold it for ever: a machine whose
# keyboard goes nowhere is worse than a message that did not get sent, and the
# person it happens to is playing a game at the time.
COMPOSE_IDLE = 45.0
# How much of the conversation the composer shows above the box.
COMPOSE_LINES = 6
# The key that opens the chat window in Kodi. It is written on the card
# because a message you cannot answer is a worse thing to be shown than no
# message: the whole point of putting it on the screen is that the person in
# the room is in the conversation rather than being talked about.
#
# Ctrl+Shift+C rather than a bare key, and that is not caution for its own
# sake: F8 was tried first and F8 is Kodi's screenshot key, so pressing it
# took a picture of the television instead of opening the chat. Kodi's own
# keymap binds nearly every unmodified letter and eight of the function keys.
# It binds nothing at all to ctrl+shift with c, j, q, u, w, y or z -- checked
# against `/usr/share/kodi/system/keymaps/keyboard.xml` rather than guessed --
# and of those, c is the one somebody will remember.
REPLY_KEY = "Ctrl+Shift+C"

MARGIN = 28
QR_PIXELS = 190
CARD_PAD = 18


def ask(request):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(3)
            sock.connect(CONTROL_SOCKET)
            sock.sendall((json.dumps(request) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        return json.loads(data or b"{}")
    except (OSError, ValueError):
        return {"ok": False, "open": False}


def wrap_two(said, per_line):
    """Two lines of at most `per_line`, breaking on a space where there is one.

    Deliberately not a full layout: this is a card on a television with room
    for two lines, and the chat window is where the rest of a long message is
    read. Ending mid-word with an ellipsis says "there is more" better than a
    line that stops neatly and looks complete.
    """
    said = " ".join(str(said).split())
    if len(said) <= per_line:
        return said, ""
    cut = said.rfind(" ", 0, per_line + 1)
    first = said[:cut] if cut > per_line // 2 else said[:per_line]
    rest = said[len(first):].strip()
    if len(rest) <= per_line:
        return first, rest
    cut = rest.rfind(" ", 0, per_line - 1)
    second = (rest[:cut] if cut > per_line // 2 else rest[:per_line - 1])
    return first, second.rstrip() + "\u2026"


def qr_surface(text, size=QR_PIXELS):
    code = qrcode.QRCode(border=2, box_size=6,
                         error_correction=qrcode.constants.ERROR_CORRECT_M)
    code.add_data(text)
    code.make(fit=True)
    image = code.make_image(fill_color="black", back_color="white").convert("RGB")
    image = image.resize((size, size), 0)          # nearest: keep the edges hard
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
    loader.write(buffer.getvalue())
    loader.close()
    return loader.get_pixbuf()


class Overlay(Gtk.Window):
    def __init__(self, corner="top-right", card_seconds=CARD_SECONDS):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.corner = corner
        self.card_seconds = card_seconds
        self.status = {}
        self.qr = None
        self.qr_for = None
        self.elapsed = 0
        self.expanded = True
        self.pending = None
        # Who is already here, so an arrival can be noticed. Seeded on the
        # first poll rather than empty, or every guest already playing would be
        # announced the moment this window opens.
        self.known = None
        self.joined = None           # (name, until) while somebody is new
        self.chat = None             # (from, text, until) while one is fresh
        # The composer: None when closed, otherwise what has been typed so
        # far. Open, this window stops being a card and becomes the one place
        # in the room where the conversation can be read and answered --
        # including while a game is running, which is the whole reason it is
        # here rather than in Kodi. Kodi is closed when a game is playing.
        self.typing = None
        self.typed_at = 0.0
        self.grabbed = False
        self.chatkey = ChatKey()
        self.chat_seen = None        # the newest id shown, None until first poll
        self.shoulders = Shoulders()
        self.hold = 0.0
        # A hold that was already under way when the request arrived does not
        # count. Otherwise a game that wants both bumpers approves whatever is
        # asked for the instant it is asked.
        self.armed = False

        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("button-press-event", self.on_click)
        # No key handler: while the composer is open the keyboards are held at
        # the kernel, so X delivers nothing to anybody -- including this
        # window. The typing is read from the devices themselves.
        self.connect("draw", self.on_draw)
        self.connect("realize", self.on_realize)
        self.connect("destroy", Gtk.main_quit)

    def on_realize(self, _widget):
        # Override-redirect: the window manager never sees this window, so it
        # cannot decide the fullscreen game below is no longer fullscreen.
        self.get_window().set_override_redirect(True)
        region = cairo.Region()
        self.get_window().input_shape_combine_region(region, 0, 0)   # click-through

    # -- state --------------------------------------------------------------

    def poll(self):
        """Wrapped, because returning nothing from a GLib timeout removes it.

        An exception here does not print anywhere -- the parent starts this
        with its output discarded -- and it does not stop the process either.
        It removes the timer, so the window carries on being drawn, frozen, at
        whatever it last was. That is the worst shape a failure can take: it
        looks exactly like a feature that was never wired up.
        """
        try:
            return self._poll()
        except Exception:
            print("overlay: poll failed", file=sys.stderr)
            traceback.print_exc()
            return True                        # keep the timer alive regardless

    def _poll(self):
        status = ask({"cmd": "status"})
        if not status.get("open"):
            Gtk.main_quit()
            return False
        self.status = status
        self.elapsed += POLL_SECONDS

        url = status.get("url")
        if url and url != self.qr_for:
            self.qr = qr_surface(url)
            self.qr_for = url

        # A request to start a game outranks the join card: it is the one
        # thing here with a deadline, and the owner may be mid-game with no
        # other window in front of them.
        # Somebody arriving is worth saying on the television: the host is
        # usually looking at a game, not at a roster, and a controller coming
        # to life with no explanation is how a guest gets blamed for something
        # the cat did.
        here = {(g.get("slot"), g.get("label"))
                for g in (status.get("guests") or [])}
        if self.known is None:
            self.known = here
        elif here - self.known:
            newest = sorted(here - self.known)[-1]
            self.joined = (newest[1] or "A guest",
                           time.monotonic() + JOINED_SECONDS)
        self.known = here

        # Anything said since the last poll. Only the newest is shown -- two
        # cards cannot both be on a television at once, and the newest is the
        # one somebody is waiting on an answer to.
        lines = status.get("chat") or []
        newest = lines[-1] if lines else None
        if self.chat_seen is None:
            # Whatever was said before this overlay started is history, not
            # news: a card for it would be shown to somebody who has already
            # had the conversation.
            self.chat_seen = status.get("chat_last") or 0
        elif newest and newest.get("id", 0) > self.chat_seen:
            self.chat_seen = newest["id"]
            self.chat = (newest.get("from") or "Guest", newest.get("text") or "",
                         time.monotonic() + CHAT_SECONDS)

        was = self.pending
        self.pending = (status.get("launch") or {}).get("pending")
        if self.pending and not was:
            self.shoulders.forget()
            # A controller that slept since the last request is on a different
            # node now, so look again rather than watch one that has gone.
            self.shoulders.rescan()
            self.armed = False
            self.hold = 0.0
        if bool(self.pending) != bool(was):
            self.set_clickable(bool(self.pending))

        full = status.get("guests") and len(status["guests"]) >= status.get("slots", 3)
        if self.expanded and (self.elapsed > self.card_seconds or full):
            self.expanded = False
        if self.joined and time.monotonic() >= self.joined[1]:
            self.joined = None
        if self.chat and time.monotonic() >= self.chat[2]:
            self.chat = None
        self.reposition()
        self.queue_draw()
        return True

    ASK_WIDTH, ASK_HEIGHT = 470, 186
    BUTTON_W, BUTTON_H = 104, 28

    def ask_buttons(self, width, height):
        """Where the two buttons are.

        One function so that what is drawn and what is clickable cannot drift
        apart -- a button that is painted somewhere it cannot be pressed is
        worse than no button.
        """
        y = height - self.BUTTON_H - 12
        right = width - CARD_PAD - self.BUTTON_W
        return [
            ("approve", right - self.BUTTON_W - 8, y, self.BUTTON_W, self.BUTTON_H),
            ("deny", right, y, self.BUTTON_W, self.BUTTON_H),
        ]

    def on_click(self, _widget, event):
        if not self.pending:
            return False
        for name, x, y, w, h in self.ask_buttons(self.get_allocated_width(),
                                                 self.get_allocated_height()):
            if x <= event.x <= x + w and y <= event.y <= y + h:
                waiting, self.pending = self.pending, None
                reply = ask({"cmd": name}) if name == "approve" else ask(
                    {"cmd": "deny", "reason": "the owner said no"})
                if not reply.get("ok") and name == "approve":
                    self.pending = waiting      # let the countdown carry on
                self.set_clickable(bool(self.pending))
                self.reposition()
                self.queue_draw()
                return True
        return False

    def set_clickable(self, yes):
        """Take clicks only while something is being asked.

        The rest of the time this window is click-through, which is not a
        detail: it sits over a fullscreen game, and a window that swallows a
        click is a window that swallows a shot.
        """
        window = self.get_window()
        if window is None:
            return
        # A region covering the window, not None. The documentation says None
        # means "the whole window"; the binding says "Argument 1 does not allow
        # None as a value" and raises -- which killed the callback that was
        # asking, and with it every update this window makes.
        #
        # Sized from the prompt's own dimensions rather than from the current
        # allocation. This is called as the request arrives, when the window is
        # still the small badge and has not been resized yet, so asking what it
        # is allocated right now gives a region that covers a corner -- with
        # both buttons outside it, and every click on them landing in whatever
        # is behind.
        if yes:
            shape = cairo.Region(cairo.RectangleInt(
                0, 0, self.ASK_WIDTH, self.ASK_HEIGHT))
        else:
            shape = cairo.Region()
        window.input_shape_combine_region(shape, 0, 0)

    def watch_keys(self):
        """Twenty times a second: has somebody asked for the chat?

        Read from the keyboards rather than taken from Kodi, because when a
        game is running Kodi is not merely behind it -- kodi-retrobox closes
        Kodi to launch a game. A shortcut that needs Kodi is a shortcut that
        works everywhere except while playing, which is where it was wanted.
        """
        try:
            if self.typing is not None:
                # While it is open, every keystroke belongs to the message.
                self.take_typing()
            elif self.chatkey.pressed():
                self.open_composer()
            if (self.typing is not None
                    and time.monotonic() - self.typed_at > COMPOSE_IDLE):
                # Only Escape closes it -- somebody reading a conversation is
                # not somebody who has finished with it. But the keyboard is
                # not theirs to hold for ever while they read, so after a long
                # silence the grab is handed back and the window stays, saying
                # so. Typing is over at that point, which is the honest state:
                # the keys are going to the game again.
                self.release_keyboard()
        except Exception:
            print("overlay: reading the keyboard failed", file=sys.stderr)
            traceback.print_exc()
            self.close_composer()
        return True

    def open_composer(self):
        """Take the keyboard and show the conversation."""
        if self.typing is not None:
            return
        self.typing = ""
        self.typed_at = time.monotonic()
        self.grab_keyboard()
        self.reposition()
        self.queue_draw()

    def close_composer(self):
        self.typing = None
        self.release_keyboard()
        self.reposition()
        self.queue_draw()

    def grab_keyboard(self):
        """Hold the keyboards while somebody is typing.

        Not an X grab. RetroArch's input driver on this machine is udev: it
        reads the keyboard devices directly and never asks X, so an X grab is
        invisible to it. That was the first attempt, and what it produced was
        a message typed into a running game -- `h` is RetroArch's reset, so a
        word with an h in it restarted somebody's game.

        This takes the devices at the kernel instead, which stops every other
        reader: the game, the window manager, and Kodi if it is up.
        """
        self.grabbed = self.chatkey.hold()

    def release_keyboard(self):
        self.chatkey.let_go()
        self.grabbed = False

    def take_typing(self):
        """Turn key presses into the line being written.

        The codes are raw, because what a key means depends on the layout
        somebody chose. GDK knows that -- it is the same table X uses to
        decide what the keyboard does -- so the translation is asked for
        rather than guessed at with a table of my own, which would have been
        right for one keyboard and wrong for anybody else's.
        """
        keymap = Gdk.Keymap.get_for_display(Gdk.Display.get_default())
        for code, value in self.chatkey.typed():
            if not value:                       # a release
                continue
            if not self.grabbed:
                # It was handed back while nobody was typing. Somebody is now.
                self.grab_keyboard()
            self.typed_at = time.monotonic()
            name = self.chatkey.evdev.ecodes.KEY.get(code, "")
            if name == "KEY_ESC":
                return self.close_composer()
            if name in ("KEY_ENTER", "KEY_KPENTER"):
                # Sends and stays. A conversation is not one message: closing
                # on send meant pressing the shortcut again for every line,
                # and the answer usually arrives while somebody is still
                # looking at what they sent.
                said = self.typing.strip()
                self.typing = ""
                if said:
                    ask({"cmd": "say", "text": said})
                    # Straight back from the host, so the line appears above
                    # the box in the same order everybody else sees it.
                    self.poll()
                continue
            if name == "KEY_BACKSPACE":
                self.typing = self.typing[:-1]
                continue
            # evdev codes and X keycodes differ by the same eight everywhere,
            # which is how X has numbered them since it was reading a PS/2
            # controller.
            shifted = any(
                (path, mod) in self.chatkey.down
                for path, mod in [(d.path, m) for d in self.chatkey.devices
                                  for m in (self.chatkey.evdev.ecodes.KEY_LEFTSHIFT,
                                            self.chatkey.evdev.ecodes.KEY_RIGHTSHIFT)])
            state = Gdk.ModifierType.SHIFT_MASK if shifted else 0
            ok, keyval, _group, _level, _consumed = keymap.translate_keyboard_state(
                code + 8, state, 0)
            if not ok:
                continue
            char = chr(Gdk.keyval_to_unicode(keyval) or 0)
            if char and char.isprintable():
                self.typing = (self.typing + char)[:240]
        self.queue_draw()

    def watch_pad(self):
        try:
            return self._watch_pad()
        except Exception:
            print("overlay: reading the pads failed", file=sys.stderr)
            traceback.print_exc()
            return True

    def _watch_pad(self):
        """Twenty times a second: is the owner holding both bumpers?"""
        progress = 0.0
        try:
            progress = self.shoulders.progress(time.monotonic())
        except Exception:
            return True                       # never take the overlay down
        if not self.pending:
            self.armed = False
            return True
        if not self.armed:
            # Wait for the bumpers to actually come up, so the hold is a new
            # one. Arming on progress == 0 would arm at the first instant of a
            # hold that was already under way when the request arrived, and
            # approve it a second and a half later without anyone deciding
            # anything.
            if not self.shoulders.holding:
                self.armed = True
            return True
        if progress >= 1.0:
            waiting, self.pending = self.pending, None
            self.hold = 0.0
            self.shoulders.forget()
            self.armed = False
            reply = ask({"cmd": "approve"})
            if not reply.get("ok"):
                self.pending = waiting        # let the countdown carry on
            self.reposition()
            self.queue_draw()
            return True
        if abs(progress - self.hold) > 0.01:
            self.hold = progress
            self.queue_draw()
        return True

    def reposition(self):
        if self.typing is not None:
            self.resize(560, 260)
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            area = monitor.get_geometry()
            self.move(area.x + area.width - 560 - MARGIN, area.y + MARGIN)
            return
        if self.chat and not self.pending:
            self.resize(430, 132)
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            area = monitor.get_geometry()
            self.move(area.x + area.width - 430 - MARGIN, area.y + MARGIN)
            return
        if self.joined and not self.pending:
            self.resize(330, 96)
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            area = monitor.get_geometry()
            self.move(area.x + area.width - 330 - MARGIN, area.y + MARGIN)
            return
        if self.pending:
            self.resize(self.ASK_WIDTH, self.ASK_HEIGHT)
            self.set_clickable(True)           # again, now it is this size
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            area = monitor.get_geometry()
            # Middle of the top edge, not a corner: this one is asking for
            # something, so it is allowed to be in the way.
            self.move(area.x + (area.width - self.ASK_WIDTH) // 2, area.y + MARGIN)
            return
        width, height = (400, 316) if self.expanded else (168, 44)
        if not self.expanded or not self.status.get("url"):
            width, height = (168, 44) if not self.expanded else (400, 132)
        self.resize(width, height)
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        area = monitor.get_geometry()
        x = area.x + area.width - width - MARGIN if "right" in self.corner else area.x + MARGIN
        y = area.y + MARGIN if "top" in self.corner else area.y + area.height - height - MARGIN
        self.move(x, y)

    # -- drawing ------------------------------------------------------------

    def on_draw(self, _widget, ctx):
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        ctx.set_operator(cairo.OPERATOR_SOURCE)
        ctx.set_source_rgba(0, 0, 0, 0)
        ctx.paint()
        ctx.set_operator(cairo.OPERATOR_OVER)

        rounded(ctx, 0, 0, width, height, 12)
        ctx.set_source_rgba(0.055, 0.07, 0.10, 0.92)
        ctx.fill_preserve()
        ctx.set_source_rgba(0.90, 0.61, 0.25, 0.85)
        ctx.set_line_width(1.5)
        ctx.stroke()

        if self.typing is not None:
            self.draw_composer(ctx, width, height)
        elif self.pending:
            self.draw_ask(ctx, width, height)
        elif self.chat:
            self.draw_chat(ctx, width, height)
        elif self.joined:
            self.draw_joined(ctx, width, height)
        elif self.expanded and self.qr:
            self.draw_card(ctx, width, height)
        else:
            self.draw_badge(ctx, width, height)
        return False

    def draw_composer(self, ctx, width, height):
        """The conversation, and the line being typed into it."""
        text(ctx, CARD_PAD, CARD_PAD + 2, "CHAT", 11, (0.90, 0.61, 0.25),
             bold=True)
        if not self.grabbed:
            # Said rather than hidden: without the grab every letter also
            # reaches the game, and finding that out by watching a save state
            # load is a poor way to learn it.
            text(ctx, CARD_PAD + 60, CARD_PAD + 2,
                 "keyboard is held by the game -- typing may reach it", 10,
                 (0.90, 0.45, 0.35))
        lines = (self.status.get("chat") or [])[-COMPOSE_LINES:]
        y = CARD_PAD + 26
        for message in lines:
            who = (message.get("from") or "?")[:14]
            text(ctx, CARD_PAD, y, who, 11, (0.90, 0.61, 0.25), bold=True)
            said, _rest = wrap_two(message.get("text") or "", 44)
            text(ctx, CARD_PAD + 110, y, said, 13, (0.89, 0.91, 0.95))
            y += 22
        if not lines:
            text(ctx, CARD_PAD, y, "Nothing said yet.", 13, (0.62, 0.66, 0.72))
            y += 22

        box = height - CARD_PAD - 34
        rounded(ctx, CARD_PAD, box, width - CARD_PAD * 2, 30, 6)
        ctx.set_source_rgba(0.09, 0.11, 0.16, 0.95)
        ctx.fill_preserve()
        ctx.set_source_rgba(0.90, 0.61, 0.25, 0.7)
        ctx.set_line_width(1.2)
        ctx.stroke()
        # The tail of what has been typed, so a long message keeps its cursor
        # in view rather than scrolling off the right of the box.
        showing = self.typing[-52:]
        text(ctx, CARD_PAD + 10, box + 8, (showing or "Say something") + "_",
             13, (0.89, 0.91, 0.95) if self.typing else (0.45, 0.49, 0.56))
        if self.grabbed:
            note, colour = "Enter sends  ·  Escape closes", (0.62, 0.66, 0.72)
        else:
            # Either the grab never took, or it was given back after a long
            # silence. Both mean the same thing to somebody about to type, so
            # both say it: the game can hear this.
            note = "the game has the keyboard  ·  Escape closes  ·  press a key to take it back"
            colour = (0.90, 0.45, 0.35)
        text(ctx, CARD_PAD, height - 14, note, 10, colour)

    def draw_chat(self, ctx, width, height):
        who, said, _until = self.chat
        text(ctx, CARD_PAD, CARD_PAD + 2, who[:22].upper(), 11,
             (0.90, 0.61, 0.25), bold=True)
        # Two lines of it, cut where it stops fitting rather than at a word
        # count, and the rest left in the chat window where it can be read
        # properly. A card that grows with what somebody typed is a card that
        # covers a game.
        first, second = wrap_two(said, 34)
        text(ctx, CARD_PAD, CARD_PAD + 26, first, 17, (0.89, 0.91, 0.95))
        if second:
            text(ctx, CARD_PAD, CARD_PAD + 50, second, 17, (0.89, 0.91, 0.95))
        text(ctx, CARD_PAD, height - CARD_PAD - 12,
             "%s to reply" % REPLY_KEY, 12, (0.62, 0.66, 0.72))

    def draw_joined(self, ctx, width, height):
        name, _until = self.joined
        text(ctx, CARD_PAD, CARD_PAD + 2, "JOINED", 11,
             (0.29, 0.84, 0.63), bold=True)
        text(ctx, CARD_PAD, CARD_PAD + 24, name[:28], 18,
             (0.89, 0.91, 0.95), bold=True)
        guests = len(self.status.get("guests") or [])
        slots = self.status.get("slots", 0)
        text(ctx, CARD_PAD, CARD_PAD + 54, "%d of %d playing" % (guests, slots),
             12, (0.62, 0.66, 0.72))

    def draw_ask(self, ctx, width, height):
        """Who wants to start what, and how long is left to answer.

        No buttons, because this window is click-through by design and a
        controller cannot reach it: it says what to do, and the doing happens
        in Kodi or at a terminal.
        """
        ask = self.pending or {}
        seconds = int(ask.get("seconds") or 0)
        text(ctx, CARD_PAD, CARD_PAD, "START A GAME?", 12,
             (0.90, 0.61, 0.25), bold=True)
        text(ctx, width - CARD_PAD - 46, CARD_PAD, "%2ds" % seconds, 14,
             (1.0, 0.36, 0.48) if seconds <= 10 else (0.89, 0.91, 0.95), bold=True)
        text(ctx, CARD_PAD, CARD_PAD + 26,
             "%s wants to play" % (ask.get("who") or "someone"), 12,
             (0.62, 0.66, 0.72))
        text(ctx, CARD_PAD, CARD_PAD + 46, (ask.get("label") or "")[:46], 15,
             (0.89, 0.91, 0.95), bold=True)
        # Which kind of start, because the two do different things to a save:
        # continuing loads it and will write over it on the way out.
        text(ctx, CARD_PAD, CARD_PAD + 68, ask.get("how") or "", 11,
             (0.90, 0.61, 0.25))
        for name, x, y, w, h in self.ask_buttons(width, height):
            allow = name == "approve"
            ctx.set_source_rgba(0.29, 0.84, 0.63, 0.18) if allow else \
                ctx.set_source_rgba(1, 1, 1, 0.08)
            ctx.rectangle(x, y, w, h)
            ctx.fill_preserve()
            ctx.set_source_rgb(*((0.29, 0.84, 0.63) if allow else (0.62, 0.66, 0.72)))
            ctx.set_line_width(1.5)
            ctx.stroke()
            label = "START IT" if allow else "NO"
            # Centred by measuring, not by guessing at the string width.
            layout = PangoCairo.create_layout(ctx)
            layout.set_font_description(Pango.FontDescription("Sans Bold 11"))
            layout.set_text(label, -1)
            tw, th = layout.get_pixel_size()
            ctx.move_to(x + (w - tw) / 2, y + (h - th) / 2)
            PangoCairo.show_layout(ctx, layout)

        if self.shoulders.ok:
            text(ctx, CARD_PAD, height - 78,
                 "Hold both shoulders \u2014 bumpers or triggers \u2014 to start it.",
                 11, (0.62, 0.66, 0.72))
            # The hold, drawn filling up, so it is obvious it is working before
            # it finishes rather than only after.
            bar_x, bar_y = CARD_PAD, height - 52
            bar_w, bar_h = width - CARD_PAD * 2, 6
            ctx.set_source_rgba(1, 1, 1, 0.12)
            ctx.rectangle(bar_x, bar_y, bar_w, bar_h)
            ctx.fill()
            if self.hold > 0:
                ctx.set_source_rgb(0.29, 0.84, 0.63)
                ctx.rectangle(bar_x, bar_y, bar_w * self.hold, bar_h)
                ctx.fill()
        else:
            text(ctx, CARD_PAD, height - 78,
                 "Fourth Player in Kodi \u2192 Approve, or: fourthplayer approve",
                 11, (0.62, 0.66, 0.72))

    def draw_card(self, ctx, width, height):
        Gdk.cairo_set_source_pixbuf(ctx, self.qr, CARD_PAD, CARD_PAD)
        ctx.paint()
        left = CARD_PAD * 2 + QR_PIXELS
        text(ctx, left, CARD_PAD + 2, "JOIN THIS GAME", 11, (0.90, 0.61, 0.25), bold=True)
        text(ctx, left, CARD_PAD + 26, "Scan, or open", 12, (0.62, 0.66, 0.72))
        text(ctx, left, CARD_PAD + 44, short_url(self.status.get("url", "")), 12,
             (0.89, 0.91, 0.95))
        text(ctx, left, CARD_PAD + 78, "PIN", 11, (0.62, 0.66, 0.72), bold=True)
        text(ctx, left, CARD_PAD + 96, self.status.get("pin") or "--", 26,
             (0.89, 0.91, 0.95), bold=True)
        text(ctx, left, CARD_PAD + 136, "%s to chat" % REPLY_KEY, 11,
             (0.62, 0.66, 0.72))
        text(ctx, CARD_PAD, height - 30, self.summary(), 12, (0.62, 0.66, 0.72))

    def draw_badge(self, ctx, width, height):
        ctx.set_source_rgb(0.90, 0.61, 0.25)
        ctx.arc(18, height / 2, 5, 0, 2 * 3.14159)
        ctx.fill()
        text(ctx, 32, height / 2 - 9, self.summary(), 12, (0.89, 0.91, 0.95))

    def summary(self):
        remaining = self.status.get("remaining")
        guests = len(self.status.get("guests") or [])
        slots = self.status.get("slots", 0)
        if remaining is None:                  # a session with no deadline
            return f"Sharing · {guests}/{slots} · no time limit"
        return f"Sharing · {guests}/{slots} · {remaining // 60}:{remaining % 60:02d} left"


def short_url(url):
    return url.split("/j/")[0].replace("https://", "") + "/j/…" if "/j/" in url else url


def text(ctx, x, y, string, size, colour, bold=False):
    layout = PangoCairo.create_layout(ctx)
    description = Pango.FontDescription("Sans %s%d" % ("Bold " if bold else "", size))
    layout.set_font_description(description)
    layout.set_text(string, -1)
    ctx.set_source_rgb(*colour)
    ctx.move_to(x, y)
    PangoCairo.show_layout(ctx, layout)


def rounded(ctx, x, y, width, height, radius):
    from math import pi
    ctx.new_sub_path()
    ctx.arc(x + width - radius, y + radius, radius, -pi / 2, 0)
    ctx.arc(x + width - radius, y + height - radius, radius, 0, pi / 2)
    ctx.arc(x + radius, y + height - radius, radius, pi / 2, pi)
    ctx.arc(x + radius, y + radius, radius, pi, 3 * pi / 2)
    ctx.close_path()


def main(argv=None):
    parser = argparse.ArgumentParser(description="the fourth-player on-screen card")
    parser.add_argument("--corner", default="top-right",
                        choices=["top-right", "top-left", "bottom-right", "bottom-left"])
    parser.add_argument("--card-seconds", type=float, default=CARD_SECONDS)
    parser.add_argument("--once", action="store_true",
                        help="draw one frame and exit (for checking it renders)")
    args = parser.parse_args(argv)

    overlay = Overlay(args.corner, args.card_seconds)
    if not overlay.poll():
        print("no session is open", file=sys.stderr)
        return 1
    overlay.show_all()
    if args.once:
        GLib.timeout_add_seconds(2, Gtk.main_quit)
    else:
        GLib.timeout_add_seconds(POLL_SECONDS, overlay.poll)
        GLib.timeout_add(50, overlay.watch_pad)
        # The same rate as the pads, and for the same reason: this is a
        # keystroke somebody made, and a fifth of a second between making it
        # and the window appearing feels like the machine hesitating.
        GLib.timeout_add(50, overlay.watch_keys)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
