"""Profile features: display name, status line, profile picture, password change, chat-list previews."""
import base64

import pytest

from server.database import Database
from shared.keys import open_dm
from shared import protocol as P
from tests.helpers import (PASSWORD, ServerThread, TClient, IDENTITIES, befriend, enc_dm, enc_group,
                           login, make_user, register)

# Not a decodable picture, but it has the JPEG signature - all the server checks.
FAKE_JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"profile-picture-bytes" * 20).decode("ascii")
FAKE_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 50).decode("ascii")


@pytest.fixture
def srv(tmp_path):
    db = Database(sqlite_path=str(tmp_path / "profile.db"))
    server = ServerThread(db).start()
    yield server
    server.stop()
    db.close()


# --------------------------------------------------------------------------- #
# validation (pure functions)
# --------------------------------------------------------------------------- #
def test_validate_profile():
    assert P.validate_profile("Ada Lovelace", "Good vibes") is None
    assert P.validate_profile("  ", "") == "Full name is required."
    assert "at most" in P.validate_profile("x" * (P.MAX_FULLNAME + 1), "")
    assert "at most" in P.validate_profile("Ada", "x" * (P.MAX_BIO + 1))
    assert P.validate_profile("Ada", None) == "Status must be text."


def test_validate_avatar():
    assert P.validate_avatar("") is None                            # '' removes the picture
    assert P.validate_avatar(FAKE_JPEG) is None and P.validate_avatar(FAKE_PNG) is None
    assert P.validate_avatar("not base64 !!") == "Picture is invalid."
    assert P.validate_avatar(base64.b64encode(b"GIF89a....").decode()) == "Picture must be a JPEG or PNG image."
    assert P.validate_avatar("A" * (P.MAX_AVATAR_CHARS + 4)) == "That picture is too large."
    assert P.validate_avatar(123) == "Picture is invalid."


# --------------------------------------------------------------------------- #
# database
# --------------------------------------------------------------------------- #
def test_database_profile_roundtrip_and_old_database_upgrade(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)                  # a v5 database: no bio / avatar columns
    raw.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, "
                "password TEXT NOT NULL, fullname TEXT NOT NULL, public_key TEXT NOT NULL DEFAULT '', "
                "date_created DATETIME NOT NULL)")
    raw.execute("INSERT INTO users (username, password, fullname, public_key, date_created) "
                "VALUES ('old', 'x', 'Old User', 'k', '2026-01-01 00:00:00')")
    raw.commit()
    raw.close()
    db = Database(sqlite_path=path)
    try:
        user = db.get_user("old")
        assert user["bio"] == "" and user["avatar_ver"] == ""
        db.update_profile("old", "Older User", "hello")
        ver = db.set_avatar("old", FAKE_JPEG)
        assert ver and db.get_avatar("old") == (ver, FAKE_JPEG)
        assert db.get_user("old")["fullname"] == "Older User" and db.get_user("old")["bio"] == "hello"
        assert db.set_avatar("old", "") == "" and db.get_avatar("old") == ("", "")
        assert db.get_avatar("nobody") == ("", "")
    finally:
        db.close()


def test_picture_is_never_part_of_friend_lists(db):
    db.create_user("a", "h", "A", "k1")
    db.create_user("b", "h", "B", "k2")
    db.make_friends("a", "b")
    db.set_avatar("b", FAKE_JPEG)
    row = db.friends_of("a")[0]
    assert row["avatar_ver"] and "avatar" not in row        # only a version tag; the picture is fetched on demand


# --------------------------------------------------------------------------- #
# server: profile
# --------------------------------------------------------------------------- #
def test_profile_update_is_saved_and_broadcast_to_everyone_online(srv):
    alice, bob = make_user(srv.url, "alice", fullname="Alice A"), make_user(srv.url, "bob")
    alice.send(type="profile_update", fullname="  Alice Adamu  ", bio="  Good vibes.  ")
    res = alice.expect("profile_result")
    assert res["ok"] and res["fullname"] == "Alice Adamu" and res["bio"] == "Good vibes."
    seen = bob.expect("profile", username="alice")
    assert seen["fullname"] == "Alice Adamu" and seen["bio"] == "Good vibes."
    assert srv.db.get_user("alice")["fullname"] == "Alice Adamu"
    # presence after the change carries the new name too, and a fresh login returns it
    alice.send(type="logout")
    alice.expect("logged_out")
    again = TClient(srv.url)
    again.send(type="login", username="alice", password=PASSWORD, public_key=IDENTITIES["alice"].public_key_b64)
    user = again.expect("login_result")["user"]
    assert user["fullname"] == "Alice Adamu" and user["bio"] == "Good vibes."


@pytest.mark.parametrize("fullname,bio,fragment", [
    ("", "", "required"),
    ("x" * (P.MAX_FULLNAME + 1), "", "at most"),
    ("Alice", "y" * (P.MAX_BIO + 1), "at most"),
    (None, "", "required"),
])
def test_profile_update_rejects_bad_input_and_changes_nothing(srv, fullname, bio, fragment):
    alice = make_user(srv.url, "alice", fullname="Alice A")
    alice.send(type="profile_update", fullname=fullname, bio=bio)
    res = alice.expect("profile_result")
    assert not res["ok"] and fragment in res["message"]
    assert srv.db.get_user("alice")["fullname"] == "Alice A"


def test_avatar_upload_fetch_and_remove(srv):
    alice, bob = make_user(srv.url, "alice"), make_user(srv.url, "bob")
    alice.send(type="avatar_set", data=FAKE_JPEG)
    res = alice.expect("profile_result")
    assert res["ok"] and res["avatar_ver"]
    ver = res["avatar_ver"]
    assert bob.expect("profile", username="alice")["avatar_ver"] == ver
    bob.send(type="avatar_get", username="alice")
    got = bob.expect("avatar", username="alice")
    assert got["data"] == FAKE_JPEG and got["avatar_ver"] == ver
    alice.send(type="avatar_set", data="")                     # remove
    assert alice.expect("profile_result")["avatar_ver"] == ""
    bob.send(type="avatar_get", username="alice")
    assert bob.expect("avatar", username="alice")["data"] == ""


def test_avatar_rejects_garbage_and_oversize(srv):
    alice = make_user(srv.url, "alice")
    for bad in ("!!!", base64.b64encode(b"<svg onload=x>").decode(), "A" * (P.MAX_AVATAR_CHARS + 8), 42):
        alice.send(type="avatar_set", data=bad)
        assert not alice.expect("profile_result")["ok"]
    assert srv.db.get_avatar("alice") == ("", "")


def test_friends_see_the_picture_version_in_state_and_cards(srv):
    alice, bob = make_user(srv.url, "alice"), make_user(srv.url, "bob")
    bob.send(type="avatar_set", data=FAKE_PNG)
    ver = bob.expect("profile_result")["avatar_ver"]
    alice.expect("profile", username="bob")
    befriend(alice, bob)
    alice.send(type="logout")
    alice.expect("logged_out")
    again = login(srv.url, "alice")
    friend = again.state["friends"][0]
    assert friend["username"] == "bob" and friend["avatar_ver"] == ver and "data" not in friend


def test_avatar_get_for_unknown_user_is_empty_not_an_error(srv):
    alice = make_user(srv.url, "alice")
    alice.send(type="avatar_get", username="ghost")
    assert alice.expect("avatar", username="ghost")["data"] == ""


# --------------------------------------------------------------------------- #
# server: password change
# --------------------------------------------------------------------------- #
def test_password_change_requires_current_password(srv):
    alice = make_user(srv.url, "alice")
    alice.send(type="password_change", old="wrong-password", new="BrandNew123", confirm="BrandNew123")
    res = alice.expect("password_result")
    assert not res["ok"] and "current password" in res["message"]
    alice.send(type="password_change", old=PASSWORD, new="short", confirm="short")
    assert not alice.expect("password_result")["ok"]
    alice.send(type="password_change", old=PASSWORD, new="BrandNew123", confirm="Different123")
    assert alice.expect("password_result")["message"] == P.MSG_PASSWORD_MISMATCH
    alice.send(type="password_change", old=PASSWORD, new="BrandNew123", confirm="BrandNew123")
    assert alice.expect("password_result")["ok"]
    alice.send(type="logout")
    alice.expect("logged_out")
    c = TClient(srv.url)                                       # old password no longer works, new one does
    c.send(type="login", username="alice", password=PASSWORD, public_key=IDENTITIES["alice"].public_key_b64)
    assert not c.expect("login_result")["ok"]
    c.send(type="login", username="alice", password="BrandNew123", public_key=IDENTITIES["alice"].public_key_b64)
    assert c.expect("login_result")["ok"]


def test_password_change_failures_count_towards_the_lockout(srv):
    alice = make_user(srv.url, "alice")
    for _ in range(5):
        alice.send(type="password_change", old="nope", new="BrandNew123", confirm="BrandNew123")
        alice.expect("password_result")
    alice.send(type="password_change", old=PASSWORD, new="BrandNew123", confirm="BrandNew123")
    res = alice.expect("password_result")
    assert not res["ok"] and "Too many" in res["message"]


# --------------------------------------------------------------------------- #
# server: previews for the chat list
# --------------------------------------------------------------------------- #
def test_login_sends_the_newest_message_of_each_conversation_as_last_message(srv):
    alice, bob, carol = (make_user(srv.url, n) for n in ("alice", "bob", "carol"))
    befriend(alice, bob)
    befriend(alice, carol)
    for text in ("first", "second"):
        alice.send(type="send_dm", to="bob", payload=enc_dm("alice", "bob", text))
        alice.expect("message")
        bob.expect("message")
    alice.send(type="group_create", name="G", members=["bob", "carol"])
    gid = alice.expect("group_update")["group"]["group_id"]
    alice.send(type="send_group", group_id=gid, payload=enc_group("alice", gid, "hi group", ["alice", "bob", "carol"]))
    alice.expect("group_message")
    alice.send(type="logout")
    alice.expect("logged_out")

    again = TClient(srv.url)
    again.send(type="login", username="alice", password=PASSWORD, public_key=IDENTITIES["alice"].public_key_b64)
    again.expect("login_result")
    previews = [m for m in again.drain(0.5) if m["type"] == "last_message"]
    kinds = sorted(m["kind"] for m in previews)
    assert kinds == ["dm", "group"]                            # carol: no messages yet -> no preview
    dm = next(m for m in previews if m["kind"] == "dm")
    assert dm["to"] == "bob"
    assert open_dm(IDENTITIES["alice"], IDENTITIES["bob"].public_key_b64, "alice", "bob", dm["payload"]) == "second"
    assert next(m for m in previews if m["kind"] == "group")["group_id"] == gid


def test_previews_hide_group_messages_from_before_the_member_joined(srv):
    alice, bob, carol = (make_user(srv.url, n) for n in ("alice", "bob", "carol"))
    befriend(alice, bob)
    befriend(alice, carol)
    alice.send(type="group_create", name="G", members=["bob"])
    gid = alice.expect("group_update")["group"]["group_id"]
    alice.send(type="send_group", group_id=gid, payload=enc_group("alice", gid, "secret", ["alice", "bob"]))
    alice.expect("group_message")
    alice.send(type="group_add", group_id=gid, username="carol")
    alice.expect("group_update")
    carol.send(type="logout")
    carol.expect("logged_out")
    again = TClient(srv.url)
    again.send(type="login", username="carol", password=PASSWORD, public_key=IDENTITIES["carol"].public_key_b64)
    again.expect("login_result")
    assert [m for m in again.drain(0.5) if m["type"] == "last_message"] == []
