"""A self-signed certificate, so the LAN case works with nothing configured.

The browser needs a secure context or it will not hand out the Gamepad API or
do WebRTC at all, which makes "just try it on the sofa" impossible over plain
HTTP. So the server generates its own certificate on first run and the LAN user
clicks through one warning.

In the deployment this is built for, HAProxy terminates a real certificate and
this file is never used: run with `behind_proxy` and the server speaks plain
HTTP to the proxy only.
"""

import datetime
import ipaddress
import os
import socket

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

VALID_DAYS = 3650


def ensure_certificate(cert_path, key_path, hostnames=()):
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return cert_path, key_path
    os.makedirs(os.path.dirname(cert_path), exist_ok=True)

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fourth-player")])

    alternatives = [x509.DNSName("localhost")]
    for host in hostnames:
        try:
            alternatives.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            alternatives.append(x509.DNSName(host))
    for address in _local_addresses():
        alternatives.append(x509.IPAddress(ipaddress.ip_address(address)))

    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(alternatives), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    with open(key_path, "wb") as handle:
        handle.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
    os.chmod(key_path, 0o600)
    with open(cert_path, "wb") as handle:
        handle.write(certificate.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def _local_addresses():
    found = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except socket.gaierror:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 1))     # TEST-NET-1, never routed
        found.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    return found
