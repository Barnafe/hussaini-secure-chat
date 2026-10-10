"""Test helpers: an in-process TCP server, a synchronous test client that speaks the real
wire protocol, real per-user encryption identities, and a sniffer that records every byte
travelling between a client and the server."""
import json
import socket
import threading
import time

from server.app import ChatServer, start_server
from shared import protocol as P
from shared.keys import Identity, generate_passphrase, seal_dm, seal_group

KDF_ITERATIONS = 1000          # few iterations so tests are fast - the algorithm is identical
PASSWORD = "Passw0rd!"
FAKE_KEY = "A" * 43 + "="     # 32 zero-ish bytes, base64: a syntactically valid public key

IDENTITIES = {}                # username -> Identity (created by register())
PASSPHRASES = {}               # username -> passphrase


class ServerThread:
    """Runs a real ChatServer (real TCP listener, one thread per client) on a random local port."""

    def __init__(self, db, **chat_kwargs):
        chat_kwargs.setdefault("bcrypt_rounds", 4)
        chat_kwargs.setdefault("rate_limit", None)
        self.db = db
        self.chat = ChatServer(db, **chat_kwargs)
        self.tcp = None
        self.port = None

    @property
    def address(self):
        return f"127.0.0.1:{self.port}"

    def start(self):
        self.tcp = start_server(self.chat, "127.0.0.1", 0)
        self.port = self.tcp.port
        return self

    def stop(self):
        for c in list(_OPEN_CLIENTS):
            c.close()
        _OPEN_CLIENTS.clear()
        self.tcp.stop()
        self.chat.close_all()


_OPEN_CLIENTS = []


class TClient:
    """Synchronous test client: length-prefixed JSON frames over a plain socket."""

    def __init__(self, address):
        host, port = P.parse_address(address)
        self.sock = socket.create_connection((host, port), timeout=5)
        self.reader = P.FrameReader(self.sock)
        _OPEN_CLIENTS.append(self)
        self.inbox = []
        self.username = None

    def send(self, **kw):
        P.send_frame(self.sock, kw)

    def send_raw(self, body: bytes):
        """Send arbitrary bytes as ONE frame (correct length prefix)."""
        self.sock.sendall(P.HEADER.pack(len(body)) + body)

    def _recv(self, timeout):
        raw = self.reader.read(idle=timeout, poll=0.05)
        if raw is None:
            raise P.ConnectionClosed("server closed the connection")
        return json.loads(raw.decode("utf-8"))

    def expect(self, mtype, timeout=3, **match):
        """Return the first message of this type (and matching fields)."""
        def ok(m):
            return m.get("type") == mtype and all(m.get(k) == v for k, v in match.items())
        for i, m in enumerate(self.inbox):
            if ok(m):
                return self.inbox.pop(i)
        end = time.time() + timeout
        while True:
            left = end - time.time()
            if left <= 0:
                raise AssertionError(f"timed out waiting for {mtype} {match}; inbox={self.inbox}")
            try:
                m = self._recv(left)
            except TimeoutError:
                raise AssertionError(f"timed out waiting for {mtype} {match}; inbox={self.inbox}")
            if ok(m):
                return m
            self.inbox.append(m)

    def drain(self, wait=0.4):
        out = list(self.inbox)
        self.inbox.clear()
        while True:
            try:
                out.append(self._recv(wait))
            except (TimeoutError, P.ConnectionClosed, OSError):
                break
        return out

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def closed_within(client, seconds=5.0):
    """True if the server closes this connection within ``seconds``."""
    end = time.time() + seconds
    while time.time() < end:
        try:
            client._recv(0.1)
        except TimeoutError:
            continue
        except (P.ConnectionClosed, OSError):
            return True
    return False


def new_identity(username):
    passphrase = generate_passphrase()
    ident = Identity(username, passphrase, KDF_ITERATIONS)
    return passphrase, ident


def register(address, username, fullname=None, password=PASSWORD):
    passphrase, ident = new_identity(username)
    c = TClient(address)
    c.send(type="register", username=username, password=password, confirm=password,
           fullname=fullname or username.title(), public_key=ident.public_key_b64)
    res = c.expect("register_result")
    c.close()
    if res["ok"]:
        IDENTITIES[res["username"]] = ident
        PASSPHRASES[res["username"]] = passphrase
    return res


def login(address, username, password=PASSWORD):
    c = TClient(address)
    c.send(type="login", username=username, password=password,
           public_key=IDENTITIES[username].public_key_b64)
    res = c.expect("login_result")
    assert res["ok"], res
    c.username = username
    c.state = c.expect("state")
    return c


def make_user(address, username, **kw):
    res = register(address, username, **kw)
    assert res["ok"], res
    return login(address, username)


def befriend(a, b):
    a.send(type="friend_request", to=b.username)
    b.expect("friend_request_in", username=a.username)
    b.send(type="friend_respond", **{"from": a.username}, accept=True)
    a.expect("friend_added")
    b.expect("friend_added")


def enc_dm(sender, receiver, text):
    """Encrypt exactly like the real client does (needs both users to be registered)."""
    return seal_dm(IDENTITIES[sender], IDENTITIES[receiver].public_key_b64, receiver, text)


def enc_group(sender, gid, text, members):
    keys = {u: IDENTITIES[u].public_key_b64 for u in members}
    return seal_group(IDENTITIES[sender], gid, text, keys)


class Sniffer:
    """A transparent TCP relay that records all bytes seen on the wire."""

    def __init__(self, target_port):
        self.target_port = target_port
        self.captured = bytearray()
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(5)
        self.port = self.listener.getsockname()[1]
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    @property
    def address(self):
        return f"127.0.0.1:{self.port}"

    def _accept(self):
        while not self._stop:
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            upstream = socket.create_connection(("127.0.0.1", self.target_port))
            for a, b in ((client, upstream), (upstream, client)):
                threading.Thread(target=self._pipe, args=(a, b), daemon=True).start()

    def _pipe(self, src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                self.captured += data
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.close()
                except OSError:
                    pass

    def stop(self):
        self._stop = True
        self.listener.close()


def wait_until(predicate, timeout=3.0, interval=0.05):
    """Poll until predicate() is truthy (the server marks messages delivered just after sending)."""
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())
