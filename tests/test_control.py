"""The channel `start` and `status` use to drive a running server.

One socket carrying line-delimited JSON, addressed two different ways. On
Linux it is a Unix socket in XDG_RUNTIME_DIR, where the permission check is
the filesystem's. Windows has no start_unix_server and its loopback is not
private -- any process on the machine can reach 127.0.0.1 -- so there the port
and a random token go in a file under LOCALAPPDATA, which is private to the
user, and a connection that cannot present the token is dropped before its
request is read.

What is being tested is that both arrangements carry a request and an answer,
and that the Windows one actually refuses a caller who has not read the file.
A control channel that accepted anyone would hand a stranger on the machine
the ability to open sessions, set PINs and kick guests.
"""
import asyncio
import json
import os
import socket
import sys
import threading

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# A channel of this test's own, before control.py is imported and works out
# where the real one lives.
#
# Not tidiness. The first version of this ran against the machine's actual
# address, which is fine on a workstation and wrong everywhere it matters: on
# a host that is *running* fourth-player the channel is already in use, so the
# test failed -- and one branch of in_use() unlinks an address it finds
# unreachable, which on a busy machine is a running server's control socket
# being deleted by its own test suite. Caught by the suite failing on ultra
# while passing alone, which is exactly the shape of a test that reaches
# outside itself.
import tempfile

_own = tempfile.mkdtemp(prefix="fp-control-test-")
os.environ["XDG_RUNTIME_DIR"] = _own        # Linux: where the socket goes
os.environ["LOCALAPPDATA"] = _own           # Windows: where the token file goes

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


from fourthplayer import control


async def handler(reader, writer):
    # in_use() probes by connecting and hanging up, so a handler that cannot
    # survive a caller who says nothing prints a traceback on every check.
    try:
        line = await reader.readline()
        if not line:
            writer.close()
            return
        request = json.loads(line)
        writer.write((json.dumps({"ok": True, "saw": request.get("cmd")})
                      + "\n").encode())
        await writer.drain()
    except (ConnectionError, ValueError):
        pass
    finally:
        writer.close()


def ask(request, sock=None):
    own = sock is None
    sock = sock or control.connect(timeout=5)
    try:
        sock.sendall((json.dumps(request) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
        return json.loads(data or b"{}")
    finally:
        if own:
            sock.close()


async def main():
    print("on this machine the channel is at")
    print("  %s" % control.address())
    check(not control.in_use(), "nothing is listening there to begin with")

    server = await control.serve(handler)
    try:
        check(control.in_use(), "once served, it reports itself in use")

        answer = await asyncio.get_running_loop().run_in_executor(
            None, ask, {"cmd": "status"})
        check(answer.get("ok") and answer.get("saw") == "status",
              "a request goes down it and an answer comes back: %r" % answer)

        if control.WINDOWS:
            print("\nand a caller who has not read the token file")
            # Straight to the port, with no token and then a request. It must
            # be dropped before the request is read.
            port, _token = control._read_state()

            def barge():
                raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                raw.settimeout(5)
                raw.connect(("127.0.0.1", port))
                raw.sendall(b'{"cmd": "stop"}\n')
                out = b""
                try:
                    while not out.endswith(b"\n"):
                        chunk = raw.recv(4096)
                        if not chunk:
                            break
                        out += chunk
                finally:
                    raw.close()
                return out

            got = await asyncio.get_running_loop().run_in_executor(None, barge)
            check(got == b"", "gets nothing back and is closed on: %r" % got)

            def wrong_token():
                raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                raw.settimeout(5)
                raw.connect(("127.0.0.1", port))
                raw.sendall(b"0" * 64 + b'\n{"cmd": "stop"}\n')
                out = b""
                try:
                    while not out.endswith(b"\n"):
                        chunk = raw.recv(4096)
                        if not chunk:
                            break
                        out += chunk
                finally:
                    raw.close()
                return out

            got = await asyncio.get_running_loop().run_in_executor(None, wrong_token)
            check(got == b"", "and so does one with the wrong token: %r" % got)
        else:
            print("\nand the Unix socket keeps its own permissions")
            mode = os.stat(control.UNIX_PATH).st_mode & 0o777
            check(mode == 0o600,
                  "0o%o -- nobody else on the machine may connect" % mode)
    finally:
        server.close()
        await server.wait_closed()
        control.cleanup()

    check(not control.in_use(), "and it is gone once the server stops")

asyncio.run(main())

import shutil
shutil.rmtree(_own, ignore_errors=True)

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
