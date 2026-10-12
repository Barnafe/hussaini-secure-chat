"""Per-user passphrases, key pairs and end-to-end message envelopes.

How it works
------------
* When someone registers, the client invents a random **passphrase** for them
  (``generate_passphrase``) and shows it once. The passphrase never leaves the computer.
* The passphrase is stretched with PBKDF2 into a private X25519 key
  (``Identity``). Only the matching **public key** is sent to the server.
* Two friends derive the same AES-256 key from "my private key + your public key"
  (X25519 key agreement, then HKDF). The server only ever holds public keys, so
  it can never read a message.
* Group messages are encrypted once with a random message key; that key is then
  wrapped separately for every member (``seal_group`` / ``open_group``).
  Nobody who was not a member when a message was sent can open it.
"""
import base64
import binascii
import hashlib
import json
import os
import re
import secrets

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from shared import protocol as P
from shared.crypto_utils import (KEY_SIZE, PBKDF2_ITERATIONS, DecryptionError, decrypt_message,
                                 encrypt_message)

APP_SALT = b"Hussaini-SecureChat-v4"
# 32 symbols = exactly 5 bits each; no 0/O/1/I so the passphrase is easy to copy by eye.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PASSPHRASE_GROUPS = 5
PASSPHRASE_GROUP_LEN = 4          # 20 symbols = 100 bits of randomness


class PeerKeyError(DecryptionError):
    """The other person's public key is missing or unusable."""


# --------------------------------------------------------------------------- #
# passphrases
# --------------------------------------------------------------------------- #
def generate_passphrase() -> str:
    """A random passphrase such as 'K7QM-X2NP-H9TD-R4WB-C6VF'."""
    groups = ("".join(secrets.choice(ALPHABET) for _ in range(PASSPHRASE_GROUP_LEN))
              for _ in range(PASSPHRASE_GROUPS))
    return "-".join(groups)


def normalize_passphrase(text) -> str:
    """Forgiving input: ignore case, spaces and dashes."""
    return re.sub(r"[\s_-]+", "", text).upper() if isinstance(text, str) else ""


# --------------------------------------------------------------------------- #
# identity (key pair derived from the passphrase)
# --------------------------------------------------------------------------- #
class Identity:
    def __init__(self, username: str, passphrase: str, iterations: int = PBKDF2_ITERATIONS):
        pw = normalize_passphrase(passphrase)
        if not pw:
            raise ValueError("Passphrase must not be empty.")
        salt = APP_SALT + b"|identity|" + username.encode("utf-8")
        seed = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iterations, dklen=32)
        self.username = username
        self._private = X25519PrivateKey.from_private_bytes(seed)
        raw_public = self._private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.public_key_b64 = base64.b64encode(raw_public).decode("ascii")
        self._pair_keys = {}

    def pair_key(self, peer_public_b64: str, peer_username: str) -> bytes:
        """The AES key shared by me and ``peer_username`` (same on both sides)."""
        cache_id = (peer_username, peer_public_b64)
        cached = self._pair_keys.get(cache_id)
        if cached:
            return cached
        try:
            raw = base64.b64decode(peer_public_b64.encode("ascii"), validate=True)
            shared = self._private.exchange(X25519PublicKey.from_public_bytes(raw))
        except (binascii.Error, ValueError, TypeError, AttributeError, UnicodeEncodeError) as exc:
            raise PeerKeyError(f"No usable encryption key for {peer_username}.") from exc
        names = sorted([self.username, peer_username])
        info = ("dm|" + "|".join(names)).encode("utf-8")
        key = HKDF(algorithm=hashes.SHA256(), length=KEY_SIZE, salt=APP_SALT,
                   info=info).derive(shared)
        self._pair_keys[cache_id] = key
        return key


# --------------------------------------------------------------------------- #
# direct messages
# --------------------------------------------------------------------------- #
def seal_dm(identity: Identity, peer_public_b64: str, peer: str, text: str) -> str:
    key = identity.pair_key(peer_public_b64, peer)
    return encrypt_message(key, text, P.dm_aad(identity.username, peer))


def open_dm(identity: Identity, peer_public_b64: str, sender: str, receiver: str, token: str) -> str:
    peer = receiver if sender == identity.username else sender
    key = identity.pair_key(peer_public_b64, peer)
    return decrypt_message(key, token, P.dm_aad(sender, receiver))


# --------------------------------------------------------------------------- #
# group messages
# --------------------------------------------------------------------------- #
def seal_group(identity: Identity, group_id: int, text: str, members: dict) -> str:
    """members: {username: public_key_b64} for everybody who should be able to read it
    (must include the sender so that the sender can read their own history)."""
    me = identity.username
    message_key = os.urandom(KEY_SIZE)
    sealed = encrypt_message(message_key, text, P.group_aad(me, group_id))
    wrapped = {}
    for user, public_b64 in members.items():
        pair = identity.pair_key(public_b64, user)
        wrapped[user] = encrypt_message(pair, base64.b64encode(message_key).decode("ascii"),
                                        P.wrap_aad(me, group_id, user))
    return json.dumps({"v": 1, "s": identity.public_key_b64, "c": sealed, "k": wrapped},
                      separators=(",", ":"))


def open_group(identity: Identity, group_id: int, sender: str, payload: str,
               sender_public_b64: str = None) -> str:
    """Decrypt a group payload. ``sender_public_b64`` (from the server's member list) is
    preferred; the key embedded in the payload is only a fallback for former members."""
    try:
        data = json.loads(payload)
        sealed, wrapped = data["c"], data["k"]
        embedded = data.get("s")
        my_wrap = wrapped[identity.username]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise DecryptionError("This message was not addressed to you.") from exc
    sender_key = sender_public_b64 or embedded
    if not isinstance(sender_key, str) or not isinstance(sealed, str) or not isinstance(my_wrap, str):
        raise DecryptionError("Malformed encrypted message.")
    pair = identity.pair_key(sender_key, sender)
    message_key_b64 = decrypt_message(pair, my_wrap, P.wrap_aad(sender, group_id, identity.username))
    try:
        message_key = base64.b64decode(message_key_b64.encode("ascii"), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DecryptionError("Malformed encrypted message.") from exc
    return decrypt_message(message_key, sealed, P.group_aad(sender, group_id))


# --------------------------------------------------------------------------- #
# Safety numbers (let two friends confirm they hold each other's real keys)
# --------------------------------------------------------------------------- #
def safety_number(public_a_b64: str, public_b_b64: str) -> str:
    """30 digits in six groups of five. Both friends compute the SAME number from the two public
    keys; if someone in the middle swapped a key, the numbers would not match."""
    import hashlib
    a, b = sorted([public_a_b64, public_b_b64])
    digest = hashlib.sha256(b"hussaini-safety-number-v1|" + a.encode() + b"|" + b.encode()).digest()
    digits = str(int.from_bytes(digest, "big") % 10 ** 30).zfill(30)
    return " ".join(digits[i:i + 5] for i in range(0, 30, 5))

