"""Phase 3/4 - client tests: network layer + controller against a real server.

Two or three ChatController objects (the same logic the Tkinter window uses) talk to
a real TCP server, so this exercises registration with an auto-generated passphrase,
encryption, decryption, offline delivery, history, groups, auto-reconnect and errors.
"""
import time

import pytest

from client.controller import DECRYPT_FAIL, ChatController, dm_key, group_key
from client.network import normalize_address
from server.database import Database
from shared.keys import generate_passphrase
from tests.helpers import ServerThread

PW = "Passw0rd!"


@pytest.fixture
def srv(tmp_path):
    db = Database(sqlite_path=str(tmp_path / "c.db"))
    server = ServerThread(db).start()
    yield server
    server.stop()
    db.close()


class Peer:
    def __init__(self, srv):
        self.srv = srv
        self.passphrase = None            # filled in by register()
        self.log = []
        self.ctrl = ChatController(kdf_iterations=1000)
        self.ctrl.on_update = lambda t, p=None: self.log.append((t, p))

    def topics(self):
        return [t for t, _ in self.log]

    def wait(self, predicate, timeout=8.0, what="condition"):
        end = time.time() + timeout
        while time.time() < end:
            self.ctrl.poll()
            if predicate():
                return True
            time.sleep(0.02)
        raise AssertionError(f"timed out waiting for {what}; log tail={self.log[-6:]}")

    def last(self, topic):
        for t, p in reversed(self.log):
            if t == topic:
                return p

    def register(self, user, name=None):
        self.ctrl.start_auth(self.srv.address, "register", "", user, PW,
                             fullname=name or user.title(), confirm=PW)
        self.wait(lambda: "register_result" in self.topics() or "auth_error" in self.topics(), what="register")
        result = self.last("register_result")
        if result and result.get("ok"):
            self.passphrase = result["passphrase"]      # the passphrase the user is shown
        return result

    def login(self, user, passphrase=None):
        self.log.clear()
        self.ctrl.start_auth(self.srv.address, "login", passphrase or self.passphrase, user, PW)
        self.wait(lambda: self.ctrl.logged_in or "auth_error" in self.topics(), what="login")
        if self.ctrl.logged_in:
            self.wait(lambda: "state" in self.topics(), what="state")

    def texts(self, key):
        return [m["text"] for m in self.ctrl.conversation(key)]


def pair(srv):
    a, b = Peer(srv), Peer(srv)
    assert a.register("alice", "Alice A")["ok"] and b.register("bob", "Bob B")["ok"]
    a.login("alice")
    b.login("bob")
    befriend(a, b, "alice", "bob")
    return a, b


def befriend(a, b, ua, ub):
    a.ctrl.send_friend_request(ub)
    b.wait(lambda: any(r["username"] == ua for r in b.ctrl.requests_in), what="request in")
    b.ctrl.respond_request(ua, True)
    a.wait(lambda: ub in a.ctrl.friends, what="friend added (a)")
    b.wait(lambda: ua in b.ctrl.friends, what="friend added (b)")


# --------------------------------------------------------------------------- #
def test_normalize_address():
    assert normalize_address("localhost:8765") == "localhost:8765"
    assert normalize_address("localhost") == "localhost:8765"
    assert normalize_address("192.168.0.4:9000") == "192.168.0.4:9000"
    assert normalize_address("  ") == ""
    with pytest.raises(ValueError):
        normalize_address("host:abc")


def test_unreachable_server_reports_connection_failed():
    p = Peer(type("S", (), {"address": "127.0.0.1:1"})())
    p.ctrl.start_auth("127.0.0.1:1", "login", generate_passphrase(), "alice", PW)
    p.wait(lambda: "auth_error" in p.topics(), what="auth_error")
    assert p.last("auth_error") == "Connection failed."


def test_client_side_validation_before_any_network_call():
    p = Peer(type("S", (), {"address": "x:1"})())
    p.ctrl.start_auth("localhost:1", "login", "", "alice", PW)
    assert p.last("auth_error") == "Enter your passphrase."
    p.ctrl.start_auth("localhost:1", "register", "", "alice", PW, fullname="A", confirm="other-pass")
    assert p.last("auth_error") == "Passwords do not match."
    p.ctrl.start_auth("not a host:1", "login", generate_passphrase(), "alice", PW)
    assert "Invalid server address" in p.last("auth_error")
    p.ctrl.start_auth("  ", "login", generate_passphrase(), "alice", PW)
    assert p.last("auth_error") == "Enter the server address."
    assert p.ctrl.network is None


def test_registration_creates_and_returns_a_passphrase(srv):
    p = Peer(srv)
    res = p.register("alice", "Alice A")
    assert res["ok"] and res["message"] == "Registration completed successfully."
    phrase = res["passphrase"]
    assert len(phrase.split("-")) == 5
    # the server stored only a public key - the passphrase itself is nowhere in the database
    row = srv.db.get_user("alice")
    assert phrase not in str(row) and row["public_key"]
    # every new account gets a different passphrase
    q = Peer(srv)
    assert q.register("bob")["passphrase"] != phrase


def test_failed_registration_shows_no_passphrase(srv):
    a = Peer(srv)
    a.register("alice")
    b = Peer(srv)
    res = b.register("alice")                          # username already taken
    assert not res["ok"] and "passphrase" not in res


def test_login_with_the_generated_passphrase_works_and_wrong_ones_fail(srv):
    p = Peer(srv)
    p.register("alice")
    wrong = Peer(srv)
    wrong.login("alice", passphrase=generate_passphrase())
    assert wrong.last("auth_error") == "That passphrase does not belong to this account."
    assert not wrong.ctrl.logged_in
    lower = Peer(srv)
    lower.login("alice", passphrase=p.passphrase.lower().replace("-", " "))   # forgiving input
    assert lower.ctrl.logged_in


def test_wrong_password_then_correct_password_on_same_connection(srv):
    p = Peer(srv)
    p.register("alice")
    p.log.clear()
    p.ctrl.start_auth(srv.address, "login", p.passphrase, "alice", "WrongPass1")
    p.wait(lambda: "auth_error" in p.topics(), what="bad login")
    assert p.last("auth_error") == "Invalid username or password."
    p.ctrl.start_auth(srv.address, "login", p.passphrase, "alice", PW)
    p.wait(lambda: p.ctrl.logged_in, what="good login")
    assert p.ctrl.me["username"] == "alice"


def test_encrypted_dm_roundtrip_between_controllers(srv):
    a, b = pair(srv)
    a.ctrl.open_conversation(dm_key("bob"))
    b.ctrl.open_conversation(dm_key("alice"))
    a.ctrl.send_text("Hello Bob, this is secret")
    b.wait(lambda: "Hello Bob, this is secret" in b.texts(dm_key("alice")), what="bob receives")
    b.ctrl.send_text("Hi Alice!")
    both = ["Hello Bob, this is secret", "Hi Alice!"]
    a.wait(lambda: a.texts(dm_key("bob")) == both, what="alice sees her own echo and bob's reply")
    assert a.texts(dm_key("bob")) == both
    assert not a.ctrl.unread and not b.ctrl.unread
    stored = srv.db.raw_dump("messages")
    assert stored and all("secret" not in r["encrypted_message"] for r in stored)


def test_users_with_different_passphrases_can_chat(srv):
    """Each user has their own passphrase; the key agreement makes them interoperate."""
    a, b = pair(srv)
    assert a.passphrase != b.passphrase
    a.ctrl.open_conversation(dm_key("bob"))
    a.ctrl.send_text("different passphrases, same conversation")
    b.wait(lambda: b.ctrl.conversation(dm_key("alice")), what="bob receives")
    assert b.ctrl.conversation(dm_key("alice"))[0]["ok"] is True


def test_unread_counter_for_unopened_conversation(srv):
    a, b = pair(srv)
    a.ctrl.open_conversation(dm_key("bob"))
    a.ctrl.send_text("ping 1")
    a.ctrl.send_text("ping 2")
    b.wait(lambda: b.ctrl.unread.get(dm_key("alice")) == 2, what="unread=2")
    b.ctrl.open_conversation(dm_key("alice"))
    assert b.ctrl.unread.get(dm_key("alice")) is None
    b.wait(lambda: b.texts(dm_key("alice")) == ["ping 1", "ping 2"], what="history merged without duplicates")


def test_offline_delivery_and_history_after_relogin(srv):
    a, b = pair(srv)
    b.ctrl.logout()
    a.wait(lambda: not a.ctrl.friends["bob"]["online"], what="bob offline")
    a.ctrl.open_conversation(dm_key("bob"))
    a.ctrl.send_text("while you were offline 1")
    a.ctrl.send_text("while you were offline 2")
    a.wait(lambda: len(a.texts(dm_key("bob"))) == 2, what="echo")
    b2 = Peer(srv)
    b2.login("bob", passphrase=b.passphrase)
    b2.wait(lambda: len(b2.texts(dm_key("alice"))) == 2, what="queued messages arrive on login")
    assert b2.texts(dm_key("alice")) == ["while you were offline 1", "while you were offline 2"]
    assert b2.ctrl.unread[dm_key("alice")] == 2
    b2.ctrl.logout()                                     # a brand-new login: history is decrypted from the server copy
    b3 = Peer(srv)
    b3.login("bob", passphrase=b.passphrase)
    b3.ctrl.open_conversation(dm_key("alice"))
    b3.wait(lambda: len(b3.texts(dm_key("alice"))) == 2, what="history")
    assert all(m["ok"] for m in b3.ctrl.conversation(dm_key("alice")))


def test_a_third_user_cannot_read_a_conversation(srv):
    """Carol is friends with Alice but cannot decrypt Alice-Bob messages she somehow obtains."""
    a, b = pair(srv)
    c = Peer(srv)
    c.register("carol")
    c.login("carol")
    befriend(c, a, "carol", "alice")
    a.ctrl.open_conversation(dm_key("bob"))
    a.ctrl.send_text("for bob only")
    b.wait(lambda: b.ctrl.conversation(dm_key("alice")), what="bob receives")
    stolen = srv.db.raw_dump("messages")[0]
    from shared.keys import open_dm
    from shared.crypto_utils import DecryptionError
    with pytest.raises(DecryptionError):
        open_dm(c.ctrl.identity, a.ctrl.identity.public_key_b64, "alice", "bob", stolen["encrypted_message"])


def test_group_chat_end_to_end(srv):
    a, b = pair(srv)
    c = Peer(srv)
    c.register("carol")
    c.login("carol")
    befriend(a, c, "alice", "carol")
    a.ctrl.create_group("Project Team", ["bob", "carol"])
    for p in (a, b, c):
        p.wait(lambda p=p: len(p.ctrl.groups) == 1, what="group created")
    gid = next(iter(a.ctrl.groups))
    a.ctrl.open_conversation(group_key(gid))
    b.ctrl.open_conversation(group_key(gid))
    a.ctrl.send_text("Meeting at 5pm")
    b.wait(lambda: b.texts(group_key(gid)) == ["Meeting at 5pm"], what="first group message")
    b.ctrl.send_text("I will be there")
    for p in (a, b):
        p.wait(lambda p=p: p.texts(group_key(gid)) == ["Meeting at 5pm", "I will be there"], what="group messages")
    c.wait(lambda: c.ctrl.unread.get(group_key(gid)) == 2, what="carol unread")
    c.ctrl.open_conversation(group_key(gid))
    c.wait(lambda: c.texts(group_key(gid)) == ["Meeting at 5pm", "I will be there"], what="carol decrypts")
    assert a.ctrl.title_for(group_key(gid)).startswith("Project Team")
    stored = srv.db.raw_dump("group_messages")
    assert stored and all("Meeting" not in r["encrypted_message"] for r in stored)
    a.ctrl.group_remove(gid, "carol")                    # admin removes carol -> she loses the group
    c.wait(lambda: gid not in c.ctrl.groups, what="removed")
    assert group_key(gid) not in c.ctrl.messages


def test_group_message_reaches_member_who_was_offline(srv):
    a, b = pair(srv)
    a.ctrl.create_group("G", ["bob"])
    for p in (a, b):
        p.wait(lambda p=p: len(p.ctrl.groups) == 1, what="group")
    gid = next(iter(a.ctrl.groups))
    b.ctrl.logout()
    a.ctrl.open_conversation(group_key(gid))
    a.ctrl.send_text("read me later")
    a.wait(lambda: a.texts(group_key(gid)) == ["read me later"], what="echo")
    b2 = Peer(srv)
    b2.login("bob", passphrase=b.passphrase)
    b2.wait(lambda: b2.texts(group_key(gid)) == ["read me later"], what="backlog decrypted")


def test_auto_reconnect_and_relogin_after_dropped_connection(srv):
    a, b = pair(srv)
    a.ctrl.network.max_backoff = 2
    old = srv.chat.sessions["alice"].conn
    old.close()                                           # server drops alice
    a.wait(lambda: any("reconnecting" in str(p) for t, p in a.log if t == "connection"),
           what="disconnect noticed")
    a.wait(lambda: "alice" in srv.chat.sessions and srv.chat.sessions["alice"].conn is not old,
           timeout=15, what="automatic re-login")
    a.ctrl.open_conversation(dm_key("bob"))
    a.ctrl.send_text("back online")
    b.ctrl.open_conversation(dm_key("alice"))
    b.wait(lambda: "back online" in b.texts(dm_key("alice")), what="message after reconnect")


def test_kicked_session_does_not_reconnect(srv):
    first = Peer(srv)
    first.register("alice")
    first.login("alice")
    second = Peer(srv)
    second.login("alice", passphrase=first.passphrase)
    first.wait(lambda: "kicked" in first.topics(), what="kicked")
    time.sleep(1.5)
    first.ctrl.poll()
    assert srv.chat.sessions["alice"].conn is not None
    assert first.ctrl.network is None                      # it did not fight back
    assert second.ctrl.logged_in


def test_logout_returns_to_clean_state(srv):
    a, b = pair(srv)
    a.ctrl.logout()
    assert not a.ctrl.logged_in and a.ctrl.friends == {} and a.ctrl.network is None
    assert a.ctrl.identity is None
    assert "logged_out" in a.topics()


def test_decrypt_failure_text_is_shown_for_garbage(srv):
    a, b = pair(srv)
    a.ctrl.open_conversation(dm_key("bob"))
    a.ctrl.network.send({"type": "send_dm", "to": "bob", "payload": "bm90LWEtcmVhbC1tZXNzYWdlLWF0LWFsbA=="})
    a.wait(lambda: a.ctrl.conversation(dm_key("bob")), what="echo of garbage")
    m = a.ctrl.conversation(dm_key("bob"))[0]
    assert m["text"] == DECRYPT_FAIL and m["ok"] is False


def test_client_keeps_the_connection_alive_with_pings(srv):
    import client.network as net
    old = net.PING_EVERY
    net.PING_EVERY = 1
    try:
        a, b = pair(srv)
        before = srv.chat.sessions["alice"].conn
        time.sleep(2.5)                                     # the server's pongs keep arriving; nothing breaks
        a.ctrl.poll()
        assert srv.chat.sessions["alice"].conn is before and a.ctrl.connected
    finally:
        net.PING_EVERY = old
