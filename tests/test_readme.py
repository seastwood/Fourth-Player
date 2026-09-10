"""The README's commands are real, and the real commands are in the README.

Documentation rots in exactly the way a skipped test does: quietly, and in the
direction of claiming more than is true. This checks the one part of it a
machine can check -- that every command written down exists, and that every
command that exists is written down.

There is deliberately no check here for anybody's name. Asserting that a
particular person's name is absent would mean writing that name into the
repository to assert it, which is the opposite of the point.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
cli = open(os.path.join(ROOT, "fourthplayer", "cli.py"), encoding="utf-8").read()

# What the CLI actually defines. Read out of the source rather than by importing
# it, so this suite runs on a machine with no GStreamer and no evdev.
def parsers(variable):
    # The lookbehind matters: without it, "sub" also matches the tail of
    # "admin_sub", and the top-level list quietly swallows every admin
    # subcommand -- which makes the last check below pass for the wrong reason.
    return set(re.findall(r'(?<![A-Za-z0-9_])' + re.escape(variable)
                          + r'\.add_parser\(\s*"([^"]+)"', cli))


admin = parsers("admin_sub")
top = parsers("sub")
check(len(admin) > 3, "found the admin subcommands in cli.py: %s"
      % ", ".join(sorted(admin)))
check("admin" in top, "and the top-level ones: %s" % ", ".join(sorted(top)))

print("\nevery admin command in the README is one that exists")
written = set(re.findall(r"fourth-player admin ([a-z][a-z0-9-]*)", readme))
for name in sorted(written):
    check(name in admin, "admin %s is a real subcommand" % name)

print("\nand every admin command that exists is in the README")
for name in sorted(admin):
    check(name in written,
          "admin %s is documented -- an undocumented command is one nobody "
          "can be told to run" % name)

print("\nevery other command in the README is one that exists")
# `fourth-player admin ...` is handled above; this is the rest.
# A letter first, so a flag like "fourth-player -f" is not read as a command.
others = set(re.findall(r"fourth-player ([a-z][a-z0-9-]*)", readme)) - {"admin"}
for name in sorted(others):
    check(name in top, "%s is a real command" % name)

print()
if fails:
    print("FAILURES: %d" % len(fails))
    sys.exit(1)
print("test_readme: all ok")
