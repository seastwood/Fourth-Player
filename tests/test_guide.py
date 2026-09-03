"""The button a guest does not have, and the numbers that move when it goes.

The Steam button. Held in front of a running game it opens Steam's overlay,
and the overlay is a store with a saved card in it. Withholding a guest's
frames while Steam's own interface is in front does not cover it, because the
game still has the foreground the whole time the overlay is up -- so the only
answer left is that the pad cannot press it at all.

Not filtered: absent. `capabilities()` is the boundary this design already
leans on -- a guest cannot type here because the device cannot express a
keystroke -- and this is that argument one button further.

The part worth a test of its own is what removing it does to everything else.
RetroArch numbers buttons by ascending evdev code over exactly the codes the
device declares, so dropping BTN_MODE slides the thumb sticks from 9 and 10 to
8 and 9. Written down twice -- once in the device, once in the profile -- that
drift shows up as a guest pressing the left stick and opening the emulator's
menu. So both come from one list, and this checks they agree.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


try:
    from evdev import ecodes as e
    from fourthplayer import pads as padlib, protocol as P, retroarch
except ImportError as exc:
    print("SKIPPED: %s -- needs python3-evdev, which lives on the host" % exc)
    sys.exit(0)

print("what the device declares")
with_guide = padlib.button_codes(True)
without = padlib.button_codes(False)
check(e.BTN_MODE in with_guide, "the guide button exists when it is asked for")
check(e.BTN_MODE not in without, "and is simply not there when it is not")
check(len(without) == len(with_guide) - 1, "nothing else went with it")
check(e.BTN_MODE not in padlib.capabilities(False)[e.EV_KEY],
      "the capability set is the same list, so the device cannot press it")

print("what a press turns into")
state = P.PadState(buttons=1 << P.BTN_GUIDE)
codes = [code for kind, code, value in padlib.to_events(state, False)
         if kind == e.EV_KEY and value]
check(e.BTN_MODE not in codes,
      "a guest holding guide writes nothing: not filtered later, never written")
codes = [code for kind, code, value in padlib.to_events(state, True)
         if kind == e.EV_KEY and value]
check(e.BTN_MODE in codes, "and with the button declared, it is written")

print("what RetroArch is told")
lines = dict(line.split(" = ") for line in
             retroarch.button_lines(True).splitlines())
check(lines['input_menu_toggle_btn'] == '"8"',
      "with the guide button, the menu is button 8 as it always was")
check(lines['input_l3_btn'] == '"9"' and lines['input_r3_btn'] == '"10"',
      "and the thumbs are 9 and 10")
lines = dict(line.split(" = ") for line in
             retroarch.button_lines(False).splitlines())
check("input_menu_toggle_btn" not in lines,
      "without it, nothing is bound to a menu the pad cannot open")
check(lines['input_l3_btn'] == '"8"' and lines['input_r3_btn'] == '"9"',
      "and the thumbs move down to 8 and 9 -- which is the whole hazard: "
      "left in place they would have been the menu button")
check(lines['input_a_btn'] == '"1"' and lines['input_start_btn'] == '"7"',
      "everything below the guide button is where it was")

print("and the two lists cannot drift")
numbered = [name for name in retroarch.button_lines(False).splitlines()]
check(len(numbered) == len(without),
      "one profile line per declared code, counted from the same list")

print("off by default")
from fourthplayer import config
check(config.Config().guest_guide_button is False,
      "a guest has no guide button unless somebody turns it on")

print()
if fails:
    print("FAILED: %d" % len(fails))
    for line in fails:
        print("  " + line)
    sys.exit(1)
print("all good")
