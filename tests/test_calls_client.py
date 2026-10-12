"""Calls, phases 2-4 - the whole path with only the hardware simulated.

Real: TCP server, wire protocol, controllers, key agreement, AES-GCM, media engine threads and queues,
real JPEG encoding. Simulated: microphone/speaker (a tone per device) and the webcam.
"""
import time

import cv2
import numpy as np
import pytest

from client.media_engine import (EncryptedMediaEngine, MediaUnavailable, derive_call_key, packet_aad)
from server.database import Database
from shared.keys import Identity
from tests.fake_media import FakeSoundDevice, install_fake_camera
from tests.helpers import ServerThread, Sniffer
from tests.test_client import Peer, pair

ALICE_MIC, ALICE_SPK, BOB_MIC, BOB_SPK = 1, 11, 2, 12
TONES = {ALICE_MIC: 440, BOB_MIC: 880}


@pytest.fixture
def srv(tmp_path):
    db = Database(sqlite_path=str(tmp_path / "cc.db"))
    server = ServerThread(db).start()
    yield server
    server.stop()
    db.close()


@pytest.fixture
def audio(monkeypatch):
    return FakeSoundDevice(TONES).install(monkeypatch)


def run(peers, seconds=0.0, until=None, timeout=8.0, what="condition"):
    """Poll every controller (the Tk loop does this in the app) until ``until()`` or ``seconds`` pass."""
    end = time.time() + (timeout if until else seconds)
    while time.time() < end:
        for p in peers:
            p.ctrl.poll()
        if until and until():
            return True
        time.sleep(0.01)
    if until:
        raise AssertionError(f"timed out waiting for {what}")
    return True


class Side:
    """One person's call software: a controller plus the media engine the window would start."""

    def __init__(self, peer, mic, spk, camera=0):
        self.peer, self.mic, self.spk, self.camera = peer, mic, spk, camera
        self.engine, self.video, self.errors = None, [], []
        self.statuses = []
        previous = peer.ctrl.on_update

        def on_update(topic, payload=None):
            previous(topic, payload)
            if topic == "call_media" and self.engine:
                self.engine.receive(payload)
            if topic == "call_status":
                self.statuses.append(payload.get("status"))
        peer.ctrl.on_update = on_update

    @property
    def ctrl(self):
        return self.peer.ctrl

    def start_engine(self):
        call = self.ctrl.active_call
        self.engine = EncryptedMediaEngine(
            self.ctrl, call["call_id"], self.ctrl.call_peer(), call["media"], on_video=self.video.append,
            on_error=self.errors.append, audio_input=self.mic, audio_output=self.spk, camera_index=self.camera)
        self.engine.start()
        return self.engine


def connect_call(a, b, media="audio", callee="bob"):
    """alice rings bob, bob answers; returns when both sides are in the running call."""
    assert a.ctrl.start_call(callee, media)
    run([a.peer, b.peer], until=lambda: b.ctrl.active_call and b.ctrl.active_call.get("status") == "ringing"
        and a.ctrl.active_call, what="ringing")
    assert b.ctrl.active_call["caller"] == "alice" and b.ctrl.active_call["media"] == media
    assert b.ctrl.answer_call(b.ctrl.active_call["call_id"], True)
    run([a.peer, b.peer], until=lambda: a.ctrl.active_call.get("status") == "active"
        and b.ctrl.active_call.get("status") == "active", what="call accepted")
    a.start_engine()
    b.start_engine()


def hang_up(a, b):
    for s in (a, b):
        if s.engine:
            s.engine.stop()
    a.ctrl.end_call()
    run([a.peer, b.peer], until=lambda: not a.ctrl.active_call and not b.ctrl.active_call, what="hang up")


@pytest.fixture
def call_pair(srv, audio):
    pa, pb = pair(srv)
    a, b = Side(pa, ALICE_MIC, ALICE_SPK, 0), Side(pb, BOB_MIC, BOB_SPK, 1)
    yield a, b
    for s in (a, b):
        if s.engine:
            s.engine.stop()


# --------------------------------------------------------------------------- #
# keys and packet security (real identities)
# --------------------------------------------------------------------------- #
def test_call_key_is_shared_unique_per_call_and_private(srv):
    pa, pb = pair(srv)
    ka = derive_call_key(pa.ctrl.identity, pa.ctrl.peer_keys["bob"], "bob", "call-1")
    kb = derive_call_key(pb.ctrl.identity, pb.ctrl.peer_keys["alice"], "alice", "call-1")
    assert ka == kb and len(ka) == 32
    assert derive_call_key(pa.ctrl.identity, pa.ctrl.peer_keys["bob"], "bob", "call-2") != ka
    eve = Identity("eve", "some passphrase", 1000)                      # the server / an outsider
    assert derive_call_key(eve, pa.ctrl.identity.public_key_b64, "alice", "call-1") != ka
    with pytest.raises(MediaUnavailable):
        derive_call_key(pa.ctrl.identity, "", "bob", "call-1")


def test_packets_cannot_be_replayed_reflected_edited_or_moved(srv):
    pa, pb = pair(srv)
    ea = EncryptedMediaEngine(pa.ctrl, "c1", "bob", "audio")
    eb = EncryptedMediaEngine(pb.ctrl, "c1", "alice", "audio")
    other_call = EncryptedMediaEngine(pb.ctrl, "c2", "alice", "audio")
    seq, text = ea.seal("audio", b"hello")
    ev = {"kind": "audio", "seq": seq, "payload": text, "from": "alice"}

    other_call.receive(ev)                                              # same friends, different call
    ea.receive(dict(ev, **{"from": "bob"}))                             # reflected back at its own sender
    eb.receive(dict(ev, **{"from": "mallory"}))                         # claims to come from somebody else
    eb.receive(dict(ev, kind="video"))                                  # kind changed in transit
    eb.receive(dict(ev, seq=seq + 1))                                   # sequence number changed in transit
    flipped = bytearray(text.encode()); flipped[20] ^= 1
    eb.receive(dict(ev, payload=flipped.decode()))                      # one bit edited
    assert other_call._audio_in.empty() and ea._audio_in.empty() and eb._audio_in.empty()

    eb.receive(ev)                                                      # the genuine packet is accepted ...
    assert eb._audio_in.get_nowait() == b"hello"
    eb.receive(ev)                                                      # ... but only once
    assert eb._audio_in.empty()
    s2, t2 = ea.seal("audio", b"two"); s3, t3 = ea.seal("audio", b"three")
    eb.receive({"kind": "audio", "seq": s3, "payload": t3, "from": "alice"})
    eb.receive({"kind": "audio", "seq": s2, "payload": t2, "from": "alice"})   # older than one already played
    assert eb._audio_in.get_nowait() == b"three" and eb._audio_in.empty()
    for junk in ({"kind": "audio", "seq": "7", "payload": text}, {"kind": "audio", "seq": True, "payload": text},
                 {"kind": "audio", "seq": 99, "payload": 5}, {"kind": "x", "seq": 99, "payload": text},
                 {"kind": "audio", "seq": 99, "payload": "!!notbase64!!"}, {"kind": "audio", "seq": 99, "payload": "QQ=="}):
        eb.receive(junk)
    assert eb._audio_in.empty() and eb.stats["rejected"] >= 10


def test_empty_video_packet_means_camera_off(srv):
    pa, pb = pair(srv)
    got = []
    ea = EncryptedMediaEngine(pa.ctrl, "c1", "bob", "video")
    eb = EncryptedMediaEngine(pb.ctrl, "c1", "alice", "video", on_video=got.append)
    for payload in (b"\xff\xd8jpeg", b""):
        seq, text = ea.seal("video", payload)
        eb.receive({"kind": "video", "seq": seq, "payload": text, "from": "alice"})
    assert got == [b"\xff\xd8jpeg", b""]


# --------------------------------------------------------------------------- #
# a real voice call
# --------------------------------------------------------------------------- #
def test_voice_call_both_people_hear_each_other_and_it_is_encrypted_on_the_wire(tmp_path, audio):
    db = Database(sqlite_path=str(tmp_path / "w.db"))
    server = ServerThread(db).start()
    sniffer = Sniffer(server.port)
    try:
        via = type("Via", (), {"address": sniffer.address})()
        pa, pb = pair(via)
        a, b = Side(pa, ALICE_MIC, ALICE_SPK), Side(pb, BOB_MIC, BOB_SPK)
        connect_call(a, b)
        run([pa, pb], seconds=2.2)
        assert abs(audio.dominant_frequency(BOB_SPK) - 440) < 15, "bob must hear alice's voice (440 Hz)"
        assert abs(audio.dominant_frequency(ALICE_SPK) - 880) < 15, "alice must hear bob's voice (880 Hz)"
        assert audio.loudness(BOB_SPK) > 2000 and audio.loudness(ALICE_SPK) > 2000
        # 20 ms chunks: about 50 audio packets per second in each direction
        assert 40 <= a.engine.stats["audio_sent"] / 2.2 <= 60, a.engine.stats
        assert a.engine.stats["rejected"] == 0 and b.engine.stats["rejected"] == 0
        hang_up(a, b)
        wire = bytes(sniffer.captured)
        assert b'"call_media"' in wire
        first_block = (np.sin(2 * np.pi * 440 * np.arange(320) / 16000) * 12000).astype(np.int16).tobytes()
        import base64
        assert first_block not in wire and base64.b64encode(first_block)[:40] not in wire
    finally:
        sniffer.stop(); server.stop(); db.close()


def test_mute_stops_my_voice_only(call_pair, audio):
    a, b = call_pair
    connect_call(a, b)
    run([a.peer, b.peer], seconds=1.5)
    assert audio.loudness(BOB_SPK) > 2000
    a.engine.set_muted(True)
    run([a.peer, b.peer], seconds=1.2)
    assert audio.loudness(BOB_SPK, 0.4) < 100, "bob must hear silence while alice is muted"
    assert audio.loudness(ALICE_SPK, 0.4) > 2000, "alice still hears bob"
    a.engine.set_muted(False)
    run([a.peer, b.peer], seconds=1.2)
    assert audio.loudness(BOB_SPK, 0.4) > 2000
    hang_up(a, b)


def test_hang_up_releases_every_device_and_thread(call_pair, audio):
    a, b = call_pair
    connect_call(a, b)
    run([a.peer, b.peer], seconds=0.5)
    assert audio.live == 4
    hang_up(a, b)
    assert audio.live == 0
    for s in (a, b):
        assert not s.engine._sender_thread.is_alive() and not s.engine._video_thread.is_alive()


def test_ringing_flows_decline_cancel_no_answer_and_busy_through_the_controllers(call_pair, srv):
    a, b = call_pair
    pa, pb = a.peer, b.peer
    # declined
    assert a.ctrl.start_call("bob")
    run([pa, pb], until=lambda: b.ctrl.active_call, what="ring")
    b.ctrl.answer_call(b.ctrl.active_call["call_id"], False)
    run([pa, pb], until=lambda: "declined" in a.statuses and "declined" in b.statuses, what="declined")
    assert not a.ctrl.active_call and not b.ctrl.active_call
    # cancelled by the caller while it rings
    a.statuses.clear(); b.statuses.clear()
    assert a.ctrl.start_call("bob")
    run([pa, pb], until=lambda: b.ctrl.active_call and a.ctrl.active_call, what="ring again")
    a.ctrl.end_call()
    run([pa, pb], until=lambda: "cancelled" in b.statuses, what="cancelled")
    assert not a.ctrl.active_call and not b.ctrl.active_call
    # calling twice in a row (double click) must not report "busy"
    a.statuses.clear()
    assert a.ctrl.start_call("bob")
    assert not a.ctrl.start_call("bob")
    assert a.statuses[-1] == "in_call"
    run([pa, pb], until=lambda: b.ctrl.active_call, what="ring a third time")
    assert "busy" not in a.statuses
    a.ctrl.end_call()
    run([pa, pb], until=lambda: not a.ctrl.active_call and not b.ctrl.active_call, what="clean up")


def test_connection_loss_closes_the_call_on_both_sides(call_pair, audio):
    a, b = call_pair
    connect_call(a, b)
    a.ctrl.network._sock.close()                         # alice's network cable is pulled
    run([a.peer, b.peer], until=lambda: not a.ctrl.active_call and not b.ctrl.active_call, what="call closed")
    assert "connection_lost" in a.statuses and "connection_lost" in b.statuses


def test_a_message_about_another_call_is_ignored(call_pair):
    a, b = call_pair
    connect_call(a, b)
    keep = dict(a.ctrl.active_call)
    a.ctrl.handle({"type": "call_ended", "call_id": "not-my-call", "status": "completed"})
    a.ctrl.handle({"type": "call_accepted", "call_id": "not-my-call", "media": "video", "peer": "x"})
    a.ctrl.handle({"type": "call_media", "call_id": "not-my-call", "kind": "audio", "seq": 1, "payload": "AA=="})
    assert a.ctrl.active_call == keep
    hang_up(a, b)


def test_history_shows_direction_duration_and_missed(call_pair, srv):
    a, b = call_pair
    connect_call(a, b, "video")
    run([a.peer, b.peer], seconds=1.3)
    hang_up(a, b)
    assert a.ctrl.start_call("bob")                       # a call bob never answers, alice gives up
    run([a.peer, b.peer], until=lambda: b.ctrl.active_call, what="ring")
    a.ctrl.end_call()
    run([a.peer, b.peer], until=lambda: not b.ctrl.active_call, what="cancel")
    for s in (a, b):
        s.ctrl.request_call_history()
    run([a.peer, b.peer], until=lambda: len(a.ctrl.call_history) == 2 and len(b.ctrl.call_history) == 2, what="history")
    ra, rb = a.ctrl.call_rows(), b.ctrl.call_rows()
    assert [(r["outcome"], r["outgoing"]) for r in ra] == [("Cancelled", True), ("Outgoing", True)]
    assert [(r["outcome"], r["outgoing"], r["missed"]) for r in rb] == [("Missed", False, True), ("Incoming", False, False)]
    assert ra[1]["media"] == "video" and 1 <= ra[1]["duration"] <= 3 and ra[1]["duration_text"].startswith("0:0")
    assert ra[1]["username"] == "bob" and rb[1]["username"] == "alice" and ra[1]["when"]


# --------------------------------------------------------------------------- #
# refusals before a call starts
# --------------------------------------------------------------------------- #
def test_calls_are_refused_for_strangers_missing_keys_and_changed_safety_numbers(srv):
    pa, pb = pair(srv)
    log = []
    previous = pa.ctrl.on_update
    pa.ctrl.on_update = lambda t, p=None: (previous(t, p), log.append(p.get("status") if t == "call_status" else None))
    assert not pa.ctrl.start_call("nobody") and log[-1] == "invalid_peer"
    saved = pa.ctrl.peer_keys.pop("bob")
    assert not pa.ctrl.start_call("bob") and log[-1] == "no_key"
    pa.ctrl.peer_keys["bob"] = saved
    pa.ctrl.mark_verified("bob")                          # I compared safety numbers with bob ...
    assert pa.ctrl.call_block_reason("bob") is None
    pa.ctrl.peer_keys["bob"] = Identity("mallory", "another passphrase", 1000).public_key_b64   # ... then his key changes
    assert not pa.ctrl.start_call("bob") and log[-1] == "key_changed"
    assert pb.ctrl.active_call is None


def test_an_incoming_call_from_a_changed_key_is_declined_not_connected(srv):
    pa, pb = pair(srv)
    log = []
    previous = pb.ctrl.on_update
    pb.ctrl.on_update = lambda t, p=None: (previous(t, p), log.append(p.get("status") if t == "call_status" else None))
    assert pa.ctrl.start_call("bob")
    pb.ctrl.mark_verified("alice")
    run([pa, pb], until=lambda: pb.ctrl.active_call, what="ring")
    pb.ctrl.peer_keys["alice"] = Identity("mallory", "x y z", 1000).public_key_b64
    pb.ctrl.answer_call(pb.ctrl.active_call["call_id"], True)
    run([pa, pb], until=lambda: not pa.ctrl.active_call and not pb.ctrl.active_call, what="declined")
    assert "key_changed" in log and "declined" in [s for s in log if s]


# --------------------------------------------------------------------------- #
# video, camera on/off and the voice -> video upgrade
# --------------------------------------------------------------------------- #
def dominant_channel(jpg):
    img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
    return img.shape, int(np.argmax(img.reshape(-1, 3).mean(axis=0)))      # 0=B 1=G 2=R


def test_video_call_sends_320x240_jpeg_with_camera_on_and_off(call_pair, monkeypatch):
    cam = install_fake_camera(monkeypatch)
    a, b = call_pair
    connect_call(a, b, "video")
    run([a.peer, b.peer], seconds=2.0)
    assert b.video and a.video, "both sides must receive the other's frames"
    shape, ch = dominant_channel(b.video[-1])
    assert shape == (240, 320, 3) and ch == 2, "bob sees alice's camera (red) at 320x240"
    assert dominant_channel(a.video[-1])[1] == 1, "alice sees bob's camera (green)"
    fps = a.engine.stats["video_sent"] / 2.0
    assert 6 <= fps <= 14, f"about 10-12 frames per second, got {fps:.1f}"
    assert all(len(f) < 45_000 for f in b.video if f)
    # camera off: the other side is told, and the camera is really released
    a.engine.set_camera(False)
    run([a.peer, b.peer], until=lambda: b.video[-1] == b"", what="'camera off' marker")
    run([a.peer, b.peer], seconds=0.4)
    alice_cam = [c for c in cam.instances if c.index == 0][-1]
    assert alice_cam.released, "switching the camera off must free the camera (its light goes out)"
    n = len(b.video)
    run([a.peer, b.peer], seconds=0.5)
    assert len(b.video) == n, "no frames while the camera is off"
    a.engine.set_camera(True)
    run([a.peer, b.peer], until=lambda: len(b.video) > n and b.video[-1] != b"", what="camera on again")
    hang_up(a, b)
    run([a.peer, b.peer], seconds=0.3)
    assert all(c.released for c in cam.instances)


def test_voice_call_can_be_upgraded_to_video_and_never_opens_a_camera_before(call_pair, monkeypatch):
    cam = install_fake_camera(monkeypatch)
    a, b = call_pair
    connect_call(a, b, "audio")
    run([a.peer, b.peer], seconds=1.0)
    assert cam.instances == [] and not b.video, "a voice call must not touch the camera"
    a.engine.set_camera(True)
    run([a.peer, b.peer], until=lambda: b.video and b.video[-1] != b"", what="video after upgrade")
    assert dominant_channel(b.video[-1])[0] == (240, 320, 3)
    hang_up(a, b)


def test_missing_camera_is_reported_but_the_voice_call_carries_on(call_pair, audio, monkeypatch):
    cam = install_fake_camera(monkeypatch)
    cam.unavailable = {0}
    a, b = call_pair
    connect_call(a, b, "video")
    run([a.peer, b.peer], until=lambda: a.errors, what="camera error")
    assert "Camera 0 could not be opened" in a.errors[0] and "Privacy" in a.errors[0]
    assert a.engine.camera_enabled is False
    run([a.peer, b.peer], seconds=1.5)
    assert abs(audio.dominant_frequency(BOB_SPK) - 440) < 15, "voice must keep working without a camera"
    hang_up(a, b)


def test_wide_and_tall_camera_frames_are_cropped_not_squashed():
    wide = np.zeros((360, 640, 3), np.uint8)                       # a 16:9 webcam frame
    wide[:, :80] = wide[:, 560:] = (255, 0, 0)                     # blue edges (these must be trimmed away)
    wide[:, 80:560] = (0, 255, 0)                                  # green centre (this 4:3 part must be kept)
    img = cv2.imdecode(np.frombuffer(EncryptedMediaEngine.encode_frame(cv2, wide), np.uint8), cv2.IMREAD_COLOR)
    assert img.shape == (240, 320, 3)
    assert img[:, :, 1].mean() > 230 and img[:, :, 0].mean() < 25   # all centre, no edge
    tall = np.zeros((640, 360, 3), np.uint8)
    assert cv2.imdecode(np.frombuffer(EncryptedMediaEngine.encode_frame(cv2, tall), np.uint8), 1).shape == (240, 320, 3)


# --------------------------------------------------------------------------- #
# devices and failures
# --------------------------------------------------------------------------- #
def test_a_missing_chosen_microphone_falls_back_to_the_default(srv, monkeypatch):
    fake = FakeSoundDevice(TONES, bad_devices={99}).install(monkeypatch)
    pa, pb = pair(srv)
    a, b = Side(pa, 99, ALICE_SPK), Side(pb, BOB_MIC, BOB_SPK)
    fake.tones[None] = 440
    connect_call(a, b)
    assert a.engine.notes and "default" in a.engine.notes[0]
    run([pa, pb], seconds=1.5)
    assert abs(fake.dominant_frequency(BOB_SPK) - 440) < 15
    hang_up(a, b)


def test_no_working_audio_device_gives_a_friendly_error(srv, monkeypatch):
    FakeSoundDevice(TONES, bad_devices={99, None}).install(monkeypatch)
    pa, pb = pair(srv)
    engine = EncryptedMediaEngine(pa.ctrl, "c", "bob", "audio", audio_input=99)
    with pytest.raises(MediaUnavailable) as err:
        engine.start()
    assert "microphone" in str(err.value).lower() and "Privacy" in str(err.value)


def test_missing_calling_packages_say_what_to_install(srv, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    pa, _ = pair(srv)
    with pytest.raises(MediaUnavailable) as err:
        EncryptedMediaEngine(pa.ctrl, "c", "bob", "audio").start()
    assert "requirements-media.txt" in str(err.value)


def test_stereo_only_speakers_work(srv, monkeypatch):
    fake = FakeSoundDevice(TONES, stereo_only={BOB_SPK}).install(monkeypatch)
    pa, pb = pair(srv)
    a, b = Side(pa, ALICE_MIC, ALICE_SPK), Side(pb, BOB_MIC, BOB_SPK)
    connect_call(a, b)
    run([pa, pb], seconds=1.5)
    assert ("Output", BOB_SPK, 2) in fake.opened
    assert abs(fake.dominant_frequency(BOB_SPK) - 440) < 15
    hang_up(a, b)


def test_a_failing_send_never_kills_the_call(srv, audio):
    pa, pb = pair(srv)
    a = Side(pa, ALICE_MIC, ALICE_SPK)
    real, calls = pa.ctrl.send_call_media, []

    def flaky(*args):
        calls.append(args)
        if len(calls) <= 5:
            raise OSError("network hiccup")
        return real(*args)
    pa.ctrl.send_call_media = flaky
    pa.ctrl.active_call = {"call_id": "c", "caller": "alice", "callee": "bob", "media": "audio", "status": "active"}
    eng = a.start_engine()
    time.sleep(0.8)
    assert len(calls) > 15 and eng._sender_thread.is_alive(), "the sender thread must survive errors"
    eng.stop()


def test_audio_latency_stays_bounded_when_packets_arrive_in_a_burst(srv, audio):
    pa, pb = pair(srv)
    ea = EncryptedMediaEngine(pa.ctrl, "c", "bob", "audio")
    eb = EncryptedMediaEngine(pb.ctrl, "c", "alice", "audio")
    for _ in range(200):                                   # two whole seconds of audio at once
        seq, text = ea.seal("audio", b"\x01\x00" * 320)
        eb.receive({"kind": "audio", "seq": seq, "payload": text, "from": "alice"})
    assert eb._audio_in.qsize() <= 10, "never queue more than 200 ms of audio"
