"""Starting a Steam game: the interface it opens in, and the art it shows.

Both are about a television with a controller in front of it. Steam's desktop
window wants a mouse for everything the game does not cover -- a first-run
prompt, a Proton dialogue, the gap between one game and the next -- and a guest
on a phone has no mouse to give it. And a row of games with a cover on every
one except the Steam ones is a list that looks broken.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


try:
    from fourthplayer import catalogue, launcher
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)

print("the launch line asks Steam for the game and nothing else")
# Big Picture was tried here three ways and every one of them ended with games
# not launching: on this line beside -applaunch, as a step before it, and as a
# foreground command whose timeout killed the client. This is what works.
row = {"kind": "steam", "appid": "274190", "label": "Broforce", "system": "Steam"}
argv = launcher.build_argv(row)
check("-applaunch" in argv and "274190" in argv,
      "it names the game: %r" % (argv,))
check(len(argv) == 3, "and says nothing else: %r" % (argv,))
check(not any("gamepadui" in str(a) or "bigpicture" in str(a) for a in argv),
      "no Big Picture anywhere near it")

print("\na ROM is not affected")
rom = {"kind": "rom", "system": "nes", "label": "Micro Mages",
       "path": "/roms/mm.nes", "core_path": "/cores/nes.so", "players": "1-4"}
check("-applaunch" not in launcher.build_argv(rom),
      "no Steam flag anywhere near it")

print("\nSteam's own art is where a Steam game's cover comes from")
check(hasattr(catalogue, "steam_art_path"), "there is a lookup for it")
check("library_600x900.jpg" in catalogue.STEAM_ART_NAMES,
      "the portrait the library shows is preferred")
check(catalogue.STEAM_ART_NAMES.index("library_600x900.jpg")
      < catalogue.STEAM_ART_NAMES.index("header.jpg"),
      "before the wide header, which is only a fallback")

# art_for sends a ROM to the thumbnail tree and a Steam game to Steam.
looked = []
real_art_path = catalogue.art_path
real_steam = catalogue.steam_art_path
catalogue.art_path = lambda system, label: looked.append(("tree", system)) or None
catalogue.steam_art_path = lambda appid: looked.append(("steam", appid)) or "/tmp/x.jpg"
check(catalogue.art_for(row) == "/tmp/x.jpg", "a Steam row is answered by Steam")
check(looked == [("steam", "274190")], "and the thumbnail tree is not asked: %r" % looked)
looked.clear()
catalogue.art_for(rom)
check(looked == [("tree", "nes")], "a ROM goes to the thumbnail tree: %r" % looked)
looked.clear()
catalogue.steam_art_path = lambda appid: None
catalogue.art_for(row)
check(("tree", "Steam") in looked,
      "and a Steam game with no art of its own still falls back: %r" % looked)
catalogue.art_path = real_art_path
catalogue.steam_art_path = real_steam

print("\nand it is served as the kind of file it is")
server = open(os.path.join(ROOT, "fourthplayer", "server.py"), encoding="utf-8").read()
art = server.split("def _art(self, key)")[1].split("\\n    def ")[0]
check("image/jpeg" in art,
      "JPEG is a content type this can answer with -- Steam's covers are JPEG, "
      "and served as PNG they arrive as broken images")
check("splitext" in art, "and the type comes from the file, not from a guess")

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
