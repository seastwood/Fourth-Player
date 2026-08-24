#!/usr/bin/env python3
"""Put Fourth Player on the Kodi home menu, next to the other tools.

Kodi finds the add-on on its own -- it appears under Program add-ons as soon as
Kodi restarts. What it does not do is put it on a *custom* home menu, and a
skin-shortcuts menu like kodi-retrobox's is exactly that: a list somebody built
by hand, which nothing new joins by itself.

This edits that list as text rather than through an XML parser, because
rewriting the file with ElementTree reformats all twenty-odd entries somebody
arranged, and a diff of the whole menu is a poor way to add one line to it.

Safe to run twice: it looks for the entry before adding it.

    install/add-kodi-menu.py            add it
    install/add-kodi-menu.py --remove   take it out again
"""

import argparse
import os
import shutil
import sys
import time

MENU = os.path.expanduser(
    "~/.kodi/userdata/addon_data/script.skinshortcuts/mainmenu.DATA.xml")

ENTRY = """	<shortcut>
		<defaultID>fourthplayer</defaultID>
		<label>FOURTH PLAYER</label>
		<label2>PLAY WITH FRIENDS</label2>
		<icon>{icon}</icon>
		<thumb />
		<action>RunScript(script.fourthplayer)</action>
	</shortcut>
"""

ICONS = [
    os.path.expanduser("~/.kodi/media/consoles/_multiplayer.png"),
    "special://skin/extras/icons/DefaultAddonProgram.png",
]

# Sit with the other tools rather than among the consoles.
AFTER = ("RunScript(script.usbip)", "RunScript(script.bluetooth)",
         "RunScript(script.joyshock)")


def icon():
    for candidate in ICONS:
        if candidate.startswith("special://") or os.path.exists(candidate):
            return candidate
    return ICONS[-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--menu", default=MENU)
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()

    if not os.path.exists(args.menu):
        print(f"no skin-shortcuts menu at {args.menu}", file=sys.stderr)
        print("Nothing to do -- the add-on still appears under Program add-ons.",
              file=sys.stderr)
        return 1

    with open(args.menu) as handle:
        text = handle.read()

    if args.remove:
        if "script.fourthplayer" not in text:
            print("not on the menu; nothing to remove")
            return 0
        start = text.rindex("<shortcut>", 0, text.index("script.fourthplayer"))
        end = text.index("</shortcut>", start) + len("</shortcut>\n")
        new = text[:start].rstrip("\t") + text[end:]
    else:
        if "script.fourthplayer" in text:
            print("already on the menu")
            return 0
        entry = ENTRY.format(icon=icon())
        anchor = None
        for action in AFTER:
            if action in text:
                anchor = text.index("</shortcut>", text.index(action)) + len("</shortcut>\n")
                break
        if anchor is None:
            anchor = text.rindex("</shortcuts>")
        new = text[:anchor] + entry + text[anchor:]

    backup = f"{args.menu}.before-fourth-player.{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(args.menu, backup)
    with open(args.menu, "w") as handle:
        handle.write(new)

    print(f"menu updated (previous kept at {os.path.basename(backup)})")
    print("Kodi caches the menu -- restart it, or reload the skin, to see it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
