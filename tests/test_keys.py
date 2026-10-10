"""Per-user passphrases, key pairs and end-to-end envelopes (shared/keys.py)."""
import json

import pytest

from shared.crypto_utils import DecryptionError
from shared.keys import (ALPHABET, Identity, PeerKeyError, generate_passphrase, normalize_passphrase,
                         open_dm, open_group, seal_dm, seal_group)

IT = 1000


def test_generated_passphrase_format_and_randomness():
    seen = {generate_passphrase() for _ in range(50)}
    assert len(seen) == 50
    p = next(iter(seen))
    groups = p.split("-")
    assert len(groups) == 5 and all(len(g) == 4 for g in groups)
    assert all(ch in ALPHABET for ch in p.replace("-", ""))


def test_passphrase_input_is_forgiving():
    p = generate_passphrase()
    assert normalize_passphrase(p.lower()) == normalize_passphrase(p)
    assert normalize_passphrase(" " + p.replace("-", " ") + " ") == normalize_passphrase(p)
    assert normalize_passphrase("") == "" and normalize_passphrase(None) == ""


def test_identity_is_deterministic_and_username_bound():
    p = generate_passphrase()
    a1, a2 = Identity("alice", p, IT), Identity("alice", p.lower(), IT)
    assert a1.public_key_b64 == a2.public_key_b64
    assert Identity("bob", p, IT).public_key_b64 != a1.public_key_b64      # same phrase, other user
    assert Identity("alice", generate_passphrase(), IT).public_key_b64 != a1.public_key_b64
    with pytest.raises(ValueError):
        Identity("alice", "  - ", IT)


def test_two_users_derive_the_same_pair_key():
    a, b = Identity("alice", generate_passphrase(), IT), Identity("bob", generate_passphrase(), IT)
    assert a.pair_key(b.public_key_b64, "bob") == b.pair_key(a.public_key_b64, "alice")
    c = Identity("carol", generate_passphrase(), IT)
    assert a.pair_key(c.public_key_b64, "carol") != a.pair_key(b.public_key_b64, "bob")


def test_dm_roundtrip_and_outsider_cannot_read():
    a, b, c = (Identity(n, generate_passphrase(), IT) for n in ("alice", "bob", "carol"))
    token = seal_dm(a, b.public_key_b64, "bob", "see you at 5")
    assert open_dm(b, a.public_key_b64, "alice", "bob", token) == "see you at 5"
    assert open_dm(a, b.public_key_b64, "alice", "bob", token) == "see you at 5"   # sender reads own history
    with pytest.raises(DecryptionError):
        open_dm(c, a.public_key_b64, "alice", "bob", token)                      # carol has no key
    with pytest.raises(DecryptionError):
        open_dm(b, a.public_key_b64, "mallory", "bob", token)                    # wrong claimed sender


def test_bad_peer_key_is_reported():
    a = Identity("alice", generate_passphrase(), IT)
    for bad in ("", "not base64!!", "AAAA", "A" * 43 + "="):
        with pytest.raises(PeerKeyError):
            seal_dm(a, bad, "bob", "x")


def test_group_roundtrip_membership_and_tampering():
    a, b, c = (Identity(n, generate_passphrase(), IT) for n in ("alice", "bob", "carol"))
    keys = {"alice": a.public_key_b64, "bob": b.public_key_b64}
    token = seal_group(a, 7, "team plan", keys)
    assert open_group(b, 7, "alice", token, a.public_key_b64) == "team plan"
    assert open_group(a, 7, "alice", token) == "team plan"                      # sender reads own message
    with pytest.raises(DecryptionError):
        open_group(c, 7, "alice", token, a.public_key_b64)                     # carol was not a member
    with pytest.raises(DecryptionError):
        open_group(b, 8, "alice", token, a.public_key_b64)                     # replayed into another group
    with pytest.raises(DecryptionError):
        open_group(b, 7, "mallory", token, a.public_key_b64)                   # wrong sender
    data = json.loads(token)
    data["c"] = data["c"][:-4] + ("AAAA" if not data["c"].endswith("AAAA") else "BBBB")
    with pytest.raises(DecryptionError):
        open_group(b, 7, "alice", json.dumps(data), a.public_key_b64)
    for junk in ("not json", "[]", "{}", '{"c": 1, "k": {}}'):
        with pytest.raises(DecryptionError):
            open_group(b, 7, "alice", junk, a.public_key_b64)


def test_group_fallback_to_embedded_sender_key():
    a, b = Identity("alice", generate_passphrase(), IT), Identity("bob", generate_passphrase(), IT)
    token = seal_group(a, 1, "hi", {"alice": a.public_key_b64, "bob": b.public_key_b64})
    assert open_group(b, 1, "alice", token, None) == "hi"                       # former member: key from payload
