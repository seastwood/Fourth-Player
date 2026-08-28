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
import time
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango, PangoCairo  # noqa: E402
import cairo  # noqa: E402
import qrcode  # noqa: E402

from .approve import Shoulders  # noqa: E402

CONTROL_SOCKET = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "fourth-player.sock")

POLL_SECONDS = 1
# How long the full card stays up before it shrinks to the badge. Long enough
# to photograph and send, short enough not to sit on the game.
CARD_SECONDS = 120

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
        self.reposition()
        self.queue_draw()
        return True

    ASK_WIDTH, ASK_HEIGHT = 470, 156
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
        window.input_shape_combine_region(
            None if yes else cairo.Region(), 0, 0)

    def watch_pad(self):
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
        if self.pending:
            self.resize(self.ASK_WIDTH, self.ASK_HEIGHT)
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            area = monitor.get_geometry()
            # Middle of the top edge, not a corner: this one is asking for
            # something, so it is allowed to be in the way.
            self.move(area.x + (area.width - self.ASK_WIDTH) // 2, area.y + MARGIN)
            return
        width, height = (400, 300) if self.expanded else (168, 44)
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

        if self.pending:
            self.draw_ask(ctx, width, height)
        elif self.expanded and self.qr:
            self.draw_card(ctx, width, height)
        else:
            self.draw_badge(ctx, width, height)
        return False

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
            text(ctx, CARD_PAD, height - 68,
                 "Hold L + R on your controller to start it. Do nothing to refuse.",
                 11, (0.62, 0.66, 0.72))
            # The hold, drawn filling up, so it is obvious it is working before
            # it finishes rather than only after.
            bar_x, bar_y = CARD_PAD, height - 48
            bar_w, bar_h = width - CARD_PAD * 2, 6
            ctx.set_source_rgba(1, 1, 1, 0.12)
            ctx.rectangle(bar_x, bar_y, bar_w, bar_h)
            ctx.fill()
            if self.hold > 0:
                ctx.set_source_rgb(0.29, 0.84, 0.63)
                ctx.rectangle(bar_x, bar_y, bar_w * self.hold, bar_h)
                ctx.fill()
        else:
            text(ctx, CARD_PAD, height - 68,
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
        text(ctx, CARD_PAD, height - 30, self.summary(), 12, (0.62, 0.66, 0.72))

    def draw_badge(self, ctx, width, height):
        ctx.set_source_rgb(0.90, 0.61, 0.25)
        ctx.arc(18, height / 2, 5, 0, 2 * 3.14159)
        ctx.fill()
        text(ctx, 32, height / 2 - 9, self.summary(), 12, (0.89, 0.91, 0.95))

    def summary(self):
        remaining = self.status.get("remaining", 0)
        guests = len(self.status.get("guests") or [])
        slots = self.status.get("slots", 0)
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
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
