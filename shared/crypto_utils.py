"""Cryptography helpers shared by the client and the server.

* AES-256-GCM encrypts every chat message on the sender's machine. GCM gives
  confidentiality AND tamper detection (the 16-byte authentication tag).
* PBKDF2-HMAC-SHA256 stretches a passphrase into key material.
* bcrypt hashes account passwords (server side only).

Wire format of an encrypted message (base64 text):

    base64( nonce[12] | tag[16] | ciphertext[n] )

A fresh random nonce is generated for every message. The "aad" (additional
authenticated data) binds a ciphertext to its sender and conversation, so a
message cannot be silently replayed into a different chat.

(Per-user passphrases and key agreement live in shared/keys.py.)
"""
import base64
import binascii
import hashlib
import os

import bcrypt
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_SIZE = 32          # 32 bytes = 256 bits  -> AES-256
NONCE_SIZE = 12        # recommended GCM nonce size
TAG_SIZE = 16          # GCM authentication tag
PBKDF2_ITERATIONS = 600_000


class DecryptionError(Exception):
    """Raised when a message cannot be decrypted or fails authentication."""


# --------------------------------------------------------------------------- #
# Key derivation
# --------------------------------------------------------------------------- #
def derive_key(passphrase: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    """Stretch a passphrase into 32 bytes (PBKDF2-HMAC-SHA256)."""
    if not passphrase:
        raise ValueError("Passphrase must not be empty.")
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt,
                               iterations, dklen=KEY_SIZE)


# --------------------------------------------------------------------------- #
# AES-256-GCM
# --------------------------------------------------------------------------- #
def encrypt_message(key: bytes, plaintext: str, aad: str = "") -> str:
    if len(key) != KEY_SIZE:
        raise ValueError("Key must be 32 bytes (AES-256).")
    nonce = os.urandom(NONCE_SIZE)
    sealed = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
    ciphertext, tag = sealed[:-TAG_SIZE], sealed[-TAG_SIZE:]
    return base64.b64encode(nonce + tag + ciphertext).decode("ascii")


def decrypt_message(key: bytes, token: str, aad: str = "") -> str:
    if len(key) != KEY_SIZE:
        raise ValueError("Key must be 32 bytes (AES-256).")
    try:
        raw = base64.b64decode(token.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError, AttributeError) as exc:
        raise DecryptionError("Malformed encrypted message.") from exc
    if len(raw) < NONCE_SIZE + TAG_SIZE:
        raise DecryptionError("Encrypted message is too short.")
    nonce = raw[:NONCE_SIZE]
    tag = raw[NONCE_SIZE:NONCE_SIZE + TAG_SIZE]
    ciphertext = raw[NONCE_SIZE + TAG_SIZE:]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext + tag, aad.encode("utf-8")).decode("utf-8")
    except (InvalidTag, UnicodeDecodeError) as exc:
        raise DecryptionError(
            "Authentication failed (wrong key or tampered message).") from exc


# --------------------------------------------------------------------------- #
# Password hashing (bcrypt)
# --------------------------------------------------------------------------- #
def hash_password(password: str, rounds: int = 12) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=rounds)).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False
