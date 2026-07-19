"""Length-prefixed key encoding for durable paths.

Mirrors the Rust `durable` crate layout so sibling subtrees never overlap and a
parent prefix only ever prefixes its own descendants.

A segment is ``uvarint(len) ++ bytes``. Within a location prefix ``P``:

- ``P`` (exact)            → a Leaf / Sum scalar value
- ``P ++ [DATA] ++ seg``   → child data (map entry, record field, element)
- ``P ++ [META] ++ seg``   → collection metadata (list len, deque head/tail)
"""

from __future__ import annotations

DATA: int = 0x01
META: int = 0x00


def put_uvarint(out: bytearray, value: int) -> None:
    """Append an unsigned LEB128 varint to *out*."""
    if value < 0:
        raise ValueError("uvarint requires a non-negative integer")
    while True:
        byte = value & 0x7F
        value >>= 7
        if value != 0:
            byte |= 0x80
        out.append(byte)
        if value == 0:
            break


def read_uvarint(data: bytes) -> tuple[int, int] | None:
    """Decode a uvarint from the front of *data*.

    Returns ``(value, bytes_consumed)`` or ``None`` if truncated/overlong.
    """
    result = 0
    shift = 0
    for i, byte in enumerate(data):
        if shift >= 64:
            return None
        result |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            return result, i + 1
        shift += 7
    return None


def put_segment(out: bytearray, data: bytes) -> None:
    put_uvarint(out, len(data))
    out.extend(data)


def read_segment(data: bytes) -> tuple[bytes, int] | None:
    """Return ``(segment_payload, total_bytes_consumed)`` or ``None``."""
    parsed = read_uvarint(data)
    if parsed is None:
        return None
    length, header = parsed
    end = header + length
    if end > len(data):
        return None
    return data[header:end], end


def child_key(parent: bytes, seg: bytes) -> bytes:
    key = bytearray(parent)
    key.append(DATA)
    put_segment(key, seg)
    return bytes(key)


def child_scan_prefix(parent: bytes) -> bytes:
    return parent + bytes([DATA])


def meta_key(parent: bytes, name: bytes) -> bytes:
    key = bytearray(parent)
    key.append(META)
    put_segment(key, name)
    return bytes(key)


def prefix_upper_bound(prefix: bytes) -> bytes | None:
    """Smallest key strictly greater than every key prefixed by *prefix*.

    ``None`` when the range extends to the end of the keyspace (empty or all
    ``0xff``), in which case callers must fall back to a scan.
    """
    end = bytearray(prefix)
    while end:
        if end[-1] != 0xFF:
            end[-1] += 1
            return bytes(end)
        end.pop()
    return None


def order_u64(index: int) -> bytes:
    """Order-preserving big-endian encoding of a non-negative index (List)."""
    if index < 0:
        raise ValueError("list index must be non-negative")
    return int(index).to_bytes(8, "big", signed=False)


def order_i64(index: int) -> bytes:
    """Order-preserving encoding of a signed index (Deque).

    Flipping the sign bit makes unsigned big-endian byte order match signed
    numeric order, so negative front indices sort before positive ones.
    """
    return ((int(index) & ((1 << 64) - 1)) ^ (1 << 63)).to_bytes(8, "big", signed=False)


def put_namespace(name: str) -> bytes:
    """Encode a root namespace segment (no DATA discriminator — top-level)."""
    out = bytearray()
    put_segment(out, name.encode("utf-8"))
    return bytes(out)
