"""Raw-byte rocksdict backend for the durable state layer.

Opens RocksDB in ``raw_mode`` so keys and values are opaque bytes. Values are
CBOR (or tagged Sum payloads); keys use the length-prefixed path codec.

``Add`` is interpreted as read-modify-write under the same single-writer lock
and atomic batch — rocksdict does not expose custom merge operators. Callers
still build reified :class:`~evaleval.state.Add` ops so a native-merge backend
can be swapped in later without API changes.

Not multi-process safe. One writer; the process-local lock serializes writers
inside this process.
"""

from __future__ import annotations

import threading
from pathlib import Path as FsPath
from typing import Any, Iterator, Sequence

from rocksdict import Options, Rdict, WriteBatch, WriteOptions

from evaleval import codec
from evaleval.state import (
    Add,
    Delete,
    DeletePrefix,
    DequePopBack,
    DequePopFront,
    DequePushBack,
    DequePushFront,
    Durability,
    ListPop,
    ListPush,
    Put,
    Write,
    combine_sum,
    decode_sum,
    encode_sum,
)


class RocksDb:
    """RocksDB handle implementing the backend-neutral durable Db contract."""

    def __init__(self, path: str | FsPath, *, _rdict: Rdict | None = None):
        self.path = FsPath(path)
        if _rdict is None:
            opts = Options(raw_mode=True)
            opts.create_if_missing(True)
            self._db = Rdict(str(self.path), opts)
        else:
            self._db = _rdict
        self._lock = threading.RLock()

    @classmethod
    def open(cls, path: str | FsPath) -> RocksDb:
        return cls(path)

    def close(self) -> None:
        self._db.close()

    def destroy(self) -> None:
        """Close and delete the on-disk database."""
        self.close()
        Rdict.destroy(str(self.path))

    def __enter__(self) -> RocksDb:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- raw reads --------------------------------------------------------

    def get_raw(self, key: bytes) -> bytes | None:
        try:
            val = self._db.get(key)
        except KeyError:
            return None
        if val is None:
            return None
        return bytes(val)

    def scan_prefix(
        self,
        prefix: bytes,
        *,
        start: bytes | None = None,
        limit: int | None = None,
    ) -> Iterator[tuple[bytes, bytes]]:
        """Scan a prefix in byte order, optionally from an inclusive raw key.

        ``limit`` bounds physical key/value pairs yielded. ``start`` is a raw
        RocksDB key and must be at or below the requested prefix range.
        """
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        if limit == 0:
            return
        yielded = 0
        from_key = prefix if start is None or start < prefix else start
        for k, v in self._db.items(from_key=from_key):
            kb = bytes(k)
            if not kb.startswith(prefix):
                break
            yield kb, bytes(v)
            yielded += 1
            if limit is not None and yielded >= limit:
                break

    def snapshot(self) -> Any:
        """Point-in-time snapshot of the default column family."""
        return self._db.snapshot()

    # -- writes -----------------------------------------------------------

    def apply(
        self,
        writes: Sequence[Write],
        durability: Durability = Durability.SYNC_WAL,
    ) -> None:
        batch = self.batch()
        batch.extend(writes)
        batch.commit(durability)

    def run(self, write: Write, durability: Durability = Durability.SYNC_WAL) -> Any:
        return self.apply([write], durability)

    def batch(self) -> RocksBatch:
        return RocksBatch(self)


class RocksBatch:
    """Sequentially interpreted operations committed as one atomic batch."""

    def __init__(self, db: RocksDb):
        self._db = db
        self._ops: list[Write] = []

    def write(self, write: Write) -> None:
        self._ops.append(write)

    def extend(self, writes: Sequence[Write]) -> None:
        self._ops.extend(writes)

    def commit(self, durability: Durability = Durability.SYNC_WAL) -> list[Any]:
        """Commit atomically. Returns results for pop ops (values or None)."""
        with self._db._lock:
            return self._commit_locked(durability)

    def _commit_locked(self, durability: Durability) -> list[Any]:
        wb = WriteBatch(raw_mode=True)
        results: list[Any] = []
        # Exact staged values plus ordered range tombstones. Exact overlay values
        # always win because DeletePrefix marks all older staged values None and
        # a later Put/Add writes a newer exact value.
        overlay: dict[bytes, bytes | None] = {}
        deleted_prefixes: list[bytes] = []

        def staged_get(key: bytes) -> bytes | None:
            if key in overlay:
                return overlay[key]
            if any(key.startswith(prefix) for prefix in deleted_prefixes):
                return None
            return self._db.get_raw(key)

        def put(key: bytes, value: bytes) -> None:
            wb.put(key, value)
            overlay[key] = value

        def delete(key: bytes) -> None:
            wb.delete(key)
            overlay[key] = None

        for write in self._ops:
            op = write.op
            if isinstance(op, Put):
                put(op.key, op.value)
            elif isinstance(op, Delete):
                delete(op.key)
            elif isinstance(op, DeletePrefix):
                self._delete_prefix(wb, overlay, deleted_prefixes, op.prefix)
            elif isinstance(op, Add):
                delta = decode_sum(op.delta)
                existing_raw = staged_get(op.key)
                existing = decode_sum(existing_raw) if existing_raw is not None else None
                put(op.key, encode_sum(combine_sum(existing, delta)))
            elif isinstance(op, ListPush):
                self._resolve_list_push(wb, overlay, deleted_prefixes, op)
            elif isinstance(op, ListPop):
                results.append(
                    self._resolve_list_pop(
                        wb, overlay, deleted_prefixes, op.coll_prefix
                    )
                )
            elif isinstance(op, DequePushBack):
                self._resolve_deque_push(
                    wb, overlay, deleted_prefixes, op.coll_prefix, op.value, back=True
                )
            elif isinstance(op, DequePushFront):
                self._resolve_deque_push(
                    wb, overlay, deleted_prefixes, op.coll_prefix, op.value, back=False
                )
            elif isinstance(op, DequePopFront):
                results.append(
                    self._resolve_deque_pop_front(
                        wb, overlay, deleted_prefixes, op.coll_prefix
                    )
                )
            elif isinstance(op, DequePopBack):
                results.append(
                    self._resolve_deque_pop_back(
                        wb, overlay, deleted_prefixes, op.coll_prefix
                    )
                )
            else:
                raise TypeError(f"unknown op {type(op)!r}")

        wo = WriteOptions()
        if durability is Durability.DISABLE_WAL:
            wo.disable_wal = True
            wo.sync = False
            self._db._db.write(wb, wo)
        elif durability is Durability.WAL_ONLY:
            wo.disable_wal = False
            wo.sync = False
            self._db._db.write(wb, wo)
        elif durability is Durability.SYNC_WAL:
            wo.disable_wal = False
            wo.sync = False
            self._db._db.write(wb, wo)
            self._db._db.flush_wal(True)
        else:
            raise ValueError(f"unknown durability {durability!r}")

        return results

    @staticmethod
    def _overlay_get(
        db: RocksDb,
        overlay: dict[bytes, bytes | None],
        deleted_prefixes: list[bytes],
        key: bytes,
    ) -> bytes | None:
        if key in overlay:
            return overlay[key]
        if any(key.startswith(prefix) for prefix in deleted_prefixes):
            return None
        return db.get_raw(key)

    def _delete_prefix(
        self,
        wb: WriteBatch,
        overlay: dict[bytes, bytes | None],
        deleted_prefixes: list[bytes],
        prefix: bytes,
    ) -> None:
        end = codec.prefix_upper_bound(prefix)
        if end is not None:
            wb.delete_range(prefix, end)
        else:
            for key, _ in self._db.scan_prefix(prefix):
                wb.delete(key)
                overlay[key] = None
            # No range upper bound (notably the empty root prefix): persisted
            # scans cannot see earlier staged puts, so delete those explicitly.
            for key in list(overlay):
                if key.startswith(prefix):
                    wb.delete(key)
        deleted_prefixes.append(prefix)
        for key in list(overlay):
            if key.startswith(prefix):
                overlay[key] = None

    def _resolve_list_push(
        self,
        wb: WriteBatch,
        overlay: dict[bytes, bytes | None],
        deleted_prefixes: list[bytes],
        op: ListPush,
    ) -> None:
        len_key = codec.meta_key(op.coll_prefix, b"len")
        raw = self._overlay_get(self._db, overlay, deleted_prefixes, len_key)
        length = int.from_bytes(raw, "little") if raw is not None else 0
        elem = codec.child_key(op.coll_prefix, codec.order_u64(length))
        wb.put(elem, op.value)
        overlay[elem] = op.value
        length += 1
        encoded_len = length.to_bytes(8, "little")
        wb.put(len_key, encoded_len)
        overlay[len_key] = encoded_len

    def _resolve_list_pop(
        self,
        wb: WriteBatch,
        overlay: dict[bytes, bytes | None],
        deleted_prefixes: list[bytes],
        coll_prefix: bytes,
    ) -> Any:
        from evaleval.state import decode_value

        len_key = codec.meta_key(coll_prefix, b"len")
        raw = self._overlay_get(self._db, overlay, deleted_prefixes, len_key)
        length = int.from_bytes(raw, "little") if raw is not None else 0
        if length == 0:
            return None
        last = length - 1
        elem = codec.child_key(coll_prefix, codec.order_u64(last))
        val_raw = self._overlay_get(self._db, overlay, deleted_prefixes, elem)
        wb.delete(elem)
        overlay[elem] = None
        encoded_len = last.to_bytes(8, "little")
        wb.put(len_key, encoded_len)
        overlay[len_key] = encoded_len
        return None if val_raw is None else decode_value(val_raw)

    def _read_i64_meta(
        self,
        overlay: dict[bytes, bytes | None],
        deleted_prefixes: list[bytes],
        key: bytes,
    ) -> int:
        raw = self._overlay_get(self._db, overlay, deleted_prefixes, key)
        if raw is None:
            return 0
        return int.from_bytes(raw, "little", signed=True)

    def _resolve_deque_push(
        self,
        wb: WriteBatch,
        overlay: dict[bytes, bytes | None],
        deleted_prefixes: list[bytes],
        coll_prefix: bytes,
        value: bytes,
        *,
        back: bool,
    ) -> None:
        head_key = codec.meta_key(coll_prefix, b"head")
        tail_key = codec.meta_key(coll_prefix, b"tail")
        head = self._read_i64_meta(overlay, deleted_prefixes, head_key)
        tail = self._read_i64_meta(overlay, deleted_prefixes, tail_key)
        if back:
            elem = codec.child_key(coll_prefix, codec.order_i64(tail))
            wb.put(elem, value)
            overlay[elem] = value
            tail += 1
        else:
            head -= 1
            elem = codec.child_key(coll_prefix, codec.order_i64(head))
            wb.put(elem, value)
            overlay[elem] = value
        head_b = head.to_bytes(8, "little", signed=True)
        tail_b = tail.to_bytes(8, "little", signed=True)
        wb.put(head_key, head_b)
        wb.put(tail_key, tail_b)
        overlay[head_key] = head_b
        overlay[tail_key] = tail_b

    def _resolve_deque_pop_front(
        self,
        wb: WriteBatch,
        overlay: dict[bytes, bytes | None],
        deleted_prefixes: list[bytes],
        coll_prefix: bytes,
    ) -> Any:
        from evaleval.state import decode_value

        head_key = codec.meta_key(coll_prefix, b"head")
        tail_key = codec.meta_key(coll_prefix, b"tail")
        head = self._read_i64_meta(overlay, deleted_prefixes, head_key)
        tail = self._read_i64_meta(overlay, deleted_prefixes, tail_key)
        if head >= tail:
            return None
        elem = codec.child_key(coll_prefix, codec.order_i64(head))
        val_raw = self._overlay_get(self._db, overlay, deleted_prefixes, elem)
        wb.delete(elem)
        overlay[elem] = None
        head_b = (head + 1).to_bytes(8, "little", signed=True)
        wb.put(head_key, head_b)
        overlay[head_key] = head_b
        return None if val_raw is None else decode_value(val_raw)

    def _resolve_deque_pop_back(
        self,
        wb: WriteBatch,
        overlay: dict[bytes, bytes | None],
        deleted_prefixes: list[bytes],
        coll_prefix: bytes,
    ) -> Any:
        from evaleval.state import decode_value

        head_key = codec.meta_key(coll_prefix, b"head")
        tail_key = codec.meta_key(coll_prefix, b"tail")
        head = self._read_i64_meta(overlay, deleted_prefixes, head_key)
        tail = self._read_i64_meta(overlay, deleted_prefixes, tail_key)
        if head >= tail:
            return None
        last = tail - 1
        elem = codec.child_key(coll_prefix, codec.order_i64(last))
        val_raw = self._overlay_get(self._db, overlay, deleted_prefixes, elem)
        wb.delete(elem)
        overlay[elem] = None
        tail_b = last.to_bytes(8, "little", signed=True)
        wb.put(tail_key, tail_b)
        overlay[tail_key] = tail_b
        return None if val_raw is None else decode_value(val_raw)
