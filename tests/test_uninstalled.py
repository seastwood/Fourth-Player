"""A game uninstalled from Steam leaves the browser.

The catalogue rebuilds when its fingerprint changes, and the fingerprint
watched the owner's chosen list and the ROM playlists -- not Steam's own
library. So a game uninstalled from Steam stayed in the browser until something
else happened to change the fingerprint, and offering a guest a game that is
not there any more is a button that can only ever fail.
"""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


try:
    from fourthplayer import catalogue, steamgames
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)

folder = tempfile.mkdtemp(prefix="fp-steamlib-")
library = os.path.join(folder, "steamapps")
os.makedirs(library)
real_libraries = steamgames._libraries
steamgames._libraries = lambda: [library]

print("the stamp follows the library")
first = steamgames.library_stamp()
check(first and first[0][0] == library, "it names the library: %r" % (first,))

open(os.path.join(library, "appmanifest_274190.acf"), "w").write("x")
time.sleep(0.01)
after_install = steamgames.library_stamp()
check(after_install != first,
      "installing a game changes it: %r -> %r" % (first, after_install))

os.unlink(os.path.join(library, "appmanifest_274190.acf"))
time.sleep(0.01)
after_remove = steamgames.library_stamp()
check(after_remove != after_install,
      "and uninstalling one changes it again: %r" % (after_remove,))

print("\nand the catalogue's fingerprint carries it")
source = open(os.path.join(ROOT, "fourthplayer", "catalogue.py"),
              encoding="utf-8").read()
mark = source.split("def _fingerprint")[1].split("\n    def ")[0]
check("library_stamp()" in mark,
      "the fingerprint asks Steam's library, not only the chosen list")

print("\na library that has gone is not an error")
steamgames._libraries = lambda: ["/nowhere/steamapps"]
check(steamgames.library_stamp() == (("/nowhere/steamapps", 0),),
      "a missing library reads as nothing rather than raising: %r"
      % (steamgames.library_stamp(),))

steamgames._libraries = real_libraries
shutil.rmtree(folder, ignore_errors=True)
print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
