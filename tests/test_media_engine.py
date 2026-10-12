"""Media engine unit tests (no devices needed): packet format, queues, device preferences."""
from client.media_engine import EncryptedMediaEngine


class FakeIdentity:
    def __init__(self, username):
        self.username = username

    def pair_key(self, peer_public, peer):
        return b"K" * 32


class FakeController:
    def __init__(self, me="alice", peer="bob"):
        self.identity = FakeIdentity(me)
        self.peer_keys = {peer: "fake-public-key"}
        self.sent = []

    def send_call_media(self, call_id, kind, seq, payload):
        self.sent.append({"call_id": call_id, "kind": kind, "seq": seq, "payload": payload})
        return True


def pair_of_engines(call_id="call-123"):
    a = EncryptedMediaEngine(FakeController("alice", "bob"), call_id, "bob", "audio")
    b = EncryptedMediaEngine(FakeController("bob", "alice"), call_id, "alice", "audio")
    return a, b


def test_encrypted_audio_packet_round_trip():
    a, b = pair_of_engines()
    original = b"pcm-audio-test-frame"
    seq, text = a.seal("audio", original)
    assert original not in text.encode()
    b.receive({"call_id": "call-123", "kind": "audio", "seq": seq, "payload": text, "from": "alice"})
    assert b._audio_in.get_nowait() == original


def test_garbage_packet_is_dropped():
    a, b = pair_of_engines("call-456")
    b.receive({"call_id": "call-456", "kind": "audio", "seq": 1, "payload": "not-valid-ciphertext"})
    assert b._audio_in.empty() and b.stats["rejected"] == 1


def test_media_out_queue_is_bounded():
    engine = EncryptedMediaEngine(FakeController(), "call-789", "bob", "audio")
    for i in range(1000):
        engine._send_packet("audio", bytes([i % 255]))
    assert engine._media_out.qsize() <= 40


def test_call_device_preferences_round_trip_and_clamp(tmp_path, monkeypatch):
    from client.prefs import Prefs
    prefs = Prefs(path=str(tmp_path / "prefs.json"))
    prefs.set_call_devices("alice", audio_input=3, audio_output="4", camera_index=99)
    loaded = prefs.call_devices("alice")
    assert loaded == {"audio_input": 3, "audio_output": 4, "camera_index": 4}
    prefs.set_call_devices("alice", audio_input="Default", audio_output=None, camera_index="bad")
    loaded = prefs.call_devices("alice")
    assert loaded == {"audio_input": None, "audio_output": None, "camera_index": 0}


def test_media_engine_accepts_device_configuration():
    engine = EncryptedMediaEngine(FakeController(), "call-device", "bob", "audio",
                                  audio_input=2, audio_output=5, camera_index=3)
    assert engine.audio_input == 2
    assert engine.audio_output == 5
    assert engine.camera_index == 3
