"""Phase 1 - database layer tests (SQLite)."""
import pytest

from server.database import UserExistsError


def make_users(db, *names):
    for n in names:
        db.create_user(n, "hash-" + n, n.title() + " Test", "KEY-" + n)


def test_create_and_get_user(db):
    u = db.create_user("alice", "$2b$hash", "Alice A")
    assert u["username"] == "alice"
    row = db.get_user("alice")
    assert row["fullname"] == "Alice A" and row["password"] == "$2b$hash"
    assert db.get_user("nobody") is None


def test_duplicate_username_rejected(db):
    db.create_user("alice", "h", "Alice")
    with pytest.raises(UserExistsError):
        db.create_user("alice", "h2", "Other")


def test_friend_request_flow(db):
    make_users(db, "alice", "bob")
    db.add_friend_request("alice", "bob")
    db.add_friend_request("alice", "bob")          # idempotent
    assert db.has_request("alice", "bob")
    assert [r["username"] for r in db.incoming_requests("bob")] == ["alice"]
    assert db.outgoing_requests("alice") == ["bob"]
    assert not db.are_friends("alice", "bob")
    db.make_friends("bob", "alice")
    assert db.are_friends("alice", "bob") and db.are_friends("bob", "alice")
    assert db.incoming_requests("bob") == []
    assert [f["username"] for f in db.friends_of("alice")] == ["bob"]
    assert [f["username"] for f in db.friends_of("bob")] == ["alice"]
    db.remove_friendship("alice", "bob")
    assert db.friends_of("alice") == []


def test_direct_messages_offline_queue_and_history(db):
    make_users(db, "alice", "bob")
    i1, ts = db.save_message("alice", "bob", "CIPHER1")
    i2, _ = db.save_message("alice", "bob", "CIPHER2")
    db.save_message("bob", "alice", "CIPHER3")
    pending = db.undelivered_messages("bob")
    assert [m["encrypted_message"] for m in pending] == ["CIPHER1", "CIPHER2"]
    assert len(ts) == 19                      # YYYY-MM-DD HH:MM:SS
    db.mark_delivered([i1, i2])
    assert db.undelivered_messages("bob") == []
    hist = db.direct_history("bob", "alice")
    assert [m["encrypted_message"] for m in hist] == ["CIPHER1", "CIPHER2", "CIPHER3"]
    assert len(db.direct_history("alice", "bob", limit=2)) == 2


def test_groups_membership_and_visibility(db):
    make_users(db, "alice", "bob", "carol")
    gid = db.create_group("Study", "alice", ["bob"])
    g = db.get_group(gid)
    assert g["admin"] == "alice" and {m["username"] for m in g["members"]} == {"alice", "bob"}
    m1, _ = db.save_group_message(gid, "alice", "G1")
    # bob is offline: backlog contains the message, cursor then moves
    assert [m["encrypted_message"] for m in db.group_backlog(gid, "bob")] == ["G1"]
    db.set_group_cursor(gid, "bob", m1)
    assert db.group_backlog(gid, "bob") == []
    # carol joins later and must NOT see G1
    db.add_group_member(gid, "carol")
    assert db.group_history(gid, "carol") == []
    assert db.group_backlog(gid, "carol") == []
    m2, _ = db.save_group_message(gid, "bob", "G2")
    assert [m["encrypted_message"] for m in db.group_history(gid, "carol")] == ["G2"]
    assert [m["encrypted_message"] for m in db.group_history(gid, "alice")] == ["G1", "G2"]
    assert db.is_member(gid, "carol")
    db.remove_group_member(gid, "carol")
    assert not db.is_member(gid, "carol")
    assert [g["group_id"] for g in db.groups_for_user("alice")] == [gid]
    db.set_group_admin(gid, "bob")
    assert db.get_group(gid)["admin"] == "bob"
    db.delete_group(gid)
    assert db.get_group(gid) is None


def test_cursor_never_moves_backwards(db):
    make_users(db, "alice", "bob")
    gid = db.create_group("G", "alice", ["bob"])
    a, _ = db.save_group_message(gid, "alice", "A")
    b, _ = db.save_group_message(gid, "alice", "B")
    db.set_group_cursor(gid, "bob", b)
    db.set_group_cursor(gid, "bob", a)           # older id: ignored
    assert db.group_backlog(gid, "bob") == []


def test_public_keys_are_stored_and_listed(db):
    make_users(db, "alice", "bob")
    assert db.get_user("alice")["public_key"] == "KEY-alice"
    db.make_friends("alice", "bob")
    assert db.friends_of("alice")[0]["public_key"] == "KEY-bob"
    gid = db.create_group("G", "alice", ["bob"])
    assert {m["username"]: m["public_key"] for m in db.get_group(gid)["members"]} == {
        "alice": "KEY-alice", "bob": "KEY-bob"}


def test_database_from_older_version_gets_public_key_column(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, "
                 "password TEXT NOT NULL, fullname TEXT NOT NULL, date_created DATETIME NOT NULL)")
    conn.execute("INSERT INTO users (username, password, fullname, date_created) VALUES ('old','h','Old','2026-01-01')")
    conn.commit()
    conn.close()
    from server.database import Database
    d = Database(sqlite_path=path)
    assert d.get_user("old")["public_key"] == ""
    d.close()


def test_database_is_safe_across_threads(db):
    import threading
    make_users(db, "alice", "bob")
    errors = []

    def work(n):
        try:
            for i in range(25):
                db.save_message("alice", "bob", f"c{n}-{i}")
        except Exception as exc:       # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(db.undelivered_messages("bob")) == 200
