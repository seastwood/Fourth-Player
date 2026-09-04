#!/usr/bin/env python3
"""Cut the Fourth Player icon down to the sizes that get used.

One drawing, media/icon.png, and everything else is made from it: the home
screen icon on a phone, the little one in a browser tab, the tile on the Kodi
menu. Kept as a script rather than a folder of files somebody has to remember
to redo, because the day the drawing changes is the day five stale copies of
the old one start disagreeing with it.

Everything is cut to the circle: the drawing is a circle on a near-black
square, and left square it reads as a sticker -- a black tile on the Kodi
menu, a black box on the join page's gradient.

The maskable one is the exception, and has to be. A launcher crops it to
whatever shape it likes and paints nothing behind it, so transparency there is
a hole rather than a rounded corner; it is also padded, because only the
middle 80% is promised and this circle nearly fills its square.
"""
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
MASTER = os.path.join(ROOT, "media", "icon.png")

# Where each one is used, so a size that stops being referenced is obvious.
PLAIN = {
    32: "the browser tab",
    180: "the home screen on iOS, which asks for exactly this",
    192: "the home screen on Android",
    512: "the splash and the install prompt",
}
MASKABLE = 512
# The colour in the drawing's own corners, so padding is invisible.
BACKGROUND = (0, 1, 10, 255)
SAFE = 0.76


def write(image, path):
    image.save(path, optimize=True)
    print("   %-28s %s" % (os.path.basename(path), image.size))


def round_off(master, size):
    """The drawing at `size`, with everything outside its circle cut away.

    The drawing is a circle on a near-black square. Left square it reads as a
    sticker: a black tile on the Kodi menu, a black box on the join page's
    gradient. Everything outside the circle becomes transparent, so what is
    left is the circle.

    The radius is measured off the drawing rather than assumed -- the artwork
    runs from 25 to 1228 across a 1254 square, which is the middle 96%, so
    0.48 of the width is its edge. The last pixel and a half is faded rather
    than cut, or the circle has a staircase on it at small sizes.
    """
    art = master.resize((size, size), Image.LANCZOS).convert("RGBA")
    middle = (size - 1) / 2.0
    radius = size * 0.48
    soft = max(1.0, size / 128.0)
    pixels = art.load()
    for y in range(size):
        for x in range(size):
            dx, dy = x - middle, y - middle
            away = (dx * dx + dy * dy) ** 0.5
            if away > radius:
                pixels[x, y] = (0, 0, 0, 0)
            elif away > radius - soft:
                r, g, b, a = pixels[x, y]
                pixels[x, y] = (r, g, b, int(a * (radius - away) / soft))
    return art


def main():
    if not os.path.exists(MASTER):
        sys.exit("no master drawing at %s" % MASTER)
    master = Image.open(MASTER).convert("RGBA")
    into = os.path.join(ROOT, "web", "icons")
    os.makedirs(into, exist_ok=True)

    for size in sorted(PLAIN):
        write(round_off(master, size),
              os.path.join(into, "icon-%d.png" % size))

    # The one that stays a filled square, and has to.
    #
    # A launcher crops a maskable icon to whatever shape it likes and paints
    # nothing behind it, so transparency here is a hole rather than a rounded
    # corner. It is padded as well: only the middle 80% is promised, and this
    # circle nearly fills its square, so uncropped it would lose its ring.
    inner = int(MASKABLE * SAFE)
    padded = Image.new("RGBA", (MASKABLE, MASKABLE), BACKGROUND)
    art = master.resize((inner, inner), Image.LANCZOS)
    padded.alpha_composite(art, ((MASKABLE - inner) // 2,) * 2)
    write(padded, os.path.join(into, "icon-maskable-%d.png" % MASKABLE))

    # The Kodi menu tile, 256 like every other tile on that menu, and clear
    # around the circle like every other tile on that menu.
    write(round_off(master, 256), os.path.join(ROOT, "media", "menu-tile.png"))


if __name__ == "__main__":
    main()
