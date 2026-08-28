"""The address guests' links are built on.

A base that is quietly wrong is the worst way for this to fail: the link looks
right, gets sent to a friend, and goes nowhere -- and the owner finds out from
the friend. So what a person types is tidied where that is unambiguous, and
refused where it is not.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from fourthplayer.server import Server

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


clean = Server.clean_url

print("what a person types becomes something a link can be built on")
cases = [
    ("https://fourthplayer.example.com", "https://fourthplayer.example.com"),
    ("fourthplayer.example.com", "https://fourthplayer.example.com"),
    ("https://fourthplayer.example.com/", "https://fourthplayer.example.com"),
    ("  https://fourthplayer.example.com/  ", "https://fourthplayer.example.com"),
    ("http://192.168.1.132:8443", "http://192.168.1.132:8443"),
    # A reverse proxy may serve this under a prefix, so a path is kept.
    ("https://example.com/play/", "https://example.com/play"),
    ("", ""),
    ("   ", ""),
]
for typed, want in cases:
    got = clean(typed)
    check(got == want, "%r -> %r" % (typed, got))

print("and what cannot be one is refused, not guessed at")
for bad, why in [
    ("ftp://example.com", "a scheme nothing here speaks"),
    ("https://exa mple.com", "a space in the host"),
    ("https://example.com/j/abc?x=1", "a query string"),
    ("https://example.com#top", "a fragment"),
]:
    try:
        clean(bad)
        check(False, "%s (%r) is refused" % (why, bad))
    except ValueError:
        check(True, "%s (%r) is refused" % (why, bad))

print("clearing it is a real answer, not an error")
check(clean("") == "", "an empty address means back to this machine's own")

print(("FAILED: %d" % len(fails)) if fails else "test_address: all ok")
sys.exit(1 if fails else 0)
