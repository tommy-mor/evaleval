"""Backend-neutral durable state: schema markers, paths, reified operations.

Paths are the application interface for both selection and transformation:

- **Selectors** (``get``, ``keys``, ``entries``, ``iter``, ``len``, ``contains``,
  …) read only the addressed key or prefix.
- **Transformations** return plain-data operations (``Put``, ``Delete``,
  ``DeletePrefix``, ``Add``, collection ops) that a backend applies atomically.

Cost model (explicit, not hidden):

- **Blind** (no read at construction): ``Leaf.set`` / ``delete``, ``Sum.add`` /
  ``set`` / ``delete``, collection ``clear``. ``Add`` is reified as plain data;
  backends without merge operators may interpret it as read-modify-write under
  their single-writer lock.
- **Read-modify-write**: list/deque push and pop (they touch a length/cursor).
  The backend interprets every operation sequentially against an in-batch view,
  then commits the resulting physical writes atomically.
- **Scan**: ``Map.keys`` / ``iter`` / ``len`` / ``contains`` / ``entries`` /
  ``transform_values``. Use ``keys_page`` / ``entries_page`` / ``iter_page`` for
  bounded selection.

Single-writer semantics are a backend contract: one writer process; serialize
writes at the application layer (the rocksdict backend holds a process-local
lock). Not multi-process safe, not distributed, not SQL.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Generic, Iterator, Protocol, Sequence, TypeVar, runtime_checkable

import cbor2

from evaleval import codec

# ---------------------------------------------------------------------------
# Serialization helpers (CBOR, canonical for map keys)
# ---------------------------------------------------------------------------


def encode_value(value: Any) -> bytes:
    return cbor2.dumps(value, canonical=True)


def decode_value(data: bytes) -> Any:
    return cbor2.loads(data)


# Sum on-disk form: [TAG, 8 LE bytes] — same tags as the Rust durable crate.
_SUM_TAG_F64 = 0
_SUM_TAG_I64 = 1
_SUM_TAG_U64 = 2


def encode_sum(value: int | float) -> bytes:
    if isinstance(value, bool):
        raise TypeError("bool is not a Sum value")
    if isinstance(value, float):
        return bytes([_SUM_TAG_F64]) + struct.pack("<d", value)
    n = int(value)
    if -(1 << 63) <= n <= (1 << 63) - 1:
        return bytes([_SUM_TAG_I64]) + struct.pack("<q", n)
    if 0 <= n <= (1 << 64) - 1:
        return bytes([_SUM_TAG_U64]) + struct.pack("<Q", n)
    raise OverflowError("Sum value does not fit in i64/u64")


def decode_sum(data: bytes) -> int | float:
    if len(data) != 9:
        raise ValueError("malformed Sum accumulator")
    tag = data[0]
    payload = data[1:9]
    if tag == _SUM_TAG_F64:
        return struct.unpack("<d", payload)[0]
    if tag == _SUM_TAG_I64:
        return struct.unpack("<q", payload)[0]
    if tag == _SUM_TAG_U64:
        return struct.unpack("<Q", payload)[0]
    raise ValueError(f"unknown Sum tag {tag}")


def combine_sum(existing: int | float | None, delta: int | float) -> int | float:
    if existing is None:
        return delta
    if isinstance(existing, float) or isinstance(delta, float):
        return float(existing) + float(delta)
    return int(existing) + int(delta)

# ---------------------------------------------------------------------------
# Durability & reified operations
# ---------------------------------------------------------------------------


class Durability(Enum):
    """Durability policy for a committed batch."""

    SYNC_WAL = "sync_wal"
    """Write through the WAL and fsync it before returning (survives power loss)."""

    WAL_ONLY = "wal_only"
    """Write through the WAL without forcing an fsync."""

    DISABLE_WAL = "disable_wal"
    """Skip the WAL. Use only for projections rebuildable from another source."""


@dataclass(frozen=True)
class Put:
    key: bytes
    value: bytes


@dataclass(frozen=True)
class Delete:
    key: bytes


@dataclass(frozen=True)
class DeletePrefix:
    prefix: bytes


@dataclass(frozen=True)
class Add:
    """Reified associative increment.

    Backends with merge operators may apply this blindly; the rocksdict backend
    interprets it as read-modify-write under its single-writer lock.
    """

    key: bytes
    delta: bytes  # encode_sum(delta)


@dataclass(frozen=True)
class ListPush:
    coll_prefix: bytes
    value: bytes


@dataclass(frozen=True)
class ListPop:
    coll_prefix: bytes


@dataclass(frozen=True)
class DequePushBack:
    coll_prefix: bytes
    value: bytes


@dataclass(frozen=True)
class DequePushFront:
    coll_prefix: bytes
    value: bytes


@dataclass(frozen=True)
class DequePopFront:
    coll_prefix: bytes


@dataclass(frozen=True)
class DequePopBack:
    coll_prefix: bytes


Op = (
    Put
    | Delete
    | DeletePrefix
    | Add
    | ListPush
    | ListPop
    | DequePushBack
    | DequePushFront
    | DequePopFront
    | DequePopBack
)


@dataclass(frozen=True)
class Write:
    """A typed, reified mutation — plain data, not a side effect."""

    op: Op

    @staticmethod
    def put(key: bytes, value: bytes) -> Write:
        return Write(Put(key, value))

    @staticmethod
    def delete(key: bytes) -> Write:
        return Write(Delete(key))

    @staticmethod
    def delete_prefix(prefix: bytes) -> Write:
        return Write(DeletePrefix(prefix))

    @staticmethod
    def add(key: bytes, delta: int | float) -> Write:
        return Write(Add(key, encode_sum(delta)))


# ---------------------------------------------------------------------------
# Schema markers (runtime)
# ---------------------------------------------------------------------------


class Schema:
    """Marker base for durable location shapes."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class Leaf(Schema):
    """A single CBOR-encoded scalar value."""


@dataclass(frozen=True, slots=True)
class Map(Schema):
    """Keys (CBOR-encoded) to a sub-schema *of*."""

    of: Schema


@dataclass(frozen=True, slots=True)
class List(Schema):
    """Index-addressed sequence of sub-schema *of*."""

    of: Schema


@dataclass(frozen=True, slots=True)
class Deque(Schema):
    """Double-ended queue of sub-schema *of* (O(1) ends)."""

    of: Schema


@dataclass(frozen=True, slots=True)
class Sum(Schema):
    """Numeric accumulator updated with reified ``Add`` operations."""


class Record(Schema):
    """Fixed named fields; declaration order assigns stable field ids.

    Add new fields at the end; reordering changes the on-disk layout.

    Example::

        Store = Record(
            nodes=Map(Leaf()),
            scores=Map(Sum()),
            log=List(Leaf()),
        )
        root = Store.root()
    """

    __slots__ = ("fields", "_by_name")

    def __init__(self, **kwargs: Schema):
        self.fields: tuple[tuple[str, Schema], ...] = tuple(kwargs.items())
        self._by_name: dict[str, Schema] = dict(kwargs)

    def __repr__(self) -> str:
        inner = ", ".join(f"{n}={s!r}" for n, s in self.fields)
        return f"Record({inner})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Record) and self.fields == other.fields

    def __hash__(self) -> int:
        return hash(self.fields)

    def __getattr__(self, name: str) -> Schema:
        try:
            return self._by_name[name]
        except KeyError as e:
            raise AttributeError(name) from e

    def field_id(self, name: str) -> int:
        for i, (n, _) in enumerate(self.fields):
            if n == name:
                return i
        raise KeyError(name)

    def root(self, namespace: str | None = None) -> "Path":
        if namespace is None:
            return Path.root(self)
        return Path.namespaced(namespace, self)


T = TypeVar("T")


@dataclass(frozen=True)
class Page(Generic[T]):
    """A bounded page plus an opaque cursor for the following page."""

    items: tuple[T, ...]
    cursor: bytes | None

# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Db(Protocol):
    """Backend-neutral database handle."""

    def get_raw(self, key: bytes) -> bytes | None: ...

    def scan_prefix(
        self,
        prefix: bytes,
        *,
        start: bytes | None = None,
        limit: int | None = None,
    ) -> Iterator[tuple[bytes, bytes]]: ...

    def apply(
        self, writes: Sequence[Write], durability: Durability = Durability.SYNC_WAL
    ) -> Any: ...

    def run(self, write: Write, durability: Durability = Durability.SYNC_WAL) -> Any: ...

    def batch(self) -> Batch: ...


@runtime_checkable
class Batch(Protocol):
    def write(self, write: Write) -> None: ...

    def extend(self, writes: Sequence[Write]) -> None: ...

    def commit(self, durability: Durability = Durability.SYNC_WAL) -> Any: ...


# ---------------------------------------------------------------------------
# Path
# ---------------------------------------------------------------------------


class Path:
    """A typed address into a durable schema.

    Cheap to copy; carries only the lowered key prefix and a runtime schema.
    Navigation is pure (no I/O). Selectors take a ``db``; transformations return
    :class:`Write` values.
    """

    __slots__ = ("_prefix", "_schema")

    def __init__(self, prefix: bytes, schema: Schema):
        self._prefix = prefix
        self._schema = schema

    @classmethod
    def root(cls, schema: Schema) -> Path:
        return cls(b"", schema)

    @classmethod
    def namespaced(cls, name: str, schema: Schema) -> Path:
        return cls(codec.put_namespace(name), schema)

    @property
    def prefix(self) -> bytes:
        return self._prefix

    @property
    def schema(self) -> Schema:
        return self._schema

    def __repr__(self) -> str:
        return f"Path(schema={self._schema!r}, prefix={self._prefix!r})"

    def _child(self, seg: bytes, schema: Schema) -> Path:
        return Path(codec.child_key(self._prefix, seg), schema)

    # -- Record field navigation ------------------------------------------

    def __getattr__(self, name: str) -> Path:
        if name.startswith("_"):
            raise AttributeError(name)
        schema = self._schema
        if not isinstance(schema, Record):
            raise AttributeError(
                f"{type(schema).__name__} path has no field {name!r}"
            )
        try:
            field_schema = schema._by_name[name]
            field_id = schema.field_id(name)
        except KeyError as e:
            raise AttributeError(name) from e
        seg = bytearray()
        codec.put_uvarint(seg, field_id)
        return self._child(bytes(seg), field_schema)

    def field(self, name: str) -> Path:
        return self.__getattr__(name)

    # -- Map --------------------------------------------------------------

    def key(self, key: Any) -> Path:
        if not isinstance(self._schema, Map):
            raise TypeError(f"key() requires Map, got {type(self._schema).__name__}")
        return self._child(encode_value(key), self._schema.of)

    def clear(self) -> Write:
        if not isinstance(self._schema, (Map, List, Deque, Record)):
            raise TypeError(
                f"clear() requires Map/List/Deque/Record, got {type(self._schema).__name__}"
            )
        return Write.delete_prefix(self._prefix)

    def keys(self, db: Db) -> list[Any]:
        if not isinstance(self._schema, Map):
            raise TypeError("keys() requires Map")
        scan = codec.child_scan_prefix(self._prefix)
        out: list[Any] = []
        last: bytes | None = None
        for db_key, _ in db.scan_prefix(scan):
            rest = db_key[len(scan) :]
            parsed = codec.read_segment(rest)
            if parsed is None:
                raise ValueError("malformed map entry key")
            key_seg, _ = parsed
            if last == key_seg:
                continue
            last = key_seg
            out.append(decode_value(key_seg))
        return out

    def keys_page(
        self,
        db: Db,
        *,
        cursor: bytes | None = None,
        limit: int = 100,
    ) -> Page[Any]:
        """Return at most ``limit`` logical map keys in encoded-byte order.

        The cursor is opaque and stable for this key encoding. Pass the returned
        cursor to the next call. Nested values are deduplicated by their first
        map-key segment without materializing the full map.
        """
        if not isinstance(self._schema, Map):
            raise TypeError("keys_page() requires Map")
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if limit == 0:
            return Page((), cursor)

        scan = codec.child_scan_prefix(self._prefix)
        start = None
        if cursor is not None:
            previous_child = codec.child_key(self._prefix, cursor)
            start = codec.prefix_upper_bound(previous_child)
            if start is None:
                return Page((), None)

        encoded: list[bytes] = []
        last: bytes | None = None
        for db_key, _ in db.scan_prefix(scan, start=start):
            rest = db_key[len(scan) :]
            parsed = codec.read_segment(rest)
            if parsed is None:
                raise ValueError("malformed map entry key")
            key_seg, _ = parsed
            if last == key_seg:
                continue
            last = key_seg
            encoded.append(key_seg)
            if len(encoded) > limit:
                break

        has_more = len(encoded) > limit
        page_keys = encoded[:limit]
        next_cursor = page_keys[-1] if has_more and page_keys else None
        return Page(tuple(decode_value(key) for key in page_keys), next_cursor)

    def len(self, db: Db) -> int:
        schema = self._schema
        if isinstance(schema, Map):
            return len(self.keys(db))
        if isinstance(schema, List):
            raw = db.get_raw(codec.meta_key(self._prefix, b"len"))
            return int.from_bytes(raw, "little") if raw is not None else 0
        if isinstance(schema, Deque):
            return max(0, self._deque_tail(db) - self._deque_head(db))
        raise TypeError(f"len() not supported for {type(schema).__name__}")

    def is_empty(self, db: Db) -> bool:
        return self.len(db) == 0

    def contains(self, db: Db, key: Any) -> bool:
        if not isinstance(self._schema, Map):
            raise TypeError("contains() requires Map")
        child = self.key(key)
        for _ in db.scan_prefix(child.prefix):
            return True
        return False

    def entries(self, db: Db) -> list[tuple[Any, Path]]:
        if not isinstance(self._schema, Map):
            raise TypeError("entries() requires Map")
        return [(k, self.key(k)) for k in self.keys(db)]

    def entries_page(
        self,
        db: Db,
        *,
        cursor: bytes | None = None,
        limit: int = 100,
    ) -> Page[tuple[Any, Path]]:
        """Paginate logical map keys paired with composable value paths."""
        page = self.keys_page(db, cursor=cursor, limit=limit)
        return Page(tuple((key, self.key(key)) for key in page.items), page.cursor)

    # -- Leaf -------------------------------------------------------------

    def get(self, db: Db, key: Any | None = None) -> Any:
        schema = self._schema
        if isinstance(schema, Leaf):
            if key is not None:
                raise TypeError("Leaf.get() takes no key")
            raw = db.get_raw(self._prefix)
            return None if raw is None else decode_value(raw)
        if isinstance(schema, Sum):
            if key is not None:
                raise TypeError("Sum.get() takes no key")
            raw = db.get_raw(self._prefix)
            return 0 if raw is None else decode_sum(raw)
        if isinstance(schema, Map) and key is not None:
            return self.key(key).get(db)
        if isinstance(schema, List) and key is not None:
            index = int(key)
            if index < 0 or index >= self.len(db):
                return None
            return self.at(index).get(db)
        raise TypeError(
            f"get() not supported for {type(schema).__name__}"
            + (" without key" if key is None and isinstance(schema, (Map, List)) else "")
        )

    def set(self, value: Any) -> Write:
        schema = self._schema
        if isinstance(schema, Leaf):
            return Write.put(self._prefix, encode_value(value))
        if isinstance(schema, Sum):
            return Write.put(self._prefix, encode_sum(value))
        raise TypeError(f"set() requires Leaf or Sum, got {type(schema).__name__}")

    def delete(self) -> Write:
        if not isinstance(self._schema, (Leaf, Sum)):
            raise TypeError(
                f"delete() requires Leaf or Sum, got {type(self._schema).__name__}"
            )
        return Write.delete(self._prefix)

    # -- Sum --------------------------------------------------------------

    def add(self, delta: int | float) -> Write:
        if not isinstance(self._schema, Sum):
            raise TypeError(f"add() requires Sum, got {type(self._schema).__name__}")
        return Write.add(self._prefix, delta)

    # -- Map leaf/sum iteration & transform -------------------------------

    def iter(self, db: Db) -> list[Any]:
        schema = self._schema
        if isinstance(schema, Map):
            if isinstance(schema.of, Leaf):
                return self._iter_leaf_map(db)
            if isinstance(schema.of, Sum):
                return self._iter_sum_map(db)
            raise TypeError("iter() on Map requires Leaf or Sum values")
        if isinstance(schema, List) and isinstance(schema.of, Leaf):
            n = self.len(db)
            out = []
            for i in range(n):
                v = self.at(i).get(db)
                if v is None:
                    raise ValueError("list element missing below len")
                out.append(v)
            return out
        if isinstance(schema, Deque) and isinstance(schema.of, Leaf):
            head = self._deque_head(db)
            tail = self._deque_tail(db)
            out = []
            for idx in range(head, tail):
                v = self._child(codec.order_i64(idx), schema.of).get(db)
                if v is None:
                    raise ValueError("deque element missing in range")
                out.append(v)
            return out
        raise TypeError(f"iter() not supported for {schema!r}")

    def iter_page(
        self,
        db: Db,
        *,
        cursor: bytes | None = None,
        limit: int = 100,
    ) -> Page[tuple[Any, Any]]:
        """Paginate values of a ``Map(Leaf)`` or ``Map(Sum)``.

        This performs one bounded key-prefix scan followed by at most ``limit``
        point reads. It never materializes the entire map.
        """
        schema = self._schema
        if not isinstance(schema, Map) or not isinstance(schema.of, (Leaf, Sum)):
            raise TypeError("iter_page() requires Map(Leaf) or Map(Sum)")
        page = self.keys_page(db, cursor=cursor, limit=limit)
        return Page(
            tuple((key, self.key(key).get(db)) for key in page.items),
            page.cursor,
        )

    def _iter_leaf_map(self, db: Db) -> list[tuple[Any, Any]]:
        assert isinstance(self._schema, Map)
        scan = codec.child_scan_prefix(self._prefix)
        out: list[tuple[Any, Any]] = []
        for db_key, value in db.scan_prefix(scan):
            rest = db_key[len(scan) :]
            parsed = codec.read_segment(rest)
            if parsed is None:
                raise ValueError("malformed map entry key")
            key_seg, used = parsed
            if used != len(rest):
                raise ValueError("unexpected nested key in leaf map")
            out.append((decode_value(key_seg), decode_value(value)))
        return out

    def _iter_sum_map(self, db: Db) -> list[tuple[Any, int | float]]:
        assert isinstance(self._schema, Map)
        scan = codec.child_scan_prefix(self._prefix)
        out: list[tuple[Any, int | float]] = []
        for db_key, value in db.scan_prefix(scan):
            rest = db_key[len(scan) :]
            parsed = codec.read_segment(rest)
            if parsed is None:
                raise ValueError("malformed map entry key")
            key_seg, used = parsed
            if used != len(rest):
                raise ValueError("unexpected nested key in sum map")
            out.append((decode_value(key_seg), decode_sum(value)))
        return out

    def transform_values(
        self, db: Db, fn: Callable[[Any, Any], Any | None]
    ) -> list[Write]:
        """One-scan bulk rewrite yielding reified writes.

        ``fn(key, value) -> new_value | None``; ``None`` deletes the entry.
        """
        if not isinstance(self._schema, Map):
            raise TypeError("transform_values() requires Map")
        if not isinstance(self._schema.of, (Leaf, Sum)):
            raise TypeError("transform_values() requires Leaf or Sum values")
        writes: list[Write] = []
        for k, v in self.iter(db):
            entry = self.key(k)
            new = fn(k, v)
            if new is None:
                writes.append(entry.delete())
            else:
                writes.append(entry.set(new))
        return writes

    # -- List -------------------------------------------------------------

    def at(self, index: int) -> Path:
        if not isinstance(self._schema, List):
            raise TypeError("at() requires List")
        return self._child(codec.order_u64(index), self._schema.of)

    def push(self, value: Any) -> Write:
        if not isinstance(self._schema, List) or not isinstance(self._schema.of, Leaf):
            raise TypeError("push() requires List(Leaf)")
        return Write(ListPush(self._prefix, encode_value(value)))

    def pop_op(self) -> Write:
        if not isinstance(self._schema, List):
            raise TypeError("pop_op() requires List")
        return Write(ListPop(self._prefix))

    def push_commit(self, db: Db, value: Any) -> int:
        """Append and commit with SyncWal; returns the new element's index."""
        index = self.len(db)
        db.run(self.push(value), Durability.SYNC_WAL)
        return index

    def pop(self, db: Db) -> Any:
        """Remove and return the last element (SyncWal)."""
        if not isinstance(self._schema, List) or not isinstance(self._schema.of, Leaf):
            raise TypeError("pop() requires List(Leaf)")
        n = self.len(db)
        if n == 0:
            return None
        value = self.at(n - 1).get(db)
        db.apply(
            [
                self.at(n - 1).delete(),
                Write.put(
                    codec.meta_key(self._prefix, b"len"),
                    (n - 1).to_bytes(8, "little"),
                ),
            ],
            Durability.SYNC_WAL,
        )
        return value

    # -- Deque ------------------------------------------------------------

    def _deque_head(self, db: Db) -> int:
        raw = db.get_raw(codec.meta_key(self._prefix, b"head"))
        return int.from_bytes(raw, "little", signed=True) if raw is not None else 0

    def _deque_tail(self, db: Db) -> int:
        raw = db.get_raw(codec.meta_key(self._prefix, b"tail"))
        return int.from_bytes(raw, "little", signed=True) if raw is not None else 0

    def push_back(self, value: Any) -> Write:
        if not isinstance(self._schema, Deque) or not isinstance(self._schema.of, Leaf):
            raise TypeError("push_back() requires Deque(Leaf)")
        return Write(DequePushBack(self._prefix, encode_value(value)))

    def push_front(self, value: Any) -> Write:
        if not isinstance(self._schema, Deque) or not isinstance(self._schema.of, Leaf):
            raise TypeError("push_front() requires Deque(Leaf)")
        return Write(DequePushFront(self._prefix, encode_value(value)))

    def pop_front_op(self) -> Write:
        if not isinstance(self._schema, Deque):
            raise TypeError("pop_front_op() requires Deque")
        return Write(DequePopFront(self._prefix))

    def pop_back_op(self) -> Write:
        if not isinstance(self._schema, Deque):
            raise TypeError("pop_back_op() requires Deque")
        return Write(DequePopBack(self._prefix))

    def push_back_commit(self, db: Db, value: Any) -> None:
        db.run(self.push_back(value), Durability.SYNC_WAL)

    def push_front_commit(self, db: Db, value: Any) -> None:
        db.run(self.push_front(value), Durability.SYNC_WAL)

    def pop_front(self, db: Db) -> Any:
        if not isinstance(self._schema, Deque) or not isinstance(self._schema.of, Leaf):
            raise TypeError("pop_front() requires Deque(Leaf)")
        head = self._deque_head(db)
        tail = self._deque_tail(db)
        if head >= tail:
            return None
        value = self._child(codec.order_i64(head), self._schema.of).get(db)
        db.apply(
            [
                self._child(codec.order_i64(head), Leaf()).delete(),
                Write.put(
                    codec.meta_key(self._prefix, b"head"),
                    (head + 1).to_bytes(8, "little", signed=True),
                ),
            ],
            Durability.SYNC_WAL,
        )
        return value

    def pop_back(self, db: Db) -> Any:
        if not isinstance(self._schema, Deque) or not isinstance(self._schema.of, Leaf):
            raise TypeError("pop_back() requires Deque(Leaf)")
        head = self._deque_head(db)
        tail = self._deque_tail(db)
        if head >= tail:
            return None
        last = tail - 1
        value = self._child(codec.order_i64(last), self._schema.of).get(db)
        db.apply(
            [
                self._child(codec.order_i64(last), Leaf()).delete(),
                Write.put(
                    codec.meta_key(self._prefix, b"tail"),
                    last.to_bytes(8, "little", signed=True),
                ),
            ],
            Durability.SYNC_WAL,
        )
        return value

    def front(self, db: Db) -> Any:
        if not isinstance(self._schema, Deque) or not isinstance(self._schema.of, Leaf):
            raise TypeError("front() requires Deque(Leaf)")
        head = self._deque_head(db)
        if head >= self._deque_tail(db):
            return None
        return self._child(codec.order_i64(head), self._schema.of).get(db)

    def back(self, db: Db) -> Any:
        if not isinstance(self._schema, Deque) or not isinstance(self._schema.of, Leaf):
            raise TypeError("back() requires Deque(Leaf)")
        tail = self._deque_tail(db)
        if self._deque_head(db) >= tail:
            return None
        return self._child(codec.order_i64(tail - 1), self._schema.of).get(db)

    def truncate_back(self, db: Db, max_len: int, durability: Durability = Durability.SYNC_WAL) -> None:
        if not isinstance(self._schema, Deque) or not isinstance(self._schema.of, Leaf):
            raise TypeError("truncate_back() requires Deque(Leaf)")
        head = self._deque_head(db)
        tail = self._deque_tail(db)
        length = max(0, tail - head)
        if length <= max_len:
            return
        new_tail = tail - (length - max_len)
        writes: list[Write] = []
        for idx in range(new_tail, tail):
            writes.append(self._child(codec.order_i64(idx), Leaf()).delete())
        writes.append(
            Write.put(
                codec.meta_key(self._prefix, b"tail"),
                new_tail.to_bytes(8, "little", signed=True),
            )
        )
        db.apply(writes, durability)
