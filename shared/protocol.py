"""Message protocol shared by the client and the server.

Transport: a plain TCP stream. Every message ("frame") is

    [ 4-byte big-endian length ][ UTF-8 JSON object of that many bytes ]

and the JSON object always has a "type" field:

    {"type": "<name>", ...fields...}

Client -> server types
    register, login, logout, ping,
    friend_request, friend_respond, friend_remove,
    send_dm, send_group, history,
    group_create, group_add, group_remove, group_leave

Server -> client types
    register_result, login_result, pong, state, presence,
    friend_request_in, request_sent, request_cleared, friend_added, friend_removed,
    message, group_message, history,
    group_update, group_removed,
    notice, error, kicked, logged_out
"""
import json
import re
import select
import socket
import struct
import time

# ---- limits / validation ---------------------------------------------------
DEFAULT_PORT = 8765
USERNAME_RE = re.compile(r"^[a-z0-9_]{3,20}$")
MIN_PASSWORD = 8
MAX_PASSWORD = 64
MAX_PASSWORD_BYTES = 72          # bcrypt hard limit
MAX_FULLNAME = 50
MAX_GROUP_NAME = 40
MAX_GROUP_MEMBERS = 50
MAX_PAYLOAD = 32_768             # max length of an encrypted message (text characters)
MAX_FRAME = 131_072              # max size of one frame body (bytes)
HISTORY_LIMIT = 200
PUBLIC_KEY_BYTES = 32            # X25519 public key

HEADER = struct.Struct(">I")

# ---- user-facing messages (taken from Chapter 3, section 3.13) -------------
MSG_REGISTERED = "Registration completed successfully."
MSG_LOGIN_OK = "Login successful."
MSG_LOGIN_BAD = "Invalid username or password."
MSG_USER_EXISTS = "Username already exists."
MSG_PASSWORD_MISMATCH = "Passwords do not match."
MSG_NETWORK = "Network unavailable."
MSG_CONN_FAILED = "Connection failed."
MSG_PASSPHRASE_BAD = "That passphrase does not belong to this account."
MSG_NO_KEY = ("This account was created by an older version and has no passphrase. "
              "Please register a new account.")


class ProtocolError(Exception):
    pass


class FrameTooLarge(ProtocolError):
    pass


class IdleTimeout(TimeoutError):
    """No data arrived for the allowed time."""


class ConnectionClosed(ConnectionError):
    """The peer closed the connection."""


# --------------------------------------------------------------------------- #
# JSON <-> frames
# --------------------------------------------------------------------------- #
def encode(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def decode(raw) -> dict:
    """Turn one frame body (bytes, or str) into a dict with a string 'type'."""
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = bytes(raw).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("Malformed message.") from exc
    if not isinstance(raw, str):
        raise ProtocolError("Malformed message.")
    try:
        obj = json.loads(raw)
    except ValueError as exc:
        raise ProtocolError("Malformed message.") from exc
    if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
        raise ProtocolError("Malformed message.")
    return obj


def encode_frame(obj: dict) -> bytes:
    data = encode(obj).encode("utf-8")
    if len(data) > MAX_FRAME:
        raise FrameTooLarge("Message is too large.")
    return HEADER.pack(len(data)) + data


def send_frame(sock, obj: dict) -> None:
    sock.sendall(encode_frame(obj))


class FrameReader:
    """Reads length-prefixed frames from a socket.

    Partial data is kept between calls, so a timeout never corrupts the stream.
    """

    def __init__(self, sock):
        self.sock = sock
        self.buf = bytearray()

    def _pop(self):
        if len(self.buf) < HEADER.size:
            return None
        (size,) = HEADER.unpack_from(self.buf)
        if size > MAX_FRAME:
            raise FrameTooLarge("Message is too large.")
        if len(self.buf) < HEADER.size + size:
            return None
        body = bytes(self.buf[HEADER.size:HEADER.size + size])
        del self.buf[:HEADER.size + size]
        return body

    def read(self, idle=None, poll=1.0):
        """Return the next frame body (bytes), or None if the peer closed cleanly.

        idle: give up with IdleTimeout after this many seconds without any data.
        """
        last = time.monotonic()
        while True:
            body = self._pop()
            if body is not None:
                return body
            wait = poll
            if idle is not None:
                left = idle - (time.monotonic() - last)
                if left <= 0:
                    raise IdleTimeout("no data received")
                wait = min(poll, left)
            try:
                ready, _, _ = select.select([self.sock], [], [], wait)
                if not ready:
                    continue
                chunk = self.sock.recv(65536)
            except (OSError, ValueError) as exc:          # closed under our feet
                raise ConnectionClosed(str(exc)) from exc
            if not chunk:
                if self.buf:
                    raise ConnectionClosed("connection closed in the middle of a message")
                return None
            self.buf += chunk
            last = time.monotonic()


# --------------------------------------------------------------------------- #
# server address  (host:port)
# --------------------------------------------------------------------------- #
_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")
_V6_RE = re.compile(r"^[0-9A-Fa-f:.]+$")


def parse_address(text, default_port=DEFAULT_PORT):
    """'localhost:8765' / '192.168.1.5' / '[::1]:9000'  ->  (host, port). Raises ValueError."""
    s = (text or "").strip()
    s = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", s)       # tolerate a leftover "scheme://" prefix
    s = s.split("/")[0].strip()
    if not s:
        raise ValueError("empty address")
    port = default_port
    if s.startswith("["):
        m = re.fullmatch(r"\[([0-9A-Fa-f:.]+)\](?::(\d+))?", s)
        if not m:
            raise ValueError("bad address")
        host = m.group(1)
        if m.group(2):
            port = int(m.group(2))
    elif s.count(":") == 1:
        host, _, p = s.partition(":")
        if not p.isdigit():
            raise ValueError("bad port")
        port = int(p)
    elif s.count(":") > 1:                                    # bare IPv6 address
        if not _V6_RE.match(s):
            raise ValueError("bad address")
        host = s
    else:
        host = s
    if ":" not in host and not _HOST_RE.match(host):
        raise ValueError("bad host")
    if not 1 <= port <= 65535:
        raise ValueError("bad port")
    return host, port


def format_address(host, port):
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def normalize_address(text):
    """Return 'host:port' for friendly input, '' for blank input; ValueError if invalid."""
    if not (text or "").strip():
        return ""
    return format_address(*parse_address(text))


def format_address_error():
    return "Invalid server address. Use host:port, for example localhost:8765"


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def normalize_username(value) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def validate_registration(username, password, confirm, fullname):
    """Return an error string, or None if the registration data is valid."""
    if not isinstance(fullname, str) or not fullname.strip():
        return "Full name is required."
    if len(fullname.strip()) > MAX_FULLNAME:
        return f"Full name must be at most {MAX_FULLNAME} characters."
    if not USERNAME_RE.match(username or ""):
        return "Username must be 3-20 characters: letters, numbers or underscore."
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    if len(password) > MAX_PASSWORD or len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return f"Password must be at most {MAX_PASSWORD} characters."
    if password != confirm:
        return MSG_PASSWORD_MISMATCH
    return None


def validate_public_key(value):
    """Return an error string, or None if value is a base64 X25519 public key."""
    import base64
    import binascii
    if not isinstance(value, str) or not value or len(value) > 64:
        return "Encryption key is missing."
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return "Encryption key is invalid."
    if len(raw) != PUBLIC_KEY_BYTES:
        return "Encryption key is invalid."
    return None


# ---- additional authenticated data (binds ciphertext to its conversation) --
def dm_aad(sender: str, receiver: str) -> str:
    return f"dm|{sender}|{receiver}"


def group_aad(sender: str, group_id: int) -> str:
    return f"group|{sender}|{int(group_id)}"


def wrap_aad(sender: str, group_id: int, recipient: str) -> str:
    return f"wrap|{sender}|{int(group_id)}|{recipient}"
