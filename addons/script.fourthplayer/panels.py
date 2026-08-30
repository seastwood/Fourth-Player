"""The two screens worth having a window for: the invite, and who is on it.

Everything else in this add-on is a list you pick from, which Kodi already does
well with a controller. These two are not lists -- one is a picture you point a
phone at, and the other changes while you watch it -- so they get a window.

Drawn with plain `WindowDialog` and controls added in code, deliberately: a
skinned `WindowXML` would look better and would also mean shipping textures and
a layout per skin, and then not working on the next skin. Labels use the
default font rather than naming one, because font names belong to skins.
"""

import os

import xbmc
import xbmcgui
import xbmcvfs


BACKDROP = (12, 15, 21)
PANEL = (23, 27, 35)
EDGE = (230, 155, 64)

CLOSE_ACTIONS = {
    9,    # ACTION_PARENT_DIR
    10,   # ACTION_PREVIOUS_MENU
    92,   # ACTION_NAV_BACK
    13,   # ACTION_STOP
}



def time_left(status):
    """How much is left, or that there is no such thing.

    A session with no deadline reports null rather than a number -- JSON has no
    infinity -- and formatting that as 0:00 would read as one about to close.
    """
    remaining = status.get("remaining")
    if remaining is None:
        return "no time limit"
    return "%d:%02d left" % (remaining // 60, remaining % 60)


def _profile():
    import xbmcaddon
    path = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo("profile"))
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
    return path


def qr_png(text, size=520):
    """A QR for this invite.

    qrcode and Pillow are imported here rather than at the top so that a
    machine without them still gets a working menu -- everything else in this
    add-on is text, and losing the picture should not cost the buttons.
    """
    import qrcode
    from PIL import Image

    code = qrcode.QRCode(border=2, box_size=10,
                         error_correction=qrcode.constants.ERROR_CORRECT_M)
    code.add_data(text)
    code.make(fit=True)
    image = code.make_image(fill_color="black", back_color="white").convert("RGB")
    # Nearest-neighbour: a QR blurred by a smooth resize is a QR that phones
    # have to work harder to read, across a room, on a television.
    image = image.resize((size, size), Image.NEAREST)
    path = os.path.join(_profile(), "invite-qr.png")
    image.save(path)
    return path


def panel_png(width, height, name):
    from PIL import Image, ImageDraw

    path = os.path.join(_profile(), name)
    image = Image.new("RGBA", (width, height), PANEL + (245,))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width - 1, height - 1], outline=EDGE + (200,), width=2)
    image.save(path)
    return path


def backdrop_png():
    from PIL import Image

    path = os.path.join(_profile(), "backdrop.png")
    Image.new("RGBA", (16, 16), BACKDROP + (235,)).save(path)
    return path


class Panel(xbmcgui.WindowDialog):
    """A window that stays up and refreshes until somebody backs out of it."""

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.closed = False

    def onAction(self, action):
        if action.getId() in CLOSE_ACTIONS:
            self.closed = True
            self.close()

    def onControl(self, control):
        self.closed = True
        self.close()


def _labels(window, entries):
    """Add labels and hand them back so a caller can keep updating them."""
    made = []
    for x, y, w, h, text, colour in entries:
        label = xbmcgui.ControlLabel(x, y, w, h, text, textColor=colour)
        window.addControl(label)
        made.append(label)
    return made


def show_invite(get_status, reshare=None):
    """The link, the PIN and the QR, with the clock running."""
    status = get_status()
    if not status.get("open"):
        return
    url, pin = status.get("url"), status.get("pin")

    # The clear link and PIN are deliberately never written to disk, so a
    # service restart -- an update, a reboot -- leaves a perfectly good session
    # whose code nobody can read back. That used to put a wall of grey on the
    # television saying to close the session and start again, which is far more
    # than it costs: a fresh pair leaves everyone already playing exactly where
    # they are, because their slots are held by their own tokens and not by the
    # invite's. So offer that instead of the sledgehammer.
    if not url and reshare is not None:
        if xbmcgui.Dialog().yesno(
                "Fourth Player",
                "The link and PIN cannot be read back after the service "
                "restarts.[CR][CR]Make a new pair now? Everyone playing keeps "
                "their place.",
                nolabel="Not now", yeslabel="New link"):
            reshare()
            status = get_status()
            if not status.get("open"):
                return
            url, pin = status.get("url"), status.get("pin")

    window = Panel()
    width, height = window.getWidth(), window.getHeight()

    window.addControl(xbmcgui.ControlImage(0, 0, width, height, backdrop_png()))

    card_w, card_h = int(width * 0.78), int(height * 0.80)
    card_x, card_y = (width - card_w) // 2, (height - card_h) // 2
    window.addControl(xbmcgui.ControlImage(
        card_x, card_y, card_w, card_h, panel_png(card_w, card_h, "card.png")))

    pad = int(card_h * 0.07)
    qr_size = card_h - pad * 2
    if url:
        try:
            window.addControl(xbmcgui.ControlImage(
                card_x + pad, card_y + pad, qr_size, qr_size, qr_png(url, qr_size)))
        except ImportError:
            _labels(window, [(card_x + pad, card_y + pad, qr_size, int(card_h * 0.1),
                              "(install python3-qrcode for a scannable code)",
                              "0xFF949CAC")])

    text_x = card_x + pad * 2 + qr_size
    text_w = card_w - (text_x - card_x) - pad
    line = int(card_h * 0.10)
    top = card_y + pad

    if not url:
        _labels(window, [
            (text_x, top, text_w, line,
             "The link and PIN cannot be read back after a restart.",
             "0xFFE3E7EE"),
            (text_x, top + line * 2, text_w, line,
             "Pick \"New link and PIN\" from the menu. Nobody loses their place.",
             "0xFF949CAC"),
        ])
    else:
        # With the link not required, the address alone plus the PIN is the
        # whole of what a guest needs -- and that address is short enough to
        # read down a telephone, which the tokenised link is not. Showing the
        # long one anyway gave no sign the setting had taken effect, and left
        # somebody dictating forty characters they did not need.
        short = (status.get("base_url") or url) if not status.get("require_link") \
            else url
        heading = ("[B]Scan this, or go to[/B]" if not status.get("require_link")
                   else "[B]Scan this, or open[/B]")
        made = _labels(window, [
            (text_x, top, text_w, line, heading, "0xFFE69B40"),
            (text_x, top + line, text_w, line,
             short.replace("https://", ""), "0xFFE3E7EE"),
            (text_x, top + int(line * 2.4), text_w, line, "[B]PIN[/B]", "0xFFE69B40"),
            (text_x, top + int(line * 3.2), text_w, int(line * 1.6),
             "[B]" + str(pin) + "[/B]", "0xFFE3E7EE"),
            (text_x, top + int(line * 5.0), text_w, line, "", "0xFF949CAC"),
            (text_x, top + int(line * 6.0), text_w, line,
             "Back to close. The code stays on screen while the session runs.",
             "0xFF949CAC"),
        ])
        clock = made[4]

    window.show()
    monitor = xbmc.Monitor()
    try:
        while not window.closed and not monitor.abortRequested():
            status = get_status()
            if not status.get("open"):
                break
            if url:
                guests = len(status.get("guests") or [])
                clock.setLabel("%d of %d playing  ·  %s"
                               % (guests, status.get("slots", 0),
                                  time_left(status)))
            if monitor.waitForAbort(1):
                break
    finally:
        window.close()
        del window


def show_monitor(get_status):
    """Who is connected, updating while you watch."""
    window = Panel()
    width, height = window.getWidth(), window.getHeight()

    window.addControl(xbmcgui.ControlImage(0, 0, width, height, backdrop_png()))
    card_w, card_h = int(width * 0.72), int(height * 0.72)
    card_x, card_y = (width - card_w) // 2, (height - card_h) // 2
    window.addControl(xbmcgui.ControlImage(
        card_x, card_y, card_w, card_h, panel_png(card_w, card_h, "monitor.png")))

    pad = int(card_h * 0.08)
    line = int(card_h * 0.09)
    heading, clock = _labels(window, [
        (card_x + pad, card_y + pad, card_w - pad * 2, line,
         "[B]Who is playing[/B]", "0xFFE69B40"),
        (card_x + pad, card_y + pad + line, card_w - pad * 2, line, "", "0xFF949CAC"),
    ])

    rows = []
    for index in range(4):
        rows.extend(_labels(window, [
            (card_x + pad, card_y + pad + int(line * (2.6 + index)),
             card_w - pad * 2, line, "", "0xFFE3E7EE"),
        ]))

    window.show()
    monitor = xbmc.Monitor()
    try:
        while not window.closed and not monitor.abortRequested():
            status = get_status()
            if not status.get("open"):
                heading.setLabel("[B]No session is open[/B]")
                clock.setLabel("")
                for row in rows:
                    row.setLabel("")
            else:
                guests = status.get("guests") or []
                clock.setLabel("%d of %d slots  ·  %s  ·  back to close"
                               % (len(guests), status.get("slots", 0),
                                  time_left(status)))
                for index, row in enumerate(rows):
                    if index < len(guests):
                        guest = guests[index]
                        state = "playing" if guest["connected"] else "away"
                        row.setLabel("%s   [COLOR FF949CAC]%s · %d inputs[/COLOR]"
                                     % (guest["label"], state, guest["frames"]))
                    elif index < status.get("slots", 0):
                        row.setLabel("[COLOR FF5C6472]Player %d — empty[/COLOR]"
                                     % (index + 2))
                    else:
                        row.setLabel("")
            if monitor.waitForAbort(2):
                break
    finally:
        window.close()
        del window
