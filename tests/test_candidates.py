"""Announcing a LAN socket at the public address, so port forwarding works.

This is the piece that makes fourth-player reachable from outside a symmetric
NAT, and it is pure string surgery on ICE candidates -- easy to get subtly
wrong and impossible to debug from someone else's phone. So it is tested here
rather than discovered there.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from fourthplayer import net
    from fourthplayer.video import Peer
except ImportError as exc:
    print("SKIPPED: %s -- needs the GStreamer bindings, which live on the host"
          % exc)
    sys.exit(0)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


class FakeStage:
    def __init__(self, public):
        self.public_ip = public


def rewrite(candidate, public="203.0.113.7"):
    peer = Peer.__new__(Peer)          # no pipeline, no GStreamer
    peer.stage = FakeStage(public)
    peer._announced = set()
    parts = candidate.split()
    kind = parts[parts.index("typ") + 1] if "typ" in parts else ""
    return peer._forwarded_candidate(parts, kind)


HOST = "candidate:1 1 UDP 2015363327 192.168.1.132 40005 typ host"

print("a LAN candidate is announced again at the public address")
out = rewrite(HOST)
check(out is not None, "something is produced")
fields = (out or "").split()
check(fields[4] == "203.0.113.7", "the address is the public one, got %r" % fields[4])
check(fields[5] == "40005",
      "the port is UNCHANGED -- the forwarded one, not STUN's: got %r" % fields[5])
check("typ srflx" in out, "it is announced as reflexive")
check("raddr 192.168.1.132" in out and "rport 40005" in out,
      "and records the local socket as its base: %r" % out)

print("\nit must not look like a duplicate of the candidate it shadows")
check(fields[0] != "candidate:1", "the foundation differs, got %r" % fields[0])
# The priority is not just "lower" -- it has to be lower by a whole type
# preference, or the browser ranks the public address level with the LAN one
# and nominates it. When that happened, a guest sitting in the house got a
# connected session and a black screen, because the forward it was pointed at
# was not carrying anything. A real srflx sits 26 * 2^24 below its base.
check(int(fields[3]) == 2015363327 - 26 * (1 << 24),
      "the priority is a genuine srflx priority, got %s (host was %d)"
      % (fields[3], 2015363327))
check(2015363327 - int(fields[3]) > 400000000,
      "so every host candidate outranks it and the LAN route is tried first")

print("\nthe component and transport are carried through untouched")
check(fields[1] == "1" and fields[2].upper() == "UDP",
      "component and transport survive: %r" % fields[:3])

print("\nonly private addresses are shadowed")
check(rewrite("candidate:2 1 UDP 100 8.8.8.8 40005 typ host") is None,
      "a public host candidate is left alone")
check(rewrite("candidate:3 1 UDP 100 192.168.1.132 40005 typ srflx") is None,
      "a reflexive candidate is not shadowed again")
check(rewrite("candidate:4 1 UDP 100 192.168.1.132 40005 typ relay") is None,
      "nor is a relayed one")

print("\nand nothing happens without a public address to offer")
check(rewrite(HOST, public="") is None, "no public ip, no extra candidate")

print("\nthe same socket is never announced twice")
peer = Peer.__new__(Peer)
peer.stage = FakeStage("203.0.113.7")
peer._announced = set()
parts = HOST.split()
first = peer._forwarded_candidate(parts, "host")
again = peer._forwarded_candidate(parts, "host")
check(first is not None and again is None,
      "a repeated candidate is announced once")
other = peer._forwarded_candidate(
    "candidate:5 1 UDP 100 192.168.1.132 40041 typ host".split(), "host")
check(other is not None, "but a different port still is")

print("\nprivate ranges are recognised, including CGNAT")
for address, private in (("10.0.0.1", True), ("172.16.5.4", True),
                         ("172.32.5.4", False), ("192.168.0.1", True),
                         ("100.64.0.1", True), ("1.1.1.1", False)):
    check(net.is_private(address) is private, "%s private=%s" % (address, private))

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
