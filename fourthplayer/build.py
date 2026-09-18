"""Which copy of the code a process is actually running.

A deploy on Windows silently did nothing, twice over, and there was no way to
see it from the outside. The tray is the supervisor there, and `schtasks /End`
ends the tray without ending the host it started -- so the host was orphaned,
survived the restart, and the new tray found it answering on the port and said
"already running". Everything looked like a successful deploy: git pulled, the
task restarted, the log kept moving. The host was three commits behind, and the
only reason it came out at all was that the browser and the host disagreed
about a message format in a way that produced a number too odd to ignore.

So a host says what it loaded, and a supervisor that finds one already running
compares it with what is on disk now. There is nothing clever here: the newest
modification time in the tree is enough to tell "the code changed under you"
from "it did not", which is the only question being asked.
"""

import os

# The package, and the files served to guests. A change to either is a change
# to what a guest gets, and the second half is easy to forget: the worker that
# decodes the picture is not Python and does not need a restart to take
# effect, but a host that has not reloaded its static files still serves the
# old ones from its own cache.
_LOOK = (os.path.dirname(os.path.abspath(__file__)),
         os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "web"))


def stamp():
    """A short string that changes whenever the code on disk changes."""
    newest = 0.0
    count = 0
    for top in _LOOK:
        for here, dirs, names in os.walk(top):
            # Nothing that a build writes: __pycache__ is rewritten by merely
            # running, which would make every process look like a new build.
            dirs[:] = [d for d in dirs
                       if d not in ("__pycache__", "node_modules", ".git")]
            for name in names:
                if name.endswith((".pyc", ".pyo", ".log")):
                    continue
                try:
                    newest = max(newest, os.stat(os.path.join(here, name)).st_mtime)
                    count += 1
                except OSError:
                    continue
    return "%d-%d" % (int(newest), count)


# What *this* process loaded, fixed at import. A host computing it fresh on
# being asked would answer for the code on disk rather than the code it is
# running, which is the one thing this must never do.
LOADED = stamp()
