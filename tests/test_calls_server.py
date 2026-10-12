"""Calls, phase 1 - signalling and the encrypted-media relay, tested against the real TCP server.

Everything here speaks the real wire protocol through real sockets (no mocks): ring, answer,
decline, busy, offline, timeout, hang-up, dropped connections, re-login, unfriending, the
database call log and the opaque media relay.
"""
import time

import pytest

from server import app as server_app
from server.app import ChatServer
from server.database import Database
from tests.helpers import ServerThread, befriend, login, make_user, register, wait_until


@pytest.fixture
def srv(tmp_path):
    db = Database(sqlite_path=str(tmp_path / "calls.db"))
    server = ServerThread(db).start()
    yield server
    server.stop()
    db.close()


@pytest.fixture
def trio(srv):
    """alice and bob are friends and online; carol is online but nobody's friend."""
    a = make_user(srv.address, "alice")
    b = make_user(srv.address, "bob")
    c = make_user(srv.address, "carol")
    befriend(a, b)
    return a, b, c


def call_msgs(client, wait=0.25):
    """Only the call-related messages that arrive within ``wait`` seconds (ignores presence etc.)."""
    return [m for m in client.drain(wait) if m["type"].startswith("call_")]


def row(db, call_id):
    return next(r for r in db.calls_for_user("alice") + db.calls_for_user("bob") if r["call_id"] == call_id)


def start(a, b, media="audio"):
    a.send(type="call_start", to=b.username, media=media)
    res = a.expect("call_result")
    return res


def connect(a, b, media="audio"):
    res = start(a, b, media)
    assert res["status"] == "ringing"
    inc = b.expect("call_incoming")
    assert inc["call_id"] == res["call_id"] and inc["caller"] == "alice"
    b.send(type="call_accept", call_id=inc["call_id"])
    a.expect("call_accepted")
    b.expect("call_accepted")
    return res["call_id"]


# --------------------------------------------------------------------------- #
# ring / answer / hang up
# --------------------------------------------------------------------------- #
def test_ring_accept_hangup_is_logged(srv, trio):
    a, b, _ = trio
    cid = connect(a, b, "video")
    a.send(type="call_end", call_id=cid)
    assert a.expect("call_ended")["status"] == "completed"
    assert b.expect("call_ended")["status"] == "completed"
    r = row(srv.db, cid)
    assert (r["caller"], r["callee"], r["media"], r["status"]) == ("alice", "bob", "video", "completed")
    assert r["ended"]


def test_duration_counts_talk_time_not_ringing_time(srv, trio):
    a, b, _ = trio
    res = start(a, b)
    b.expect("call_incoming")
    time.sleep(2.2)                                   # ringing for a while ...
    b.send(type="call_accept", call_id=res["call_id"])
    a.expect("call_accepted"); b.expect("call_accepted")
    time.sleep(1.1)                                   # ... then talk for about a second
    a.send(type="call_end", call_id=res["call_id"])
    a.expect("call_ended")
    assert row(srv.db, res["call_id"])["duration"] in (1, 2), "ringing time must not be counted as talk time"


def test_caller_sees_ringing_before_answer_even_if_callee_answers_instantly(srv, trio):
    a, b, _ = trio
    for _ in range(15):
        a.send(type="call_start", to="bob", media="audio")
        inc = b.expect("call_incoming")
        b.send(type="call_accept", call_id=inc["call_id"])         # answers the moment it rings
        kinds = []
        while "call_accepted" not in kinds:
            m = a.expect("call_result", timeout=3) if "call_result" not in kinds else a.expect("call_accepted", timeout=3)
            kinds.append(m["type"])
        assert kinds == ["call_result", "call_accepted"]
        b.expect("call_accepted")
        a.send(type="call_end", call_id=inc["call_id"])
        a.expect("call_ended"); b.expect("call_ended")


def test_decline_and_cancel_and_callee_hangup_while_ringing(srv, trio):
    a, b, _ = trio
    res = start(a, b)
    inc = b.expect("call_incoming")
    b.send(type="call_decline", call_id=inc["call_id"])
    assert a.expect("call_ended")["status"] == "declined"
    assert b.expect("call_ended")["status"] == "declined"

    res = start(a, b)                                  # caller gives up while it rings
    b.expect("call_incoming")
    a.send(type="call_end", call_id=res["call_id"])
    assert a.expect("call_ended")["status"] == "cancelled"
    assert b.expect("call_ended")["status"] == "cancelled"

    res = start(a, b)                                  # callee presses "end" while it rings = a decline
    b.expect("call_incoming")
    b.send(type="call_end", call_id=res["call_id"])
    assert a.expect("call_ended")["status"] == "declined"
    b.expect("call_ended")
    assert row(srv.db, res["call_id"])["status"] == "declined"


def test_only_friends_can_call_and_not_yourself(srv, trio):
    a, b, c = trio
    a.send(type="call_start", to="carol", media="audio")
    assert "friends" in a.expect("error")["message"]
    a.send(type="call_start", to="alice", media="audio")
    a.expect("error")
    a.send(type="call_start", to="bob", media="hologram")
    a.expect("error")
    assert not call_msgs(c)                            # carol never heard anything


def test_busy_offline_and_both_are_logged(srv, trio):
    a, b, c = trio
    befriend(a, c)
    cid = connect(a, b)
    c.send(type="call_start", to="alice", media="audio")         # alice (the callee) is already in a call
    assert c.expect("call_result")["status"] == "busy"
    a.send(type="call_start", to="carol", media="audio")         # alice (the caller) is already in a call
    assert a.expect("call_result")["status"] == "busy"
    assert not call_msgs(b, 0.2)                                 # the busy attempt never rings bob's phone
    a.send(type="call_end", call_id=cid); a.expect("call_ended")
    assert wait_until(lambda: any(r["status"] == "busy" for r in srv.db.calls_for_user("carol")))
    c.close()
    time.sleep(0.2)
    a.send(type="call_start", to="carol", media="audio")
    assert a.expect("call_result")["status"] == "offline"
    assert any(r["status"] == "no_answer" for r in srv.db.calls_for_user("alice"))


def test_no_answer_after_timeout(srv, trio, monkeypatch):
    a, b, _ = trio
    monkeypatch.setattr(server_app, "RING_SECONDS", 0.5)
    res = start(a, b)
    b.expect("call_incoming")
    assert a.expect("call_ended", timeout=3)["status"] == "no_answer"
    assert b.expect("call_ended")["status"] == "no_answer"
    assert row(srv.db, res["call_id"])["status"] == "no_answer"
    b.send(type="call_accept", call_id=res["call_id"])           # too late
    b.expect("error")


def test_only_the_callee_can_accept(srv, trio):
    a, b, _ = trio
    res = start(a, b)
    b.expect("call_incoming")
    a.send(type="call_accept", call_id=res["call_id"])           # caller cannot answer their own call
    a.expect("error")
    c_id = res["call_id"]
    a.send(type="call_end", call_id=c_id); a.expect("call_ended")


# --------------------------------------------------------------------------- #
# lost connections, re-login, unfriending, restarts
# --------------------------------------------------------------------------- #
def test_dropped_connection_ends_the_call_for_the_other_side(srv, trio):
    a, b, _ = trio
    cid = connect(a, b)
    b.close()
    assert a.expect("call_ended", timeout=3)["status"] == "connection_lost"
    assert row(srv.db, cid)["status"] == "connection_lost"
    assert row(srv.db, cid)["duration"] >= 0


def test_relogin_elsewhere_does_not_leave_a_ghost_call(srv, trio):
    a, b, _ = trio
    res = start(a, b)
    b.expect("call_incoming")
    b2 = login(srv.address, "bob")                               # bob logs in on another computer
    b.expect("kicked")
    assert a.expect("call_ended", timeout=3)["status"] in ("connection_lost", "no_answer", "cancelled")
    a.send(type="call_start", to="bob", media="audio")           # the line must be free again
    assert a.expect("call_result")["status"] == "ringing"
    b2.expect("call_incoming")


def test_unfriending_hangs_up(srv, trio):
    a, b, _ = trio
    cid = connect(a, b)
    a.send(type="friend_remove", user="bob")
    assert a.expect("call_ended")["status"] in ("completed", "cancelled")
    assert b.expect("call_ended")
    assert row(srv.db, cid)["ended"]


def test_stale_calls_are_closed_when_the_server_restarts(tmp_path):
    db = Database(sqlite_path=str(tmp_path / "stale.db"))
    db.create_call("deadbeef", "alice", "bob", "audio", "ringing")
    db.create_call("cafebabe", "alice", "bob", "audio", "ringing")
    ChatServer(db, bcrypt_rounds=4, rate_limit=None)
    rows = {r["call_id"]: r for r in db.calls_for_user("alice")}
    assert all(r["ended"] and r["status"] != "ringing" for r in rows.values())
    db.close()


def test_server_shutdown_closes_active_calls(srv, trio):
    a, b, _ = trio
    cid = connect(a, b)
    srv.chat.close_all()
    assert wait_until(lambda: row(srv.db, cid)["ended"])


# --------------------------------------------------------------------------- #
# media relay + call log
# --------------------------------------------------------------------------- #
def test_media_is_relayed_unchanged_only_inside_an_active_call(srv, trio):
    a, b, c = trio
    res = start(a, b)
    inc = b.expect("call_incoming")
    a.send(type="call_media", call_id=res["call_id"], kind="audio", seq=1, payload="AAAA")   # still ringing
    assert not call_msgs(b)
    b.send(type="call_accept", call_id=inc["call_id"])
    a.expect("call_accepted"); b.expect("call_accepted")
    a.send(type="call_media", call_id=res["call_id"], kind="audio", seq=1, payload="c2VjcmV0")
    got = b.expect("call_media")
    assert (got["from"], got["kind"], got["seq"], got["payload"]) == ("alice", "audio", 1, "c2VjcmV0")
    c.send(type="call_media", call_id=res["call_id"], kind="audio", seq=1, payload="x")      # an outsider
    assert not call_msgs(a) and not call_msgs(b)
    for bad in (dict(kind="text", seq=1, payload="x"), dict(kind="audio", seq=0, payload="x"),
                dict(kind="audio", seq="1", payload="x"), dict(kind="audio", seq=1, payload=5),
                dict(kind="video", seq=1, payload="x" * 80000)):
        a.send(type="call_media", call_id=res["call_id"], **bad)
        a.expect("error")
    assert not call_msgs(b)


def test_media_flood_is_capped(srv, trio):
    a, b, _ = trio
    cid = connect(a, b)
    for i in range(1, 2001):
        a.send(type="call_media", call_id=cid, kind="audio", seq=i, payload="AAAA")
    time.sleep(0.5)
    got = [m for m in call_msgs(b, 0.5) if m["type"] == "call_media"]
    assert 0 < len(got) < 1500, f"relay forwarded {len(got)} of 2000 flooded packets"


def test_call_start_flood_is_rate_limited(tmp_path):
    db = Database(sqlite_path=str(tmp_path / "rl.db"))
    server = ServerThread(db, rate_limit=(5, 10)).start()
    try:
        a = make_user(server.address, "alice"); b = make_user(server.address, "bob")
        befriend(a, b)
        for _ in range(12):
            a.send(type="call_start", to="bob", media="audio")
        msgs = a.drain(0.6)
        assert any(m["type"] == "error" for m in msgs), "ring-spamming must be limited"
        assert sum(1 for m in msgs if m["type"] == "call_result") < 12
    finally:
        server.stop(); db.close()


def test_history_has_direction_data_and_survives_for_both_users(srv, trio):
    a, b, _ = trio
    cid = connect(a, b, "video")
    time.sleep(0.2)
    a.send(type="call_end", call_id=cid); a.expect("call_ended"); b.expect("call_ended")
    start(a, b); inc = b.expect("call_incoming")
    b.send(type="call_decline", call_id=inc["call_id"]); a.expect("call_ended"); b.expect("call_ended")
    for u in (a, b):
        u.send(type="call_history")
        calls = u.expect("call_history")["calls"]
        assert [c["status"] for c in calls] == ["declined", "completed"]            # newest first
        assert calls[1]["media"] == "video" and calls[1]["caller"] == "alice" and calls[1]["callee"] == "bob"
    c = login(srv.address, "carol")
    c.send(type="call_history")
    assert c.expect("call_history")["calls"] == []                # strangers see nothing
