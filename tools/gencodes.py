"""Emit the evdev constants this project uses, as a plain-Python table."""
from evdev import ecodes
import re

FAMILIES = ("EV_", "KEY_", "BTN_", "ABS_", "REL_", "SYN_", "MSC_", "REP_")
names = sorted(n for n in dir(ecodes)
               if n.startswith(FAMILIES) and isinstance(getattr(ecodes, n), int))

out = []
out.append('"""Input-event codes, generated from evdev -- do not edit by hand.')
out.append("")
out.append("Regenerate with tools/gencodes.py on a Linux machine that has evdev.")
out.append('"""')
out.append("")
out.append("# name -> code, exactly as the kernel defines them. These are a stable ABI,")
out.append("# so the numbers are the same on every Linux and are safe to carry to a")
out.append("# machine that has no kernel to ask -- see codes.py for why that matters.")
out.append("CODES = {")
for n in names:
    out.append("    %r: %d," % (n, getattr(ecodes, n)))
out.append("}")
out.append("")
out.append("# code -> name, for the one place that asks the question backwards")
out.append("# (overlay.py, naming the key somebody just pressed).")
out.append("KEY = {")
for code, name in sorted(ecodes.KEY.items()):
    if isinstance(name, str):
        out.append("    %d: %r," % (code, name))
    else:
        out.append("    %d: %r," % (code, tuple(name)))
out.append("}")
print("\n".join(out))
