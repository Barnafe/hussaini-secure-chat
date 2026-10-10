"""Wire protocol: framing over real sockets, address parsing, validation."""
import socket
import struct

import pytest

from shared import protocol as P


def pair():
    a, b = socket.socketpair()
    return a, b


def test_frame_roundtrip_with_unicode():
    a, b = pair()
    obj = {"type": "x", "text": "Sannu \u2713 \U0001F600"}
    P.send_frame(a, obj)
    assert P.decode(P.FrameReader(b).read(idle=2)) == obj


def test_reader_handles_split_and_back_to_back_frames():
    a, b = pair()
    r = P.FrameReader(b)
    data = P.encode_frame({"type": "one"}) + P.encode_frame({"type": "two"})
    a.sendall(data[:3])
    with pytest.raises(P.IdleTimeout):
        r.read(idle=0.2, poll=0.05)                       # half a header: nothing lost, just waiting
    a.sendall(data[3:9])
    with pytest.raises(P.IdleTimeout):
        r.read(idle=0.2, poll=0.05)
    a.sendall(data[9:])
    assert P.decode(r.read(idle=2))["type"] == "one"
    assert P.decode(r.read(idle=2))["type"] == "two"


def test_clean_close_returns_none_and_midframe_close_raises():
    a, b = pair()
    a.close()
    assert P.FrameReader(b).read(idle=2) is None
    a, b = pair()
    a.sendall(P.HEADER.pack(50) + b"abc")
    a.close()
    with pytest.raises(P.ConnectionClosed):
        P.FrameReader(b).read(idle=2)


def test_oversized_frame_is_refused_without_reading_the_body():
    a, b = pair()
    a.sendall(struct.pack(">I", P.MAX_FRAME + 1))
    with pytest.raises(P.FrameTooLarge):
        P.FrameReader(b).read(idle=2)
    with pytest.raises(P.FrameTooLarge):
        P.encode_frame({"type": "x", "p": "A" * (P.MAX_FRAME + 10)})


@pytest.mark.parametrize("raw", [b"not json", b"[]", b'{"no_type": 1}', b'{"type": 5}', b"\xff\xfe\x00"])
def test_decode_rejects_malformed(raw):
    with pytest.raises(P.ProtocolError):
        P.decode(raw)


@pytest.mark.parametrize("text,expected", [
    ("localhost:8765", "localhost:8765"),
    ("localhost", "localhost:8765"),
    ("  192.168.1.20:9000 ", "192.168.1.20:9000"),
    ("my-pc.local", "my-pc.local:8765"),
    ("ws://localhost:8765/", "localhost:8765"),
    ("[::1]:9000", "[::1]:9000"),
    ("   ", ""),
    ("", ""),
])
def test_address_normalisation(text, expected):
    assert P.normalize_address(text) == expected


@pytest.mark.parametrize("bad", ["host:abc", "host:0", "host:70000", "-bad:1", "a b", "host:1:2x", "[::1"])
def test_bad_addresses_rejected(bad):
    with pytest.raises(ValueError):
        P.normalize_address(bad)


def test_public_key_validation():
    assert P.validate_public_key("A" * 43 + "=") is None
    for bad in (None, "", "short", "!" * 44, "A" * 100):
        assert P.validate_public_key(bad)
