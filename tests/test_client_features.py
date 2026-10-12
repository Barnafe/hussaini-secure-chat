"""Version 1.1 features, client side (controller): ticks, chat-list previews and sorting, typing,
change password, group rename / admin hand-over and safety numbers."""
import time
from datetime import datetime, timedelta, timezone

import pytest

from client.controller import (ChatController, TYPING_THROTTLE, day_label, dm_key, group_key)
from client.trust import TrustStore
from server.database import Database
from tests.helpers import ServerThread
from tests.test_client import PW, Peer, befriend, pair


@pytest.fixture
def srv(tmp_path):
    db = Database(sqlite_path=str(tmp_path / "cf.db"))
    server = ServerThread(db).start()
    yield server
    server.stop()
    db.close()


def add_peer(srv, name, friend_of=None):
    p = Peer(srv)
    assert p.register(name)["ok"]
    p.login(name)
    if friend_of:
        befriend(friend_of[0], p, friend_of[1], name)
    return p


# --------------------------------------------------------------------------- #
def test_day_label_today_yesterday_and_older():
    ts = "2026-10-10 12:00:00"
    local_day = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone().date()
    assert day_label(ts, today=local_day) == "Today"
    assert day_label(ts, today=local_day + timedelta(days=1)) == "Yesterday"
    older = day_label(ts, today=local_day + timedelta(days=9))
    assert older == local_day.strftime("%d %b %Y").lstrip("0")
    assert day_label("garbage") == "" and day_label(None) == ""


def test_delivery_ticks_in_the_controller(srv):
    a, b = pair(srv)
    a.ctrl.open_conversation(dm_key("bob"))
    a.ctrl.send_text("online tick")
    a.wait(lambda: a.ctrl.conversation(dm_key("bob")), what="echo")
    assert a.ctrl.conversation(dm_key("bob"))[0]["delivered"] is True       # bob was online: two ticks
    b.ctrl.logout()
    a.wait(lambda: not a.ctrl.friends["bob"]["online"], what="bob offline")
    a.ctrl.send_text("offline tick")
    a.wait(lambda: len(a.ctrl.conversation(dm_key("bob"))) == 2, what="second echo")
    assert [m["delivered"] for m in a.ctrl.conversation(dm_key("bob"))] == [True, False]
    b2 = Peer(srv)
    b2.login("bob", passphrase=b.passphrase)
    a.wait(lambda: all(m["delivered"] for m in a.ctrl.conversation(dm_key("bob"))), what="second tick arrives")
    # and the flags survive a fresh history load
    a.ctrl.logout()
    a2 = Peer(srv)
    a2.login("alice", passphrase=a.passphrase)
    a2.ctrl.open_conversation(dm_key("bob"))
    a2.wait(lambda: len(a2.ctrl.conversation(dm_key("bob"))) == 2, what="history")
    assert all(m["delivered"] for m in a2.ctrl.conversation(dm_key("bob")))


def test_chat_list_is_sorted_by_latest_message_with_previews(srv):
    a, b = pair(srv)
    c = add_peer(srv, "carol", friend_of=(a, "alice"))
    assert [u for u, _n, _o in a.ctrl.friend_list()][0] == "bob"             # nothing yet: alphabetical
    a.ctrl.open_conversation(dm_key("carol"))
    a.ctrl.send_text("Hello Carol, are you coming to the library tomorrow afternoon?")
    a.wait(lambda: a.ctrl.preview(dm_key("carol")), what="preview")
    assert [u for u, _n, _o in a.ctrl.friend_list()][0] == "carol"           # latest conversation on top
    assert a.ctrl.preview(dm_key("carol")).startswith("You: Hello Carol")
    assert a.ctrl.preview(dm_key("carol")).endswith("…") and len(a.ctrl.preview(dm_key("carol"))) <= 36
    b.ctrl.open_conversation(dm_key("alice"))
    b.ctrl.send_text("Bob here")
    a.wait(lambda: a.ctrl.preview(dm_key("bob")) == "Bob here", what="incoming preview")
    assert [u for u, _n, _o in a.ctrl.friend_list()][0] == "bob"
    # after a brand-new login the previews and the order come from the server's `state`, no chat opened
    a.ctrl.logout()
    a2 = Peer(srv)
    a2.login("alice", passphrase=a.passphrase)
    assert [u for u, _n, _o in a2.ctrl.friend_list()][0] == "bob"
    assert a2.ctrl.preview(dm_key("bob")) == "Bob here"
    assert a2.ctrl.preview(dm_key("carol")).startswith("You: Hello Carol")
    assert a2.ctrl.unread == {}                                              # previews are not "new" messages


def test_group_preview_shows_who_wrote_it(srv):
    a, b = pair(srv)
    a.ctrl.create_group("Team", ["bob"])
    a.wait(lambda: a.ctrl.groups, what="group")
    gid = next(iter(a.ctrl.groups))
    b.wait(lambda: b.ctrl.groups, what="group for bob")
    b.ctrl.open_conversation(group_key(gid))
    b.ctrl.send_text("Standup at nine")
    a.wait(lambda: a.ctrl.preview(group_key(gid)) == "Bob: Standup at nine", what="group preview")
    assert a.ctrl.group_list()[0]["group_id"] == gid


def test_typing_indicator(srv):
    a, b = pair(srv)
    a.ctrl.open_conversation(dm_key("bob"))
    a.ctrl.notify_typing()
    b.wait(lambda: b.ctrl.typing_names(dm_key("alice")) == ["Alice A"], what="bob sees typing")
    seen = len([t for t in b.topics() if t == "typing"])
    a.ctrl.notify_typing()                                                   # within the throttle window: not sent
    time.sleep(0.3)
    b.ctrl.poll()
    assert len([t for t in b.topics() if t == "typing"]) == seen
    assert TYPING_THROTTLE >= 2
    # a real message clears the indicator at once
    a.ctrl.send_text("done typing")
    b.wait(lambda: b.ctrl.typing_names(dm_key("alice")) == [], what="indicator cleared by the message")
    # ...and it fades by itself
    a.ctrl._typing_sent.clear()
    a.ctrl.notify_typing()
    b.wait(lambda: b.ctrl.typing_names(dm_key("alice")), what="typing again")
    b.ctrl.typing[dm_key("alice")]["alice"] = time.monotonic() - 1
    b.ctrl.poll()
    assert b.ctrl.typing_names(dm_key("alice")) == []


def test_change_password_in_the_controller(srv):
    a, _b = pair(srv)
    a.log.clear()
    a.ctrl.change_password("", "NewPassw0rd", "NewPassw0rd")
    assert a.last("password_changed")["message"] == "Enter your current password."
    a.ctrl.change_password(PW, "short", "short")
    assert "at least" in a.last("password_changed")["message"]
    a.ctrl.change_password("WrongPass9", "NewPassw0rd", "NewPassw0rd")
    a.wait(lambda: a.last("password_changed") and "incorrect" in a.last("password_changed")["message"], what="wrong")
    a.ctrl.change_password(PW, "NewPassw0rd", "NewPassw0rd")
    a.wait(lambda: a.last("password_changed")["ok"], what="changed")
    assert a.ctrl._login_msg["password"] == "NewPassw0rd"                    # auto-reconnect uses the new one
    a.ctrl.logout()
    old = Peer(srv)
    old.ctrl.start_auth(srv.address, "login", a.passphrase, "alice", PW)
    old.wait(lambda: "auth_error" in old.topics(), what="old password rejected")
    new = Peer(srv)
    new.ctrl.start_auth(srv.address, "login", a.passphrase, "alice", "NewPassw0rd")
    new.wait(lambda: new.ctrl.logged_in, what="new password accepted")


def test_group_rename_and_admin_hand_over_in_the_controller(srv):
    a, b = pair(srv)
    a.ctrl.create_group("Old", ["bob"])
    a.wait(lambda: a.ctrl.groups, what="group")
    gid = next(iter(a.ctrl.groups))
    b.wait(lambda: b.ctrl.groups, what="group for bob")
    a.ctrl.group_rename(gid, "Final Year Project")
    b.wait(lambda: b.ctrl.groups[gid]["name"] == "Final Year Project", what="rename arrives")
    a.ctrl.group_transfer(gid, "bob")
    a.wait(lambda: a.ctrl.groups[gid]["admin"] == "bob", what="admin changed")
    b.wait(lambda: b.ctrl.groups[gid]["admin"] == "bob", what="bob sees he is admin")


def test_safety_numbers_match_and_detect_a_changed_key(srv, tmp_path):
    a, b = pair(srv)
    sa, sb = a.ctrl.safety_number_for("bob"), b.ctrl.safety_number_for("alice")
    assert sa == sb and len(sa.split()) == 6                                 # both friends see the same number
    assert a.ctrl.verification_state("bob") == "unverified"
    a.ctrl.mark_verified("bob")
    assert a.ctrl.verification_state("bob") == "verified"
    assert TrustStore().verified_number("alice", "bob") == sa                # remembered on disk
    a.ctrl.peer_keys["bob"] = "B" * 43 + "="                                  # somebody swapped bob's key
    assert a.ctrl.verification_state("bob") == "changed"
    assert a.ctrl.verification_state("nobody") == "unknown"
