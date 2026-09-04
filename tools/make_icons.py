#!/usr/bin/env python3
"""Cut the Fourth Player icon down to the sizes that get used.

One drawing, media/icon.png, and everything else is made from it: the home
screen icon on a phone, the little one in a browser tab, the tile on the Kodi
menu. Kept as a script rather than a folder of files somebody has to remember
to redo, because the day the drawing changes is the day five stale copies of
the old one start disagreeing with it.

The maskable one is padded on purpose. Android crops a maskable icon to
whatever shape the launcher likes, and it only promises to keep the middle
80%; this drawing is a circle that nearly fills its square, so cropping it
uncropped would shave the ring off. Padding it to about three-quarters puts
the whole circle inside the part that survives.
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


def main():
    if not os.path.exists(MASTER):
        sys.exit("no master drawing at %s" % MASTER)
    master = Image.open(MASTER).convert("RGBA")
    into = os.path.join(ROOT, "web", "icons")
    os.makedirs(into, exist_ok=True)

    for size in sorted(PLAIN):
        write(master.resize((size, size), Image.LANCZOS),
              os.path.join(into, "icon-%d.png" % size))

    inner = int(MASKABLE * SAFE)
    padded = Image.new("RGBA", (MASKABLE, MASKABLE), BACKGROUND)
    art = master.resize((inner, inner), Image.LANCZOS)
    padded.alpha_composite(art, ((MASKABLE - inner) // 2,) * 2)
    write(padded, os.path.join(into, "icon-maskable-%d.png" % MASKABLE))

    # The Kodi menu tile, which is 256 like every other tile on that menu.
    write(master.resize((256, 256), Image.LANCZOS),
          os.path.join(ROOT, "media", "menu-tile.png"))

    # And one with the corners cut away, for the join page.
    #
    # The drawing is a circle on a near-black square. On a home screen that is
    # right -- the launcher wants a square and rounds it itself -- but on the
    # join page, which is a purple gradient, the square reads as a sticker
    # somebody stuck on top. Everything outside the circle becomes
    # transparent, measured off the drawing rather than assumed: the artwork
    # runs from 25 to 1228 across a 1254 square, so the circle is the middle
    # 96% of it.
    write(round_off(master), os.path.join(into, "icon-round-192.png"))


def round_off(master, size=192):
    """The drawing with everything outside its circle made transparent."""
    art = master.resize((size, size), Image.LANCZOS).convert("RGBA")
    middle = (size - 1) / 2.0
    radius = size * 0.48                  # the circle in the drawing, measured
    pixels = art.load()
    for y in range(size):
        for x in range(size):
            dx, dy = x - middle, y - middle
            away = (dx * dx + dy * dy) ** 0.5
            if away > radius:
                pixels[x, y] = (0, 0, 0, 0)
            elif away > radius - 1.5:
                # One pixel of softened edge, so the circle does not have a
                # staircase on it at this size.
                r, g, b, a = pixels[x, y]
                pixels[x, y] = (r, g, b, int(a * (radius - away) / 1.5))
    return art


if __name__ == "__main__":
    main()
