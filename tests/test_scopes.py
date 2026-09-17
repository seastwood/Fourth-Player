"""Every name a function reads can actually be found at runtime.

(Named test_scopes rather than test_names: test_names.py is about guests'
names and joining without the link, and I wrote this over the top of it
before noticing. Restored from git.)

Twice in one evening a name that does not exist took the Windows host down,
and neither time did anything catch it before it ran:

  vdisplay.py guarded a block on SUPPORTED, which was defined further down
  the file. Import failed outright, so the host died on start and the tray
  restarted it into the same failure.

  video.py's Stage.start() referred to `chosen`, a local of __init__. The
  capture pipeline refused, so the session could not be restored, and somebody
  typed a PIN at a host with no session for the second time that night.

py_compile sees neither: both are valid syntax. There is no pyflakes here and
this project installs with a git clone and a Python, so rather than add a
dependency for one check, the check is written out -- which is also how it can
say what it is for.

What it does: for every function, work out the names it reads and the names it
could possibly resolve -- its own locals and parameters, the enclosing
functions' locals, the module's globals, and the builtins -- and complain
about the difference. Comprehensions and lambdas carry their own scopes;
attributes are not names and are not checked, because `self.thing` failing is
a different kind of mistake and not one this can see.
"""
import ast
import builtins
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__",
                                 "__spec__", "__package__", "__builtins__"}


def module_names(tree):
    """Everything defined at module level, however it got there."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                found.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            found.add(node.id)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            found.add(node.name)
        elif isinstance(node, ast.Global):
            found.update(node.names)
    return found


def bound_here(node):
    """Names this scope binds: assignments, parameters, imports, walrus."""
    bound = set()
    for arg in getattr(node, "args", None) and (
            node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            + ([node.args.vararg] if node.args.vararg else [])
            + ([node.args.kwarg] if node.args.kwarg else [])) or []:
        bound.add(arg.arg)
    for inner in ast.walk(node):
        # A nested function's own body has its own scope; its *name* is bound
        # here though.
        if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef)):
            bound.add(inner.name)
        elif isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Store):
            bound.add(inner.id)
        elif isinstance(inner, (ast.Import, ast.ImportFrom)):
            for alias in inner.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(inner, ast.ExceptHandler) and inner.name:
            bound.add(inner.name)
        elif isinstance(inner, ast.arg):
            bound.add(inner.arg)
    return bound


def scan(path):
    """Every (function, unresolvable name) in one file."""
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    globals_ = module_names(tree) | BUILTINS
    trouble = []

    def walk(node, visible, where):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = visible | bound_here(child)
                reads = {n.id for n in ast.walk(child)
                         if isinstance(n, ast.Name)
                         and isinstance(n.ctx, ast.Load)}
                for name in sorted(reads - inner):
                    trouble.append(("%s.%s" % (where, child.name), name))
                walk(child, inner, "%s.%s" % (where, child.name))
            elif isinstance(child, ast.ClassDef):
                # A class body's names are not visible to methods, which is
                # why they are not added here.
                walk(child, visible, "%s.%s" % (where, child.name))
            else:
                walk(child, visible, where)

    walk(tree, globals_, os.path.basename(path))
    return trouble


print("the checker finds the two faults it was written for")
# Proven against the real thing rather than trusted: a checker that cannot
# demonstrate a catch is a checker nobody should believe.
import tempfile
with tempfile.TemporaryDirectory() as tmp:
    bad = os.path.join(tmp, "bad.py")
    with open(bad, "w") as handle:
        handle.write("def start(self):\n    return chosen\n")
    found = scan(bad)
    check(("bad.py.start", "chosen") in found,
          "a local of another function is caught: %s" % found)

    ok = os.path.join(tmp, "ok.py")
    with open(ok, "w") as handle:
        handle.write("CHOSEN = 1\n\n\ndef start(self):\n"
                     "    inner = CHOSEN\n"
                     "    def deeper():\n        return inner\n"
                     "    return deeper()\n")
    check(scan(ok) == [],
          "and a name from an enclosing scope is not: %s" % scan(ok))

print()
print("and every module in the package passes it")
package = os.path.join(ROOT, "fourthplayer")
for name in sorted(os.listdir(package)):
    if not name.endswith(".py"):
        continue
    trouble = scan(os.path.join(package, name))
    check(not trouble, "%s%s" % (name, "" if not trouble else
          " reads " + ", ".join("%s in %s" % (n, w) for w, n in trouble[:4])))

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
