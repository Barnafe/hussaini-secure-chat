"""Phase 1 - cryptography tests (AES-256-GCM, PBKDF2, bcrypt)."""
import base64

import pytest

from shared.crypto_utils import (DecryptionError, decrypt_message, derive_key, encrypt_message,
                                 hash_password, verify_password)

SALT = b"test-salt"
KEY = derive_key("a secret phrase", SALT, iterations=1000)


def test_key_is_256_bits_and_deterministic():
    assert len(KEY) == 32
    assert derive_key("a secret phrase", SALT, iterations=1000) == KEY
    assert derive_key("another passphrase", SALT, iterations=1000) != KEY


def test_empty_passphrase_rejected():
    with pytest.raises(ValueError):
        derive_key("", SALT)


def test_roundtrip_including_unicode():
    for text in ["hello", "", "Sannu, ina kwana? \u2713 \U0001F600", "x" * 5000]:
        token = encrypt_message(KEY, text, "aad")
        assert decrypt_message(KEY, token, "aad") == text


def test_ciphertext_does_not_contain_plaintext():
    token = encrypt_message(KEY, "top secret exam answers", "a")
    assert b"top secret" not in base64.b64decode(token)
    assert "secret" not in token


def test_same_message_encrypts_differently_each_time():
    a = encrypt_message(KEY, "same text", "a")
    b = encrypt_message(KEY, "same text", "a")
    assert a != b                                   # random nonce


def test_wrong_key_fails():
    token = encrypt_message(KEY, "hello", "a")
    with pytest.raises(DecryptionError):
        decrypt_message(derive_key("wrong passphrase", SALT, iterations=1000), token, "a")


def test_tampering_is_detected():
    raw = bytearray(base64.b64decode(encrypt_message(KEY, "hello world", "a")))
    for position in (0, 12, 20, len(raw) - 1):      # nonce, tag, ciphertext
        bad = bytearray(raw)
        bad[position] ^= 0x01
        with pytest.raises(DecryptionError):
            decrypt_message(KEY, base64.b64encode(bytes(bad)).decode(), "a")


def test_aad_binds_message_to_conversation():
    token = encrypt_message(KEY, "pay me", "dm|alice|bob")
    with pytest.raises(DecryptionError):
        decrypt_message(KEY, token, "dm|alice|carol")      # replayed to a different chat


@pytest.mark.parametrize("bad", ["", "not base64!!", "AAAA", None])
def test_malformed_tokens_rejected(bad):
    with pytest.raises(DecryptionError):
        decrypt_message(KEY, bad, "a")


def test_bcrypt_hash_and_verify():
    h = hash_password("Passw0rd!", rounds=4)
    assert h.startswith("$2") and "Passw0rd!" not in h
    assert verify_password("Passw0rd!", h)
    assert not verify_password("passw0rd!", h)
    assert not verify_password("x", "not-a-hash")
    assert hash_password("Passw0rd!", rounds=4) != h        # random salt


def test_wire_format_is_nonce_tag_ciphertext():
    raw = base64.b64decode(encrypt_message(KEY, "abc", "a"))
    assert len(raw) == 12 + 16 + 3                 # nonce | tag | ciphertext (same length as plaintext)
