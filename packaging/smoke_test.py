"""Start a server program, then register / log in / chat through it like real clients would.

    python packaging/smoke_test.py dist/HussainiServer.exe
    python packaging/smoke_test.py python run_server.py          (any command works)

Used by the GitHub Actions build to prove the packaged .exe really works. Exit code 0 = OK.
"""
import os
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from shared import protocol as P                       # noqa: E402
from shared.keys import (Identity, generate_passphrase, open_dm, seal_dm)  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Client:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.reader = P.FrameReader(self.sock)
        self.pending = []

    def send(self, **kw):
        P.send_frame(self.sock, kw)

    def expect(self, mtype, timeout=10):
        for i, m in enumerate(self.pending):
            if m["type"] == mtype:
                return self.pending.pop(i)
        end = time.time() + timeout
        while time.time() < end:
            try:
                raw = self.reader.read(idle=0.5, poll=0.1)
            except P.IdleTimeout:
                continue
            if raw is None:
                break
            m = P.decode(raw)
            if m["type"] == mtype:
                return m
            self.pending.append(m)
        raise SystemExit(f"SMOKE TEST FAILED: no '{mtype}' from the server")


def main():
    cmd = sys.argv[1:]
    if not cmd:
        sys.exit(__doc__)
    port = free_port()
    tmp = tempfile.mkdtemp(prefix="hussaini_smoke_")
    env = dict(os.environ, HUSSAINI_SERVER_PORT=str(port), HUSSAINI_SERVER_LISTEN_HOST="127.0.0.1",
               HUSSAINI_DATABASE_FILE=os.path.join(tmp, "smoke.db"), HUSSAINI_BCRYPT_ROUNDS="4",
               PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.time() + 60
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                if proc.poll() is not None:
                    sys.exit("SMOKE TEST FAILED: the server program exited early:\n" + proc.stdout.read())
                if time.time() > deadline:
                    sys.exit("SMOKE TEST FAILED: the server did not start within 60 s")
                time.sleep(0.3)

        users = {}
        for name in ("alice", "bob"):
            ident = Identity(name, generate_passphrase(), 1000)
            c = Client(port)
            c.send(type="register", username=name, password="Passw0rd!", confirm="Passw0rd!",
                   fullname=name.title(), public_key=ident.public_key_b64)
            assert c.expect("register_result")["ok"], "registration failed"
            c.send(type="login", username=name, password="Passw0rd!", public_key=ident.public_key_b64)
            assert c.expect("login_result")["ok"], "login failed"
            c.expect("state")
            users[name] = (c, ident)
        (a, ia), (b, ib) = users["alice"], users["bob"]
        a.send(type="friend_request", to="bob")
        b.expect("friend_request_in")
        b.send(type="friend_respond", **{"from": "alice"}, accept=True)
        added = a.expect("friend_added")
        b.expect("friend_added")
        a.send(type="send_dm", to="bob", payload=seal_dm(ia, added["user"]["public_key"], "bob", "smoke test ok"))
        got = b.expect("message")
        assert open_dm(ib, ia.public_key_b64, "alice", "bob", got["payload"]) == "smoke test ok"
        print("SMOKE TEST PASSED: register, login, friends and an end-to-end encrypted message all work.")
    except AssertionError as exc:
        sys.exit(f"SMOKE TEST FAILED: {exc}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
