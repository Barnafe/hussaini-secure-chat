"""Version 1.1 features, backend side: change password, delivery ticks, chat-list previews,
group rename / admin hand-over, typing indicator and the server-owner database tools."""
import sqlite3

import pytest

from server.database import Database
from shared import protocol as P
from tests.helpers import (PASSWORD, ServerThread, TClient, befriend, enc_dm, enc_group, login,
                           make_user, register, wait_until)


@pytest.fixture
def srv(tmp_path):
    db = Database(sqlite_path=str(tmp_path / "feat.db"))
    server = ServerThread(db).start()
    yield server
    server.stop()
    db.close()


# --------------------------------------------------------------------------- #
# database
# --------------------------------------------------------------------------- #
def test_db_password_rename_summaries_and_backup(tmp_path):
    db = Database(sqlite_path=str(tmp_path / "d.db"))
    db.create_user("alice", "old-hash", "Alice A", "PK1")
    db.create_user("bob", "hash", "Bob B", "PK2")
    db.update_password("alice", "new-hash")
    assert db.get_user("alice")["password"] == "new-hash"
    gid = db.create_group("Old name", "alice", ["bob"])
    db.rename_group(gid, "New name")
    assert db.get_group(gid)["name"] == "New name"
    db.make_friends("alice", "bob")
    db.save_message("alice", "bob", "CIPHER")
    rows = {r["username"]: r for r in db.user_summaries()}
    assert rows["alice"]["friends"] == 1 and rows["alice"]["messages_sent"] == 1 and rows["bob"]["messages_sent"] == 0
    copy = db.backup_to(str(tmp_path / "backups" / "copy.db"))
    with sqlite3.connect(copy) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    db.close()


def test_db_last_messages_for_chat_list_previews(tmp_path):
    db = Database(sqlite_path=str(tmp_path / "l.db"))
    for u in ("alice", "bob", "carol"):
        db.create_user(u, "h", u.title(), "PK")
    db.save_message("alice", "bob", "A1")
    db.save_message("bob", "alice", "B2")
    db.save_message("carol", "alice", "C1")
    last = db.last_direct_messages("alice")
    assert {p: r["encrypted_message"] for p, r in last.items()} == {"bob": "B2", "carol": "C1"}
    assert db.last_direct_messages("bob").keys() == {"alice"}
    gid = db.create_group("G", "alice", ["bob"])
    db.save_group_message(gid, "alice", "G1")
    db.add_group_member(gid, "carol")                       # carol joins after G1
    assert db.last_group_messages("bob")[gid]["encrypted_message"] == "G1"
    assert db.last_group_messages("carol") == {}            # nothing she is allowed to see yet
    db.save_group_message(gid, "bob", "G2")
    assert db.last_group_messages("carol")[gid]["encrypted_message"] == "G2"
    db.close()


# --------------------------------------------------------------------------- #
# change password
# --------------------------------------------------------------------------- #
def test_change_password_flow(srv):
    alice = make_user(srv.url, "alice")
    for payload, fragment in [(dict(current="WrongPass1", new="NewPassw0rd", confirm="NewPassw0rd"), "current password is incorrect"),
                              (dict(current=PASSWORD, new="short", confirm="short"), "at least"),
                              (dict(current=PASSWORD, new="NewPassw0rd", confirm="Different1"), "do not match"),
                              (dict(current=PASSWORD, new=PASSWORD, confirm=PASSWORD), "different")]:
        alice.send(type="change_password", **payload)
        res = alice.expect("password_changed")
        assert not res["ok"] and fragment in res["message"], res
    alice.send(type="change_password", current=PASSWORD, new="NewPassw0rd", confirm="NewPassw0rd")
    assert alice.expect("password_changed")["ok"]
    old = TClient(srv.url)
    old.send(type="login", username="alice", password=PASSWORD)
    assert not old.expect("login_result")["ok"]               # the old password no longer works
    assert login(srv.url, "alice", password="NewPassw0rd")    # the new one does


def test_change_password_wrong_current_counts_towards_lockout(tmp_path):
    db = Database(sqlite_path=str(tmp_path / "lock.db"))
    server = ServerThread(db, max_failed=3, lockout_seconds=60).start()
    try:
        alice = make_user(server.url, "alice")
        for _ in range(3):
            alice.send(type="change_password", current="WrongPass1", new="NewPassw0rd", confirm="NewPassw0rd")
            assert not alice.expect("password_changed")["ok"]
        alice.send(type="change_password", current=PASSWORD, new="NewPassw0rd", confirm="NewPassw0rd")
        res = alice.expect("password_changed")
        assert not res["ok"] and "Too many" in res["message"]
    finally:
        server.stop()
        db.close()


# --------------------------------------------------------------------------- #
# delivery ticks
# --------------------------------------------------------------------------- #
def test_echo_says_delivered_when_recipient_is_online(srv):
    alice, bob = make_user(srv.url, "alice"), make_user(srv.url, "bob")
    befriend(alice, bob)
    alice.send(type="send_dm", to="bob", payload=enc_dm("alice", "bob", "hi"))
    assert alice.expect("message")["delivered"] is True


def test_offline_message_gets_second_tick_when_recipient_logs_in(srv):
    alice, bob = make_user(srv.url, "alice"), make_user(srv.url, "bob")
    befriend(alice, bob)
    bob.send(type="logout")
    bob.expect("logged_out")
    alice.send(type="send_dm", to="bob", payload=enc_dm("alice", "bob", "while you were out"))
    echo = alice.expect("message")
    assert echo["delivered"] is False                          # one tick only
    login(srv.url, "bob")
    tick = alice.expect("delivered")                           # bob came online -> two ticks
    assert tick["peer"] == "bob" and tick["ids"] == [echo["id"]]


def test_history_carries_the_delivered_flag(srv):
    alice, bob = make_user(srv.url, "alice"), make_user(srv.url, "bob")
    befriend(alice, bob)
    alice.send(type="send_dm", to="bob", payload=enc_dm("alice", "bob", "one"))
    alice.expect("message")
    bob.send(type="logout")
    bob.expect("logged_out")
    alice.send(type="send_dm", to="bob", payload=enc_dm("alice", "bob", "two"))
    alice.expect("message")
    alice.send(type="history", kind="dm", **{"with": "bob"})
    flags = [m["delivered"] for m in alice.expect("history")["messages"]]
    assert flags == [True, False]


# --------------------------------------------------------------------------- #
# chat-list previews (the state message carries the newest ciphertext of each chat)
# --------------------------------------------------------------------------- #
def test_state_contains_last_message_of_each_conversation(srv):
    alice, bob = make_user(srv.url, "alice"), make_user(srv.url, "bob")
    befriend(alice, bob)
    p1, p2 = enc_dm("alice", "bob", "first"), enc_dm("bob", "alice", "second")
    alice.send(type="send_dm", to="bob", payload=p1)
    alice.expect("message")
    bob.send(type="send_dm", to="alice", payload=p2)
    alice.expect("message")
    alice.send(type="logout")
    alice.expect("logged_out")
    again = login(srv.url, "alice")
    last = again.state["last"]["dm"]["bob"]
    assert last["payload"] == p2 and last["from"] == "bob" and last["to"] == "alice"
    assert again.state["last"]["group"] == {}


# --------------------------------------------------------------------------- #
# group rename and admin hand-over
# --------------------------------------------------------------------------- #
def make_group(srv):
    alice, bob, carol = (make_user(srv.url, n) for n in ("alice", "bob", "carol"))
    befriend(alice, bob)
    befriend(alice, carol)
    alice.send(type="group_create", name="Study", members=["bob"])
    gid = alice.expect("group_update")["group"]["group_id"]
    bob.expect("group_update")
    return alice, bob, carol, gid


def test_group_rename_rules(srv):
    alice, bob, carol, gid = make_group(srv)
    bob.send(type="group_rename", group_id=gid, name="Hacked")
    assert "admin" in bob.expect("error")["message"]
    alice.send(type="group_rename", group_id=gid, name="   ")
    assert "required" in alice.expect("error")["message"]
    alice.send(type="group_rename", group_id=gid, name="Final Year Project")
    for c in (alice, bob):
        assert c.expect("group_update")["group"]["name"] == "Final Year Project"
    carol.send(type="group_rename", group_id=gid, name="Outsider")           # not a member
    assert "not a member" in carol.expect("error")["message"]


def test_group_admin_hand_over(srv):
    alice, bob, carol, gid = make_group(srv)
    bob.send(type="group_transfer", group_id=gid, username="bob")
    assert "admin" in bob.expect("error")["message"]                          # bob is not admin yet
    alice.send(type="group_transfer", group_id=gid, username="alice")
    assert "another member" in alice.expect("error")["message"]
    alice.send(type="group_transfer", group_id=gid, username="carol")        # carol is not in the group
    assert "another member" in alice.expect("error")["message"]
    alice.send(type="group_transfer", group_id=gid, username="bob")
    assert bob.expect("group_update")["group"]["admin"] == "bob"
    alice.drain(0.3)
    alice.send(type="group_rename", group_id=gid, name="Mine")               # alice lost the power
    assert "admin" in alice.expect("error")["message"]
    bob.send(type="group_rename", group_id=gid, name="Bob's group")
    assert bob.expect("group_update")["group"]["name"] == "Bob's group"


# --------------------------------------------------------------------------- #
# typing indicator
# --------------------------------------------------------------------------- #
def test_typing_is_relayed_to_friends_only(srv):
    alice, bob, carol = (make_user(srv.url, n) for n in ("alice", "bob", "carol"))
    befriend(alice, bob)
    alice.send(type="typing", to="bob")
    assert bob.expect("typing")["from"] == "alice"
    alice.send(type="typing", to="carol")                                     # not friends: nothing happens
    assert [m for m in carol.drain(0.4) if m["type"] == "typing"] == []
    assert [m for m in alice.drain(0.2) if m["type"] == "error"] == []        # and no error either


def test_group_typing_goes_to_other_members_only(srv):
    alice, bob, carol, gid = make_group(srv)
    bob.send(type="typing", group_id=gid)
    got = alice.expect("typing")
    assert got["from"] == "bob" and got["group_id"] == gid
    assert [m for m in bob.drain(0.4) if m["type"] == "typing"] == []         # not echoed to the typist
    carol.send(type="typing", group_id=gid)                                   # outsider: ignored
    assert [m for m in alice.drain(0.4) if m["type"] == "typing"] == []


def test_typing_flood_is_dropped_silently(srv):
    alice, bob = make_user(srv.url, "alice"), make_user(srv.url, "bob")
    befriend(alice, bob)
    for _ in range(40):
        alice.send(type="typing", to="bob")
    got = [m for m in bob.drain(1.0) if m["type"] == "typing"]
    assert 0 < len(got) <= 20
