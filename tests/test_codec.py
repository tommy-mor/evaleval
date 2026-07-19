"""Unit tests for length-prefixed path key encoding."""

from evaleval.codec import (
    child_key,
    child_scan_prefix,
    meta_key,
    order_i64,
    order_u64,
    prefix_upper_bound,
    put_segment,
    put_uvarint,
    read_segment,
    read_uvarint,
)


def test_uvarint_roundtrip():
    for value in [0, 1, 127, 128, 300, 16384, 2**32 - 1, 2**63]:
        buf = bytearray()
        put_uvarint(buf, value)
        decoded, used = read_uvarint(bytes(buf))
        assert decoded == value
        assert used == len(buf)


def test_read_uvarint_rejects_truncated():
    assert read_uvarint(b"\x80") is None
    assert read_uvarint(b"") is None


def test_segment_roundtrip_and_self_delimiting():
    buf = bytearray()
    put_segment(buf, b"alpha")
    put_segment(buf, b"")
    put_segment(buf, bytes([0x00, 0xFF, 0x01]))

    a, n1 = read_segment(bytes(buf))
    assert a == b"alpha"
    b, n2 = read_segment(bytes(buf)[n1:])
    assert b == b""
    c, _ = read_segment(bytes(buf)[n1 + n2 :])
    assert c == bytes([0x00, 0xFF, 0x01])

def test_segment_no_false_prefix():
    a = bytearray()
    put_segment(a, b"a")
    ab = bytearray()
    put_segment(ab, b"ab")
    assert not bytes(ab).startswith(bytes(a))


def test_upper_bound_basics():
    assert prefix_upper_bound(bytes([1, 2, 3])) == bytes([1, 2, 4])
    assert prefix_upper_bound(bytes([1, 2, 0xFF])) == bytes([1, 3])
    assert prefix_upper_bound(bytes([0xFF, 0xFF])) is None
    assert prefix_upper_bound(b"") is None


def test_order_i64_is_monotonic():
    values = sorted([-5, -1, 0, 1, 5, -(2**63), (2**63) - 1])
    for a, b in zip(values, values[1:]):
        assert order_i64(a) < order_i64(b)


def test_order_u64_is_monotonic():
    for a, b in [(0, 1), (1, 2), (255, 256), (2**32, 2**32 + 1)]:
        assert order_u64(a) < order_u64(b)


def test_child_and_meta_keys_are_disjoint():
    parent = b"P"
    child = child_key(parent, b"x")
    meta = meta_key(parent, b"len")
    assert child.startswith(child_scan_prefix(parent))
    assert not meta.startswith(child_scan_prefix(parent))
    assert child != meta
