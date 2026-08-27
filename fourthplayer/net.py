"""Finding out how this machine looks from the internet.

One job: discover the public address with STUN, so the server can tell guests
somewhere they can actually reach it.

That is needed because of how home routers behave. A symmetric NAT hands out a
*different* external port for every destination, so the port STUN reports is
the one that works for talking to the STUN server and nobody else. A guest told
that port finds nothing listening. Meanwhile a static port forward -- WAN 40005
to this box 40005 -- works perfectly for anyone who tries it, and ICE never
learns it exists.

So the public *address* here is useful and the public *port* is not, which is
exactly what `session.public_candidate` relies on.
"""

import logging
import os
import socket
import struct

log = logging.getLogger("fourthplayer.net")

STUN_SERVERS = (("stun.cloudflare.com", 3478),
                ("stun.l.google.com", 19302),
                ("stun1.l.google.com", 19302))

MAGIC = 0x2112A442
BINDING_REQUEST = 0x0001
XOR_MAPPED_ADDRESS = 0x0020


def public_address(timeout=4.0, servers=STUN_SERVERS):
    """(ip, port) as the internet sees us, or None.

    The port is per-destination on a symmetric NAT and should not be trusted
    for anything but diagnosis; the address is what matters.
    """
    for host, port in servers:
        try:
            return _ask(host, port, timeout)
        except (OSError, ValueError):
            continue
    return None


def _ask(host, port, timeout):
    transaction = os.urandom(12)
    request = struct.pack(">HHI", BINDING_REQUEST, 0, MAGIC) + transaction
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(request, (host, port))
        data, _ = sock.recvfrom(2048)
    finally:
        sock.close()
    if len(data) < 20 or data[8:20] != transaction:
        raise ValueError("not our answer")
    return _xor_mapped(data)


def _xor_mapped(data):
    index = 20
    while index + 4 <= len(data):
        kind, length = struct.unpack(">HH", data[index:index + 4])
        value = data[index + 4:index + 4 + length]
        if kind == XOR_MAPPED_ADDRESS and len(value) >= 8:
            port = struct.unpack(">H", value[2:4])[0] ^ (MAGIC >> 16)
            address = bytes(b ^ c for b, c in zip(value[4:8],
                                                  struct.pack(">I", MAGIC)))
            return socket.inet_ntoa(address), port
        index += 4 + length + ((4 - length % 4) % 4)
    raise ValueError("no mapped address in the answer")


def is_private(address):
    try:
        packed = socket.inet_aton(address)
    except OSError:
        return False
    first, second = packed[0], packed[1]
    return (first == 10
            or (first == 172 and 16 <= second <= 31)
            or (first == 192 and second == 168)
            or first == 127
            or (first == 169 and second == 254)
            or (first == 100 and 64 <= second <= 127))     # CGNAT
