"""Phase 5 - security tests.

These back up the security objectives of Chapter 1: confidentiality (nobody on the wire
or in the database can read messages), integrity (tampering and forgery are detected),
authentication, and resistance to common attacks.
"""
import sqlite3

import pytest

from server.database import Database
from shared import protocol as P
from shared.crypto_utils import DecryptionError
from shared.keys import Identity, open_dm, open_group
from tests.helpers import (IDENTITIES, closed_within, PASSPHRASES, PASSWORD, ServerThread, Sniffer, TClient, befriend,
                           enc_dm, enc_group, login, make_user, register)

SECRET = "the exam is on Monday and the answer is 42"


@pytest.fixture
def srv(tmp_path):
    db = Database(sqlite_path=str(tmp_path / "sec.db"))
    server = ServerThread(db).start()
    yield server
    server.stop()
    db.close()


def test_wire_traffic_contains_no_plaintext(srv):
    """A packet sniffer between clients and server sees only ciphertext."""
    sniffer = Sniffer(srv.port)
    try:
        register(srv.url, "alice")
        register(srv.url, "bob")
        alice, bob = login(sniffer.url, "alice"), login(sniffer.url, "bob")
        befriend(alice, bob)
        payload = enc_dm("alice", "bob", SECRET)
        alice.send(type="send_dm", to="bob", payload=payload)
        got = bob.expect("message")
        assert open_dm(IDENTITIES["bob"], IDENTITIES["alice"].public_key_b64, "alice", "bob",
                       got["payload"]) == SECRET
        wire = bytes(sniffer.captured)
        assert payload.encode() in wire                 # the ciphertext did cross the wire
        for needle in (SECRET, "exam", "Monday", "answer is 42", PASSPHRASES["alice"],
                       PASSPHRASES["bob"], PASSPHRASES["alice"].replace("-", "")):
            assert needle.encode() not in wire, needle
    finally:
        sniffer.stop()


def test_database_holds_only_ciphertext_and_password_hashes(srv):
    alice, bob = make_user(srv.url, "alice"), make_user(srv.url, "bob")
    befriend(alice, bob)
    alice.send(type="group_create", name="G", members=["bob"])
    gid = alice.expect("group_update")["group"]["group_id"]
    alice.send(type="send_dm", to="bob", payload=enc_dm("alice", "bob", SECRET))
    alice.send(type="send_group", group_id=gid, payload=enc_group("alice", gid, SECRET, ["alice", "bob"]))
    bob.expect("message")
    bob.expect("group_message")
    for table in ("messages", "group_messages"):
        rows = srv.db.raw_dump(table)
        assert rows
        for r in rows:
            assert SECRET not in r["encrypted_message"] and "exam" not in r["encrypted_message"]
    for u in srv.db.raw_dump("users"):
        assert u["password"].startswith("$2") and len(u["password"]) == 60     # bcrypt
        assert PASSWORD not in u["password"]
        assert u["public_key"] == IDENTITIES[u["username"]].public_key_b64     # a public key, nothing secret
        assert PASSPHRASES[u["username"]] not in str(u)


def test_tampered_ciphertext_in_database_is_detected(srv, tmp_path):
    alice, bob = make_user(srv.url, "alice"), make_user(srv.url, "bob")
    befriend(alice, bob)
    alice.send(type="send_dm", to="bob", payload=enc_dm("alice", "bob", SECRET))
    bob.expect("message")
    # an attacker with database access flips a few characters of the stored ciphertext
    with sqlite3.connect(srv.db._path) as conn:
        original = conn.execute("SELECT encrypted_message FROM messages").fetchone()[0]
        mid = len(original) // 2
        flipped = original[:mid] + ("A" if original[mid] != "A" else "B") + original[mid + 1:]
        conn.execute("UPDATE messages SET encrypted_message = ?", (flipped,))
    alice.send(type="history", kind="dm", **{"with": "bob"})
    stored = alice.expect("history")["messages"][0]["payload"]
    assert stored != original
    with pytest.raises(DecryptionError):
        open_dm(IDENTITIES["bob"], IDENTITIES["alice"].public_key_b64, "alice", "bob", stored)


def test_ciphertext_replayed_by_another_user_fails_authentication(srv):
    """Mallory copies Alice's ciphertext and sends it to Bob as her own."""
    alice, bob, mallory = (make_user(srv.url, n) for n in ("alice", "bob", "mallory"))
    befriend(alice, bob)
    befriend(mallory, bob)
    alice.send(type="send_dm", to="bob", payload=enc_dm("alice", "bob", "transfer 1000 to alice"))
    stolen = bob.expect("message")["payload"]
    mallory.send(type="send_dm", to="bob", payload=stolen)
    forged = bob.expect("message")
    assert forged["from"] == "mallory"
    with pytest.raises(DecryptionError):
        open_dm(IDENTITIES["bob"], IDENTITIES["mallory"].public_key_b64, "mallory", "bob",
                forged["payload"])


def test_sender_cannot_be_spoofed(srv):
    alice, bob, carol = (make_user(srv.url, n) for n in ("alice", "bob", "carol"))
    befriend(alice, bob)
    alice.send(type="send_dm", to="bob", payload=enc_dm("alice", "bob", "hi"), **{"from": "carol"})
    assert bob.expect("message")["from"] == "alice"            # the server uses the session identity


def test_non_friends_cannot_read_history_or_message(srv):
    alice, bob, mallory = (make_user(srv.url, n) for n in ("alice", "bob", "mallory"))
    befriend(alice, bob)
    alice.send(type="send_dm", to="bob", payload=enc_dm("alice", "bob", SECRET))
    bob.expect("message")
    mallory.send(type="history", kind="dm", **{"with": "alice"})
    assert "friends" in mallory.expect("error")["message"]
    mallory.send(type="send_dm", to="alice", payload=enc_dm("mallory", "alice", "hi"))
    assert "friends" in mallory.expect("error")["message"]


def test_sql_injection_is_harmless(srv):
    evil = "alice'; DROP TABLE users;--"
    res = register(srv.url, evil)
    assert not res["ok"]                                          # fails validation
    register(srv.url, "alice")
    c = TClient(srv.url)
    for attempt in ("alice' OR '1'='1", "' OR 1=1 --", "alice'--"):
        c.send(type="login", username=attempt, password="' OR '1'='1")
        assert c.expect("login_result")["message"] == P.MSG_LOGIN_BAD
    alice = login(srv.url, "alice")
    alice.send(type="friend_request", to="' OR 1=1 --")
    assert alice.expect("error")["message"] == "User not found."
    bob = make_user(srv.url, "bob")
    befriend(alice, bob)
    nasty = "'); DROP TABLE messages; --"
    alice.send(type="send_dm", to="bob", payload=nasty)           # treated as data
    assert bob.expect("message")["payload"] == nasty
    assert srv.db.get_user("alice") is not None
    assert len(srv.db.raw_dump("messages")) == 1


def test_oversized_frame_closes_connection(srv):
    c = make_user(srv.url, "alice")
    c.sock.sendall(P.HEADER.pack(P.MAX_FRAME + 1000) + b"x" * 500)
    assert "too large" in c.expect("error")["message"]
    assert closed_within(c)


def test_non_text_frames_rejected(srv):
    c = TClient(srv.url)
    c.send_raw(b"\x00\x01\x02\xff")
    assert "Malformed" in c.expect("error")["message"]


def test_half_open_garbage_does_not_crash_the_server(srv):
    import socket
    host, port = "127.0.0.1", srv.port
    for junk in (b"GET / HTTP/1.1\r\n\r\n", b"\x00\x00", b"\xff\xff\xff\xff", b""):
        s = socket.create_connection((host, port))
        s.sendall(junk)
        s.close()
    c = make_user(srv.url, "alice")                      # still serving
    c.send(type="friend_request", to="nobody")
    assert c.expect("error")["message"] == "User not found."


def test_passwords_are_never_echoed_or_logged(srv, caplog):
    caplog.set_level("DEBUG", logger="chat.server")
    register(srv.url, "alice", password="S3cretPassw0rd!")
    login(srv.url, "alice", password="S3cretPassw0rd!")
    assert "S3cretPassw0rd!" not in caplog.text


def test_group_messages_cannot_be_posted_by_non_members(srv):
    alice, bob, mallory = (make_user(srv.url, n) for n in ("alice", "bob", "mallory"))
    befriend(alice, bob)
    alice.send(type="group_create", name="Private", members=["bob"])
    gid = alice.expect("group_update")["group"]["group_id"]
    mallory.send(type="send_group", group_id=gid, payload=enc_group("mallory", gid, "let me in", ["mallory"]))
    assert "not a member" in mallory.expect("error")["message"]
    mallory.send(type="group_add", group_id=gid, username="mallory")
    assert "not a member" in mallory.expect("error")["message"]


def test_group_message_is_unreadable_for_a_non_member_with_database_access(srv):
    alice, bob, carol = (make_user(srv.url, n) for n in ("alice", "bob", "carol"))
    befriend(alice, bob)
    alice.send(type="group_create", name="Closed", members=["bob"])
    gid = alice.expect("group_update")["group"]["group_id"]
    alice.send(type="send_group", group_id=gid, payload=enc_group("alice", gid, SECRET, ["alice", "bob"]))
    bob.expect("group_message")
    stored = srv.db.raw_dump("group_messages")[0]["encrypted_message"]
    assert open_group(IDENTITIES["bob"], gid, "alice", stored, IDENTITIES["alice"].public_key_b64) == SECRET
    with pytest.raises(DecryptionError):                  # carol (or the server operator) cannot
        open_group(IDENTITIES["carol"], gid, "alice", stored, IDENTITIES["alice"].public_key_b64)


def test_server_cannot_decrypt_with_what_it_stores(srv):
    """The server only holds public keys; a guessed passphrase does not produce the right key pair."""
    alice, bob = make_user(srv.url, "alice"), make_user(srv.url, "bob")
    befriend(alice, bob)
    alice.send(type="send_dm", to="bob", payload=enc_dm("alice", "bob", SECRET))
    bob.expect("message")
    row = srv.db.raw_dump("messages")[0]
    operator = Identity("bob", "AAAA-BBBB-CCCC-DDDD-EEEE", 1000)
    assert operator.public_key_b64 != srv.db.get_user("bob")["public_key"]
    with pytest.raises(DecryptionError):
        open_dm(operator, srv.db.get_user("alice")["public_key"], "alice", "bob", row["encrypted_message"])
