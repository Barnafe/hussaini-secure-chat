"""Phase 5 - performance and reliability tests.

Measures message latency, throughput with many simultaneous clients, and the cost of the
cryptographic operations. Results are written to tests/results/performance.{json,md} so
they can be quoted in the report. (Numbers depend on the machine; the assertions are
deliberately generous.)
"""
import json
import os
import statistics
import threading
import time

import pytest

from server.database import Database
from shared.crypto_utils import (decrypt_message, derive_key, encrypt_message, hash_password,
                                 verify_password)
from shared.keys import Identity, generate_passphrase
from tests.helpers import ServerThread, befriend, enc_dm, make_user

RESULTS = {}
RESULT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


@pytest.fixture(scope="module", autouse=True)
def write_results():
    yield
    os.makedirs(RESULT_DIR, exist_ok=True)
    with open(os.path.join(RESULT_DIR, "performance.json"), "w") as fh:
        json.dump(RESULTS, fh, indent=2)
    lines = ["# Performance test results", "", "| Test | Result |", "|---|---|"]
    for name, data in RESULTS.items():
        lines.append(f"| {name} | " + ", ".join(f"{k}: {v}" for k, v in data.items()) + " |")
    with open(os.path.join(RESULT_DIR, "performance.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")


@pytest.fixture
def srv(tmp_path):
    db = Database(sqlite_path=str(tmp_path / "perf.db"))
    server = ServerThread(db).start()
    yield server
    server.stop()
    db.close()


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p))]


def test_message_latency_sequential(srv):
    alice, bob = make_user(srv.address, "alice"), make_user(srv.address, "bob")
    befriend(alice, bob)
    n, lat = 200, []
    for i in range(n):
        payload = enc_dm("alice", "bob", f"message number {i}")
        t0 = time.perf_counter()
        alice.send(type="send_dm", to="bob", payload=payload)
        got = bob.expect("message")
        lat.append((time.perf_counter() - t0) * 1000)
        assert got["payload"] == payload
        alice.expect("message")                                     # drain the echo
    RESULTS["Sequential delivery latency (200 msgs)"] = {
        "mean_ms": round(statistics.mean(lat), 2), "p95_ms": round(pct(lat, 0.95), 2),
        "max_ms": round(max(lat), 2), "database": srv.db.backend}
    assert statistics.mean(lat) < 250


def test_concurrent_clients_all_messages_delivered(srv):
    pairs, per_pair = 10, 30
    senders, receivers = [], []
    for i in range(pairs):
        a, b = make_user(srv.address, f"sender{i}"), make_user(srv.address, f"recv{i}")
        befriend(a, b)
        senders.append(a)
        receivers.append(b)
    received = [0] * pairs
    errors = []

    def send_loop(i):
        try:
            for k in range(per_pair):
                senders[i].send(type="send_dm", to=f"recv{i}",
                                payload=enc_dm(f"sender{i}", f"recv{i}", f"m{k}"))
        except Exception as exc:                                     # pragma: no cover
            errors.append(exc)

    def recv_loop(i):
        try:
            for _ in range(per_pair):
                receivers[i].expect("message", timeout=15)
                received[i] += 1
        except Exception as exc:
            errors.append(exc)

    t0 = time.perf_counter()
    threads = [threading.Thread(target=recv_loop, args=(i,)) for i in range(pairs)] + \
              [threading.Thread(target=send_loop, args=(i,)) for i in range(pairs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(40)
    elapsed = time.perf_counter() - t0
    total = pairs * per_pair
    RESULTS["Concurrent clients (20 online, 10 pairs x 30 msgs)"] = {
        "messages": total, "delivered": sum(received), "seconds": round(elapsed, 2),
        "msgs_per_sec": round(total / elapsed, 1), "database": srv.db.backend}
    assert not errors, errors
    assert sum(received) == total
    assert len(srv.db.raw_dump("messages")) == total


def test_many_simultaneous_logins(srv):
    n = 30
    for i in range(n):
        from tests.helpers import register
        assert register(srv.address, f"user{i}")["ok"]
    t0 = time.perf_counter()
    clients = [__import__("tests.helpers", fromlist=["login"]).login(srv.address, f"user{i}") for i in range(n)]
    elapsed = time.perf_counter() - t0
    assert len(srv.chat.sessions) == n
    RESULTS["Simultaneous sessions"] = {"clients_online": n, "login_seconds_total": round(elapsed, 2)}
    for c in clients:
        c.close()


def test_crypto_cost():
    key = derive_key("a secret phrase", b"salt", iterations=1000)
    text = "x" * 200
    n = 2000
    t0 = time.perf_counter()
    for _ in range(n):
        assert decrypt_message(key, encrypt_message(key, text, "aad"), "aad") == text
    per_msg_ms = (time.perf_counter() - t0) * 1000 / n
    t0 = time.perf_counter()
    Identity("alice", generate_passphrase())                        # production iteration count
    kdf_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    h = hash_password("Passw0rd!", rounds=12)
    hash_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    assert verify_password("Passw0rd!", h)
    verify_ms = (time.perf_counter() - t0) * 1000
    RESULTS["Cryptography cost"] = {
        "aes_gcm_encrypt+decrypt_200chars_ms": round(per_msg_ms, 4),
        "passphrase_to_keypair_pbkdf2_600k_ms": round(kdf_ms, 1),
        "bcrypt_cost12_hash_ms": round(hash_ms, 1), "bcrypt_cost12_verify_ms": round(verify_ms, 1)}
    assert per_msg_ms < 5


def test_server_survives_abrupt_client_disconnects(srv):
    alice, bob = make_user(srv.address, "alice"), make_user(srv.address, "bob")
    befriend(alice, bob)
    for _ in range(20):                                              # clients that vanish mid-session
        c = make_user(srv.address, f"ghost{_}")
        c.close()
    alice.send(type="send_dm", to="bob", payload=enc_dm("alice", "bob", "still alive"))
    assert bob.expect("message")["from"] == "alice"
    RESULTS["Abrupt disconnects"] = {"ghost_clients": 20, "server_still_routing": True}
