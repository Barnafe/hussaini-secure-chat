"""Phase 2 - server integration tests (real TCP server, real database)."""
import pytest

from server.database import Database
from shared import protocol as P
from shared.crypto_utils import hash_password
from shared.keys import Identity
from tests.helpers import (FAKE_KEY, closed_within, IDENTITIES, PASSWORD, ServerThread, TClient, befriend, enc_dm,
                           enc_group, login, make_user, register, wait_until)


@pytest.fixture
def srv(tmp_path):
    db = Database(sqlite_path=str(tmp_path / "srv.db"))
    server = ServerThread(db).start()
    yield server
    server.stop()
    db.close()


# --------------------------------------------------------------------------- #
# registration & login
# --------------------------------------------------------------------------- #
def test_ping_is_answered_before_and_after_login(srv):
    c = TClient(srv.url)
    c.send(type="ping")
    c.expect("pong")
    alice = make_user(srv.url, "alice")
    alice.send(type="ping")
    alice.expect("pong")


def test_registration_success_and_messages(srv):
    res = register(srv.url, "alice", "Alice Adamu")
    assert res["ok"] and res["message"] == P.MSG_REGISTERED


def test_duplicate_username_is_rejected_case_insensitively(srv):
    register(srv.url, "alice")
    res = register(srv.url, "ALICE")
    assert not res["ok"] and res["message"] == P.MSG_USER_EXISTS


@pytest.mark.parametrize("kwargs,fragment", [
    (dict(username="ab"), "Username"),
    (dict(username="bad name!"), "Username"),
    (dict(password="short", confirm="short"), "at least"),
    (dict(password="Passw0rd!", confirm="Different1!"), "do not match"),
    (dict(fullname="   "), "Full name"),
])
def test_registration_validation(srv, kwargs, fragment):
    data = dict(username="validuser", password=PASSWORD, confirm=PASSWORD, fullname="Valid User",
                public_key=FAKE_KEY)
    data.update(kwargs)
    c = TClient(srv.url)
    c.send(type="register", **data)
    res = c.expect("register_result")
    assert not res["ok"] and fragment in res["message"]


@pytest.mark.parametrize("key", [None, "", "short", "!" * 44])
def test_registration_requires_a_valid_public_key(srv, key):
    c = TClient(srv.url)
    c.send(type="register", username="alice", password=PASSWORD, confirm=PASSWORD,
           fullname="Alice", public_key=key)
    res = c.expect("register_result")
    assert not res["ok"] and "key" in res["message"]
    assert srv.db.get_user("alice") is None


def test_server_stores_only_the_public_key(srv):
    register(srv.url, "alice")
    assert srv.db.get_user("alice")["public_key"] == IDENTITIES["alice"].public_key_b64


def test_login_with_wrong_passphrase_is_refused(srv):
    register(srv.url, "alice")
    other = Identity("alice", "SOME-OTHER-PHRASE-HERE", 1000)
    c = TClient(srv.url)
    c.send(type="login", username="alice", password=PASSWORD, public_key=other.public_key_b64)
    res = c.expect("login_result")
    assert not res["ok"] and res["message"] == P.MSG_PASSPHRASE_BAD
    c.send(type="login", username="alice", password=PASSWORD)            # no key at all
    assert c.expect("login_result")["message"] == P.MSG_PASSPHRASE_BAD
    assert "alice" not in srv.chat.sessions


def test_wrong_passphrase_message_is_not_shown_for_a_wrong_password(srv):
    """Nobody can probe passphrases without knowing the password."""
    register(srv.url, "alice")
    c = TClient(srv.url)
    c.send(type="login", username="alice", password="WrongPass1", public_key=FAKE_KEY)
    assert c.expect("login_result")["message"] == P.MSG_LOGIN_BAD


def test_account_without_key_cannot_log_in(srv):
    srv.db.create_user("legacy", hash_password(PASSWORD, 4), "Legacy")
    c = TClient(srv.url)
    c.send(type="login", username="legacy", password=PASSWORD, public_key=FAKE_KEY)
    assert c.expect("login_result")["message"] == P.MSG_NO_KEY


def test_login_success_returns_user_and_state(srv):
    register(srv.url, "alice", "Alice Adamu")
    c = TClient(srv.url)
    c.send(type="login", username="Alice", password=PASSWORD,     # username is case-insensitive
           public_key=IDENTITIES["alice"].public_key_b64)
    res = c.expect("login_result")
    assert res["ok"] and res["message"] == P.MSG_LOGIN_OK
    assert res["user"] == {"username": "alice", "fullname": "Alice Adamu"}
    state = c.expect("state")
    assert state["friends"] == [] and state["groups"] == []


def test_login_wrong_password_and_unknown_user_same_message(srv):
    register(srv.url, "alice")
    for user, pw in [("alice", "WrongPass1"), ("nobody", "WrongPass1")]:
        c = TClient(srv.url)
        c.send(type="login", username=user, password=pw, public_key=FAKE_KEY)
        res = c.expect("login_result")
        assert not res["ok"] and res["message"] == P.MSG_LOGIN_BAD


def test_login_lockout_after_repeated_failures(tmp_path):
    db = Database(sqlite_path=str(tmp_path / "l.db"))
    server = ServerThread(db, max_failed=3, lockout_seconds=60).start()
    try:
        register(server.url, "alice")
        c = TClient(server.url)
        for _ in range(3):
            c.send(type="login", username="alice", password="bad-password", public_key=FAKE_KEY)
            assert not c.expect("login_result")["ok"]
        c.send(type="login", username="alice", password=PASSWORD,      # even the right password
               public_key=IDENTITIES["alice"].public_key_b64)
        res = c.expect("login_result")
        assert not res["ok"] and "Too many" in res["message"]
    finally:
        server.stop()


def test_requests_require_login(srv):
    c = TClient(srv.url)
    c.send(type="send_dm", to="bob", payload="x")
    err = c.expect("error")
    assert err["code"] == "auth_required"
    c.send(type="friend_request", to="bob")
    assert c.expect("error")["code"] == "auth_required"


def test_malformed_input_is_survivable(srv):
    c = make_user(srv.url, "alice")
    for raw in [b"not json", b"[]", b'{"no_type": 1}', b'{"type": 5}', b'{"type": "bogus"}', b"\xff\xfe"]:
        c.send_raw(raw)
        assert c.expect("error")
    c.send(type="friend_request", to="nobody")
    assert c.expect("error")["message"] == "User not found."     # still alive


def test_second_login_kicks_first_session(srv):
    first = make_user(srv.url, "alice")
    second = login(srv.url, "alice")
    assert "elsewhere" in first.expect("kicked")["message"]
    second.send(type="friend_request", to="alice")
    assert second.expect("error")["message"] == "You cannot add yourself."


def test_logout_ends_session(srv):
    c = make_user(srv.url, "alice")
    c.send(type="logout")
    c.expect("logged_out")
    assert wait_until(lambda: "alice" not in srv.chat.sessions)       # session removed
    d = TClient(srv.url)
    d.send(type="send_dm", to="x", payload="y")
    assert d.expect("error")["code"] == "auth_required"


# --------------------------------------------------------------------------- #
# presence & friends
# --------------------------------------------------------------------------- #
def test_presence_and_online_list(srv):
    alice = make_user(srv.url, "alice")
    bob = make_user(srv.url, "bob")
    assert [u["username"] for u in bob.state["online"]] == ["alice"]
    p = alice.expect("presence", username="bob")
    assert p["online"] is True
    bob.send(type="logout")
    assert alice.expect("presence", username="bob")["online"] is False


def test_friend_request_accept_flow(srv):
    alice, bob = make_user(srv.url, "alice"), make_user(srv.url, "bob")
    alice.send(type="friend_request", to="bob")
    alice.expect("request_sent")
    req = bob.expect("friend_request_in")
    assert req["username"] == "alice"
    bob.send(type="friend_respond", **{"from": "alice"}, accept=True)
    assert alice.expect("friend_added")["user"]["username"] == "bob"
    added = bob.expect("friend_added")
    assert added["user"]["username"] == "alice" and added["user"]["online"] is True
    assert added["user"]["public_key"] == IDENTITIES["alice"].public_key_b64


def test_friend_request_decline(srv):
    alice, bob = make_user(srv.url, "alice"), make_user(srv.url, "bob")
    alice.send(type="friend_request", to="bob")
    bob.expect("friend_request_in")
    bob.send(type="friend_respond", **{"from": "alice"}, accept=False)
    bob.expect("request_cleared")
    alice.send(type="send_dm", to="bob", payload=enc_dm("alice", "bob", "hi"))
    assert "friends" in alice.expect("error")["message"]


def test_mutual_requests_auto_accept(srv):
    alice, bob = make_user(srv.url, "alice"), make_user(srv.url, "bob")
    alice.send(type="friend_request", to="bob")
    bob.expect("friend_request_in")
    bob.send(type="friend_request", to="alice")
    bob.expect("friend_added")
    alice.expect("friend_added")


def test_pending_request_survives_offline_and_appears_in_state(srv):
    alice = make_user(srv.url, "alice")
    register(srv.url, "bob")
    alice.send(type="friend_request", to="bob")
    alice.expect("request_sent")
    bob = login(srv.url, "bob")
    assert [r["username"] for r in bob.state["requests_in"]] == ["alice"]


def test_friend_remove(srv):
    alice, bob = make_user(srv.url, "alice"), make_user(srv.url, "bob")
    befriend(alice, bob)
    alice.send(type="friend_remove", user="bob")
    assert alice.expect("friend_removed")["username"] == "bob"
    assert bob.expect("friend_removed")["username"] == "alice"


# --------------------------------------------------------------------------- #
# direct messages
# --------------------------------------------------------------------------- #
def test_dm_delivered_live_to_online_friend_and_echoed(srv):
    alice, bob = make_user(srv.url, "alice"), make_user(srv.url, "bob")
    befriend(alice, bob)
    payload = enc_dm("alice", "bob", "hello bob")
    alice.send(type="send_dm", to="bob", payload=payload)
    got = bob.expect("message")
    assert got["from"] == "alice" and got["payload"] == payload and got["queued"] is False
    echo = alice.expect("message")
    assert echo["id"] == got["id"] and echo["to"] == "bob"


def test_dm_to_non_friend_rejected(srv):
    alice, bob = make_user(srv.url, "alice"), make_user(srv.url, "bob")
    alice.send(type="send_dm", to="bob", payload=enc_dm("alice", "bob", "hi"))
    assert "friends" in alice.expect("error")["message"]


def test_offline_message_delivered_only_when_recipient_logs_in(srv):
    alice, bob = make_user(srv.url, "alice"), make_user(srv.url, "bob")
    befriend(alice, bob)
    bob.send(type="logout")
    bob.expect("logged_out")
    payloads = [enc_dm("alice", "bob", f"offline {i}") for i in range(3)]
    for p in payloads:
        alice.send(type="send_dm", to="bob", payload=p)
        alice.expect("message")
    assert len(srv.db.undelivered_messages("bob")) == 3          # queued, not delivered
    bob2 = TClient(srv.url)
    bob2.send(type="login", username="bob", password=PASSWORD, public_key=IDENTITIES["bob"].public_key_b64)
    bob2.expect("login_result")
    got = [bob2.expect("message") for _ in range(3)]
    assert [g["payload"] for g in got] == payloads and all(g["queued"] for g in got)
    assert wait_until(lambda: srv.db.undelivered_messages("bob") == [])   # now marked delivered
    bob2.send(type="logout")
    bob2.expect("logged_out")
    bob3 = login(srv.url, "bob")                                 # nothing re-delivered
    assert [m for m in bob3.drain(0.5) if m["type"] == "message"] == []


def test_dm_history_returns_ciphertext_in_order(srv):
    alice, bob = make_user(srv.url, "alice"), make_user(srv.url, "bob")
    befriend(alice, bob)
    sent = []
    for i in range(4):
        sender, to = (alice, "bob") if i % 2 == 0 else (bob, "alice")
        p = enc_dm(sender.username, to, f"msg {i}")
        sent.append(p)
        sender.send(type="send_dm", to=to, payload=p)
        alice.expect("message")
        bob.expect("message")
    alice.send(type="history", kind="dm", **{"with": "bob"})
    hist = alice.expect("history")
    assert [m["payload"] for m in hist["messages"]] == sent


def test_empty_and_oversized_payloads_rejected(srv):
    alice, bob = make_user(srv.url, "alice"), make_user(srv.url, "bob")
    befriend(alice, bob)
    alice.send(type="send_dm", to="bob", payload="")
    assert alice.expect("error")["message"] == "Message is empty."
    alice.send(type="send_dm", to="bob", payload="A" * (P.MAX_PAYLOAD + 1))
    assert alice.expect("error")["message"] == "Message is too long."


def test_flood_control(tmp_path):
    db = Database(sqlite_path=str(tmp_path / "f.db"))
    server = ServerThread(db, rate_limit=(5, 10)).start()
    try:
        alice, bob = make_user(server.url, "alice"), make_user(server.url, "bob")
        befriend(alice, bob)
        for i in range(8):
            alice.send(type="send_dm", to="bob", payload=enc_dm("alice", "bob", str(i)))
        errors = [m for m in alice.drain(1.0) if m["type"] == "error"]
        assert errors and "too fast" in errors[0]["message"]
    finally:
        server.stop()


# --------------------------------------------------------------------------- #
# groups
# --------------------------------------------------------------------------- #
def three_friends(srv):
    alice, bob, carol = (make_user(srv.url, n) for n in ("alice", "bob", "carol"))
    befriend(alice, bob)
    befriend(alice, carol)
    return alice, bob, carol


def test_group_create_and_group_message(srv):
    alice, bob, carol = three_friends(srv)
    alice.send(type="group_create", name="Study Group", members=["bob", "carol"])
    for c in (alice, bob, carol):
        g = c.expect("group_update")["group"]
        assert g["name"] == "Study Group" and g["admin"] == "alice"
        assert {m["username"] for m in g["members"]} == {"alice", "bob", "carol"}
    gid = g["group_id"]
    p = enc_group("bob", gid, "hello team", ["alice", "bob", "carol"])
    bob.send(type="send_group", group_id=gid, payload=p)
    for c in (alice, bob, carol):
        m = c.expect("group_message")
        assert m["from"] == "bob" and m["payload"] == p and m["group_id"] == gid


def test_group_requires_friends_and_at_least_one_member(srv):
    alice, bob, _ = three_friends(srv)
    dave = make_user(srv.url, "dave")
    alice.send(type="group_create", name="X", members=["dave"])
    assert "not in your friends" in alice.expect("error")["message"]
    alice.send(type="group_create", name="X", members=[])
    assert "at least one" in alice.expect("error")["message"]
    alice.send(type="group_create", name="  ", members=["bob"])
    assert "name" in alice.expect("error")["message"]


def test_group_offline_member_gets_backlog_on_login(srv):
    alice, bob, carol = three_friends(srv)
    alice.send(type="group_create", name="G", members=["bob", "carol"])
    gid = alice.expect("group_update")["group"]["group_id"]
    carol.send(type="logout")
    carol.expect("logged_out")
    payloads = [enc_group("alice", gid, f"while you were away {i}", ["alice", "bob", "carol"]) for i in range(2)]
    for p in payloads:
        alice.send(type="send_group", group_id=gid, payload=p)
        alice.expect("group_message")
    carol2 = TClient(srv.url)
    carol2.send(type="login", username="carol", password=PASSWORD,
                public_key=IDENTITIES["carol"].public_key_b64)
    carol2.expect("login_result")
    got = [carol2.expect("group_message") for _ in range(2)]
    assert [g["payload"] for g in got] == payloads and all(g["queued"] for g in got)
    carol2.send(type="logout")
    carol2.expect("logged_out")
    again = login(srv.url, "carol")
    assert [m for m in again.drain(0.5) if m["type"] == "group_message"] == []


def test_group_admin_add_remove_leave_rules(srv):
    alice, bob, carol = three_friends(srv)
    dave = make_user(srv.url, "dave")
    befriend(alice, dave)
    alice.send(type="group_create", name="G", members=["bob"])
    gid = alice.expect("group_update")["group"]["group_id"]
    bob.expect("group_update")
    # a non-admin cannot add or remove
    bob.send(type="group_add", group_id=gid, username="carol")
    assert "admin" in bob.expect("error")["message"]
    bob.send(type="group_remove", group_id=gid, username="alice")
    assert "admin" in bob.expect("error")["message"]
    # admin adds a friend
    alice.send(type="group_add", group_id=gid, username="carol")
    g = carol.expect("group_update")["group"]
    assert {m["username"] for m in g["members"]} == {"alice", "bob", "carol"}
    # admin cannot add someone who is not her friend
    alice.send(type="group_add", group_id=gid, username="zed")
    assert "friends list" in alice.expect("error")["message"]
    # admin removes bob: bob is told, others see the update
    alice.drain(0.3)
    alice.send(type="group_remove", group_id=gid, username="bob")
    assert bob.expect("group_removed")["reason"] == "removed"
    # removed member can no longer post
    bob.send(type="send_group", group_id=gid, payload=enc_group("bob", gid, "hi", ["alice", "bob"]))
    assert "not a member" in bob.expect("error")["message"]
    # admin leaves -> admin passes to the remaining member
    alice.send(type="group_leave", group_id=gid)
    assert alice.expect("group_removed")["reason"] == "left"
    upd = [m for m in carol.drain(0.5) if m["type"] == "group_update"][-1]["group"]
    assert upd["admin"] == "carol" and [m["username"] for m in upd["members"]] == ["carol"]
    carol.send(type="group_leave", group_id=gid)
    carol.expect("group_removed")
    assert wait_until(lambda: srv.db.get_group(gid) is None)   # last member gone -> group deleted


def test_new_member_cannot_see_earlier_group_messages(srv):
    alice, bob, carol = three_friends(srv)
    alice.send(type="group_create", name="G", members=["bob"])
    gid = alice.expect("group_update")["group"]["group_id"]
    alice.send(type="send_group", group_id=gid, payload=enc_group("alice", gid, "secret before carol", ["alice", "bob"]))
    alice.expect("group_message")
    alice.send(type="group_add", group_id=gid, username="carol")
    carol.expect("group_update")
    carol.send(type="history", kind="group", group_id=gid)
    assert carol.expect("history")["messages"] == []
    alice.send(type="send_group", group_id=gid, payload=enc_group("alice", gid, "welcome carol", ["alice", "bob", "carol"]))
    carol.expect("group_message")
    carol.send(type="history", kind="group", group_id=gid)
    assert len(carol.expect("history")["messages"]) == 1
    bob.send(type="history", kind="group", group_id=gid)
    assert len(bob.expect("history")["messages"]) == 2


def test_state_contains_groups_and_friends_after_relogin(srv):
    alice, bob, _ = three_friends(srv)
    alice.send(type="group_create", name="Persist", members=["bob"])
    alice.expect("group_update")
    alice.send(type="logout")
    again = login(srv.url, "alice")
    assert [g["name"] for g in again.state["groups"]] == ["Persist"]
    assert {f["username"] for f in again.state["friends"]} == {"bob", "carol"}


def test_non_member_cannot_read_group_history(srv):
    alice, bob, carol = three_friends(srv)
    alice.send(type="group_create", name="G", members=["bob"])
    gid = alice.expect("group_update")["group"]["group_id"]
    carol.send(type="history", kind="group", group_id=gid)
    assert "not a member" in carol.expect("error")["message"]


def test_state_and_groups_carry_public_keys(srv):
    alice, bob, _ = three_friends(srv)
    alice.send(type="group_create", name="K", members=["bob"])
    g = alice.expect("group_update")["group"]
    assert {m["username"]: m["public_key"] for m in g["members"]} == {
        "alice": IDENTITIES["alice"].public_key_b64, "bob": IDENTITIES["bob"].public_key_b64}
    alice.send(type="logout")
    again = login(srv.url, "alice")
    assert {f["username"]: f["public_key"] for f in again.state["friends"]} == {
        "bob": IDENTITIES["bob"].public_key_b64, "carol": IDENTITIES["carol"].public_key_b64}


def test_idle_connection_is_dropped(tmp_path):
    db = Database(sqlite_path=str(tmp_path / "idle.db"))
    server = ServerThread(db, idle_timeout=1).start()
    try:
        c = TClient(server.url)
        assert closed_within(c, 6)
    finally:
        server.stop()
        db.close()


def test_oversized_frame_is_refused_and_connection_closed(srv):
    c = make_user(srv.url, "alice")
    c.sock.sendall(P.HEADER.pack(P.MAX_FRAME + 1) + b"x" * 100)
    assert "too large" in c.expect("error")["message"]
    assert closed_within(c)
    assert wait_until(lambda: "alice" not in srv.chat.sessions)
