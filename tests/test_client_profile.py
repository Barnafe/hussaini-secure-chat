"""Client controller: profile, picture, password, chat-list previews, mute / clear / shared links."""
import base64
import time
from datetime import datetime, timezone

from client.controller import (clock, day_heading, dm_key, find_links, group_key, list_time)
from tests.test_client import Peer, PW, pair, srv  # noqa: F401  (srv is a fixture)

FAKE_JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"picture" * 30).decode("ascii")


def test_time_helpers():
    local = datetime(2026, 10, 10, 15, 5, tzinfo=timezone.utc).astimezone()
    assert clock("2026-10-10 15:05:00") == local.strftime("%I:%M %p").lstrip("0")
    assert not clock("2026-10-10 15:05:00").startswith("0")
    assert clock("garbage") == "" and day_heading("garbage") == "" and list_time(None) == ""
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    assert day_heading(now).startswith("Today, ") and list_time(now) == clock(now)
    assert day_heading("2001-02-03 12:00:00") == "3 Feb 2001"
    assert find_links("go to https://example.com/a?b=1, or www.test.org.") == ["https://example.com/a?b=1", "www.test.org"]


def test_profile_change_reaches_me_and_friends(srv):
    a, b = pair(srv)
    a.ctrl.update_profile("  Alice Adamu ", "Good vibes. Great conversations.")
    a.wait(lambda: a.last("profile_result") and a.last("profile_result")["ok"], what="own result")
    assert a.ctrl.me["fullname"] == "Alice Adamu" and a.ctrl.me["bio"].startswith("Good vibes")
    b.wait(lambda: b.ctrl.friends["alice"]["fullname"] == "Alice Adamu", what="friend sees new name")
    assert b.ctrl.bio_of("alice") == "Good vibes. Great conversations."
    assert b.ctrl.name_of("alice") == "Alice Adamu" and b.ctrl.title_for(dm_key("alice")).startswith("Alice Adamu")


def test_profile_validation_happens_before_sending(srv):
    a, _b = pair(srv)
    assert a.ctrl.update_profile("", "x") is False
    assert a.last("profile_result")["ok"] is False
    assert a.ctrl.set_avatar("not-base64!") is False
    assert a.ctrl.change_password("", "BrandNew123", "BrandNew123") is False
    assert a.last("password_result")["message"] == "Enter your current password."
    assert a.ctrl.change_password(PW, "short", "short") is False


def test_picture_is_fetched_once_and_cached_by_friends(srv):
    a, b = pair(srv)
    assert b.ctrl.avatar_b64("alice") is None                    # nothing uploaded yet
    a.ctrl.set_avatar(FAKE_JPEG)
    a.wait(lambda: a.ctrl.avatar_version("alice") != "", what="own picture version")
    b.wait(lambda: b.ctrl.avatar_version("alice") != "", what="friend sees the new version")
    assert b.ctrl.avatar_b64("alice") is None                    # first call asks the server...
    b.wait(lambda: b.ctrl.avatar_b64("alice") == FAKE_JPEG, what="picture arrives")
    assert ("avatar", "alice") in b.log
    n = len(b.log)
    assert b.ctrl.avatar_b64("alice") == FAKE_JPEG and len(b.log) == n   # cached: no new request / event
    a.ctrl.set_avatar("")                                        # remove it again
    b.wait(lambda: b.ctrl.avatar_version("alice") == "", what="removal seen")
    assert b.ctrl.avatar_b64("alice") is None


def test_picture_survives_a_new_login(srv):
    a, b = pair(srv)
    a.ctrl.set_avatar(FAKE_JPEG)
    a.wait(lambda: a.ctrl.avatar_version("alice") != "", what="uploaded")
    b.ctrl.logout()
    b.login("bob")
    assert b.ctrl.avatar_version("alice") != ""                  # version arrives with the friend list
    b.ctrl.avatar_b64("alice")
    b.wait(lambda: b.ctrl.avatar_b64("alice") == FAKE_JPEG, what="picture after relogin")


def test_change_password_end_to_end(srv):
    a, _b = pair(srv)
    a.ctrl.change_password("wrong-one", "BrandNew123", "BrandNew123")
    a.wait(lambda: a.last("password_result") is not None, what="reply")
    assert a.last("password_result")["ok"] is False
    a.ctrl.change_password(PW, "BrandNew123", "BrandNew123")
    a.wait(lambda: a.last("password_result")["ok"], what="password changed")


def test_chat_list_has_previews_after_login_without_unread(srv):
    a, b = pair(srv)
    a.ctrl.open_conversation(dm_key("bob"))
    a.ctrl.send_text("hello bob")
    a.ctrl.send_text("see https://www.figma.com/file/x1 please")
    b.wait(lambda: b.ctrl.unread.get(dm_key("alice")) == 2, what="bob got both")
    b.ctrl.logout()
    b.login("bob")
    b.wait(lambda: b.ctrl.last_message(dm_key("alice")) is not None, what="preview after login")
    row = b.ctrl.conversation_rows()[0]
    assert row["key"] == dm_key("alice") and row["last"]["text"].startswith("see https://www.figma")
    assert row["unread"] == 0 and row["name"] == "Alice A"       # a preview never counts as unread
    assert b.ctrl.shared_links(dm_key("alice")) == [("https://www.figma.com/file/x1", "figma.com")]


def test_conversation_rows_sort_by_latest_activity(srv):
    a, b = pair(srv)
    c = Peer(srv)
    assert c.register("carol", "Carol C")["ok"]
    c.login("carol")
    from tests.test_client import befriend
    befriend(a, c, "alice", "carol")
    a.ctrl.create_group("Team Project", ["bob", "carol"])
    a.wait(lambda: a.ctrl.groups, what="group")
    gkey = group_key(next(iter(a.ctrl.groups)))
    a.ctrl.open_conversation(dm_key("carol"))
    a.ctrl.send_text("first to carol")
    a.wait(lambda: a.ctrl.last_message(dm_key("carol")), what="carol msg")
    time.sleep(1.1)                                              # timestamps have one-second resolution
    a.ctrl.open_conversation(gkey)
    a.ctrl.send_text("hello team")
    a.wait(lambda: a.ctrl.last_message(gkey), what="group msg")
    keys = [r["key"] for r in a.ctrl.conversation_rows()]
    assert keys[:2] == [gkey, dm_key("carol")] and keys[2] == dm_key("bob")   # no messages -> last


def test_mute_and_clear_chat_are_local(srv):
    a, b = pair(srv)
    key = dm_key("bob")
    a.ctrl.open_conversation(key)
    a.ctrl.send_text("one")
    a.ctrl.send_text("two")
    a.wait(lambda: len(a.texts(key)) == 2, what="sent")
    a.ctrl.set_muted(key, True)
    assert a.ctrl.is_muted(key) and a.ctrl.conversation_rows()[0]["muted"]
    a.ctrl.set_muted(key, False)
    assert not a.ctrl.is_muted(key)
    a.ctrl.clear_chat(key)
    assert a.texts(key) == [] and a.ctrl.last_message(key) is None
    a.ctrl.send_text("three")
    a.wait(lambda: a.texts(key) == ["three"], what="only new messages after clearing")
    assert len(a.ctrl.messages[key]) == 3            # still stored (and still on the server): only hidden here
