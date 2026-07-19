"""Integration and property-style tests for durable path state over rocksdict."""

from __future__ import annotations

import threading
from collections import deque as PyDeque

import pytest
from hypothesis import given, settings, strategies as st

from evaleval import (
    Add,
    DeletePrefix,
    Deque,
    Durability,
    Leaf,
    List,
    Map,
    Put,
    Record,
    RocksDb,
    Sum,
)


def Store():
    return Record(
        scopes=Map(
            Record(
                edges=Map(Sum()),
                voted_pairs=Map(Leaf()),
                recent_votes=Deque(Leaf()),
                item_count=Sum(),
            )
        ),
        nodes=Map(Leaf()),
        log=List(Leaf()),
    )


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.rocks"
    handle = RocksDb.open(path)
    yield handle
    handle.close()


def test_leaf_set_get_delete(db):
    root = Store().root()
    k = "reddit.com/r/rust"
    assert root.nodes.key(k).get(db) is None
    db.run(root.nodes.key(k).set("Rust"), Durability.SYNC_WAL)
    assert root.nodes.key(k).get(db) == "Rust"
    db.run(root.nodes.key(k).delete(), Durability.SYNC_WAL)
    assert root.nodes.key(k).get(db) is None


def test_sum_accumulates_with_reified_adds(db):
    edges = Store().root().scopes.key("s").edges
    e = (3, 7)
    db.apply(
        [
            edges.key(e).add(2.0),
            edges.key(e).add(1.0),
            edges.key(e).add(0.5),
        ],
        Durability.SYNC_WAL,
    )
    assert edges.key(e).get(db) == 3.5
    db.run(edges.key(e).add(-1.5), Durability.SYNC_WAL)
    assert edges.key(e).get(db) == 2.0
    assert edges.key((9, 9)).get(db) == 0.0


def test_sum_set_then_add(db):
    count = Store().root().scopes.key("s").item_count
    db.run(count.set(10), Durability.SYNC_WAL)
    db.run(count.add(5), Durability.SYNC_WAL)
    assert count.get(db) == 15


def test_reified_writes_are_inspectable_data():
    edges = Store().root().scopes.key("s").edges
    merge = edges.key((1, 2)).add(1.0)
    assert isinstance(merge.op, Add)

    put = Store().root().nodes.key("x").set("y")
    assert isinstance(put.op, Put)

    clear = Store().root().scopes.clear()
    assert isinstance(clear.op, DeletePrefix)


def test_point_update_touches_only_its_own_key(db):
    root = Store().root()
    rust = root.scopes.key("rust")
    python = root.scopes.key("python")

    batch = db.batch()
    for j in range(5):
        batch.write(rust.edges.key((0, j)).add(float(j + 1)))
        batch.write(python.edges.key((0, j)).add(100.0))
    batch.write(rust.recent_votes.push_back({"a": "a", "b": "b", "ratio": 2}))
    batch.commit()

    db.run(rust.edges.key((0, 2)).add(10.0), Durability.SYNC_WAL)

    assert rust.edges.key((0, 2)).get(db) == 13.0
    assert rust.edges.key((0, 0)).get(db) == 1.0
    assert rust.edges.key((0, 4)).get(db) == 5.0
    for j in range(5):
        assert python.edges.key((0, j)).get(db) == 100.0
    assert rust.recent_votes.len(db) == 1


def test_map_keys_len_contains_entries(db):
    nodes = Store().root().nodes
    db.apply(
        [
            nodes.key("a").set("1"),
            nodes.key("b").set("2"),
            nodes.key("c").set("3"),
        ],
        Durability.SYNC_WAL,
    )
    assert sorted(nodes.keys(db)) == ["a", "b", "c"]
    assert nodes.len(db) == 3
    assert nodes.contains(db, "b")
    assert not nodes.contains(db, "z")
    assert sorted(nodes.iter(db)) == [("a", "1"), ("b", "2"), ("c", "3")]
    entries = nodes.entries(db)
    assert sorted(k for k, _ in entries) == ["a", "b", "c"]
    assert entries[0][1].get(db) in {"1", "2", "3"}


def test_map_keys_dedup_across_nested_subkeys(db):
    scopes = Store().root().scopes
    rust = scopes.key("rust")
    batch = db.batch()
    batch.write(rust.edges.key((0, 1)).add(1.0))
    batch.write(rust.edges.key((0, 2)).add(1.0))
    batch.write(rust.item_count.add(3))
    batch.write(rust.recent_votes.push_back({"a": "a", "b": "b", "ratio": 1}))
    batch.write(scopes.key("python").item_count.add(1))
    batch.commit()

    assert sorted(scopes.keys(db)) == ["python", "rust"]
    assert scopes.len(db) == 2


def test_map_clear_deletes_subtree_only(db):
    rust = Store().root().scopes.key("rust")
    batch = db.batch()
    batch.write(rust.edges.key((0, 1)).add(1.0))
    batch.write(rust.edges.key((0, 2)).add(2.0))
    batch.write(rust.item_count.add(5))
    batch.commit()

    db.run(rust.edges.clear(), Durability.SYNC_WAL)
    assert rust.edges.len(db) == 0
    assert rust.edges.key((0, 1)).get(db) == 0.0
    assert rust.item_count.get(db) == 5


def test_transform_values_decays_edges_in_one_batch(db):
    edges = Store().root().scopes.key("rust").edges
    batch = db.batch()
    for j in range(1, 5):
        batch.write(edges.key((0, j)).add(float(j * 10)))
    batch.commit()

    writes = edges.transform_values(
        db,
        lambda _k, w: None if (decayed := w * 0.5) <= 5.0 else decayed,
    )
    db.apply(writes, Durability.SYNC_WAL)

    assert edges.key((0, 1)).get(db) == 0.0
    assert edges.key((0, 2)).get(db) == 10.0
    assert edges.key((0, 3)).get(db) == 15.0
    assert edges.key((0, 4)).get(db) == 20.0
    assert edges.len(db) == 3


def test_list_push_pop_iter(db):
    log = Store().root().log
    assert log.is_empty(db)
    assert log.push_commit(db, 10) == 0
    assert log.push_commit(db, 20) == 1
    assert log.push_commit(db, 30) == 2
    assert log.len(db) == 3
    assert log.get(db, 1) == 20
    assert log.get(db, 3) is None
    assert log.iter(db) == [10, 20, 30]
    assert log.pop(db) == 30
    assert log.len(db) == 2
    assert log.iter(db) == [10, 20]


def test_batched_list_pushes_get_contiguous_indices(db):
    log = Store().root().log
    batch = db.batch()
    batch.write(log.push(1))
    batch.write(log.push(2))
    batch.write(log.push(3))
    batch.commit()
    assert log.iter(db) == [1, 2, 3]

    batch = db.batch()
    batch.write(log.push(4))
    batch.write(log.push(5))
    batch.commit()
    assert log.iter(db) == [1, 2, 3, 4, 5]
    assert log.len(db) == 5


def test_batch_list_push_pop_interleaving_is_sequential(db):
    log = Store().root().log
    batch = db.batch()
    batch.write(log.push(1))
    batch.write(log.push(2))
    batch.write(log.pop_op())
    batch.write(log.push(3))

    assert batch.commit() == [2]
    assert log.iter(db) == [1, 3]

    batch = db.batch()
    batch.write(log.pop_op())
    batch.write(log.push(4))
    assert batch.commit() == [3]
    assert log.iter(db) == [1, 4]


def test_deque_behaves_like_double_ended_queue(db):
    dq = Store().root().scopes.key("s").recent_votes
    v = lambda n: {"a": f"a{n}", "b": f"b{n}", "ratio": n}

    dq.push_back_commit(db, v(1))
    dq.push_back_commit(db, v(2))
    dq.push_front_commit(db, v(0))

    assert dq.len(db) == 3
    assert dq.iter(db) == [v(0), v(1), v(2)]
    assert dq.front(db) == v(0)
    assert dq.back(db) == v(2)
    assert dq.pop_front(db) == v(0)
    assert dq.pop_back(db) == v(2)
    assert dq.iter(db) == [v(1)]
    assert dq.pop_front(db) == v(1)
    assert dq.pop_front(db) is None
    assert dq.is_empty(db)


def test_batch_deque_push_pop_interleaving_is_sequential(db):
    dq = Store().root().scopes.key("s").recent_votes
    batch = db.batch()
    batch.write(dq.push_back("middle"))
    batch.write(dq.push_front("front"))
    batch.write(dq.push_back("back"))
    batch.write(dq.pop_front_op())
    batch.write(dq.pop_back_op())
    batch.write(dq.push_front("new-front"))

    assert batch.commit() == ["front", "back"]
    assert dq.iter(db) == ["new-front", "middle"]


def test_deque_truncate_back(db):
    dq = Store().root().scopes.key("s").recent_votes
    for n in range(10):
        dq.push_back_commit(db, {"a": str(n), "b": "x", "ratio": n})
    dq.truncate_back(db, 3, Durability.SYNC_WAL)
    kept = dq.iter(db)
    assert [v["ratio"] for v in kept] == [0, 1, 2]
    dq.truncate_back(db, 10, Durability.SYNC_WAL)
    assert dq.len(db) == 3


def test_batch_atomicity_and_restart_persistence(tmp_path):
    path = tmp_path / "persist.rocks"
    rust_key = "rust"
    db = RocksDb.open(path)
    try:
        rust = Store().root().scopes.key(rust_key)
        batch = db.batch()
        batch.write(rust.edges.key((0, 1)).add(2.0))
        batch.write(rust.edges.key((1, 0)).add(1.0))
        batch.write(rust.voted_pairs.key((0, 1)).set(True))
        batch.write(rust.recent_votes.push_back({"a": "0", "b": "1", "ratio": 2}))
        batch.write(rust.item_count.add(2))
        batch.commit()
    finally:
        db.close()

    db = RocksDb.open(path)
    try:
        rust = Store().root().scopes.key(rust_key)
        assert rust.edges.key((0, 1)).get(db) == 2.0
        assert rust.edges.key((1, 0)).get(db) == 1.0
        assert rust.voted_pairs.key((0, 1)).get(db) is True
        assert rust.recent_votes.len(db) == 1
        assert rust.item_count.get(db) == 2
    finally:
        db.close()

def test_disable_wal_and_wal_only_visible_within_session(db):
    count = Store().root().scopes.key("s").item_count
    db.run(count.add(7), Durability.DISABLE_WAL)
    assert count.get(db) == 7
    node = Store().root().nodes.key("k")
    db.run(node.set("v"), Durability.WAL_ONLY)
    assert node.get(db) == "v"


def test_namespaced_roots_do_not_collide(db):
    schema = Store()
    a = schema.root("a")
    b = schema.root("b")
    db.run(a.nodes.key("k").set("av"), Durability.SYNC_WAL)
    db.run(b.nodes.key("k").set("bv"), Durability.SYNC_WAL)
    assert a.nodes.key("k").get(db) == "av"
    assert b.nodes.key("k").get(db) == "bv"


def test_prefix_deletion_does_not_touch_siblings(db):
    root = Store().root()
    db.apply(
        [
            root.nodes.key("keep").set("yes"),
            root.scopes.key("gone").item_count.add(1),
            root.scopes.key("stay").item_count.add(9),
        ],
        Durability.SYNC_WAL,
    )
    db.run(root.scopes.key("gone").clear(), Durability.SYNC_WAL)
    assert root.scopes.key("gone").item_count.get(db) == 0
    assert root.scopes.key("stay").item_count.get(db) == 9
    assert root.nodes.key("keep").get(db) == "yes"


def test_put_then_delete_prefix_removes_put(db):
    nodes = Store().root().nodes
    db.apply(
        [nodes.key("new").set("value"), nodes.clear()],
        Durability.SYNC_WAL,
    )
    assert nodes.key("new").get(db) is None
    assert nodes.keys(db) == []


def test_delete_prefix_then_put_preserves_later_put(db):
    nodes = Store().root().nodes
    db.run(nodes.key("old").set("old"), Durability.SYNC_WAL)
    db.apply(
        [nodes.clear(), nodes.key("new").set("value")],
        Durability.SYNC_WAL,
    )
    assert nodes.key("old").get(db) is None
    assert nodes.key("new").get(db) == "value"


def test_root_delete_prefix_order_uses_scan_fallback_sequentially(db):
    root = Store().root()
    db.apply(
        [root.nodes.key("before").set("gone"), root.clear()],
        Durability.SYNC_WAL,
    )
    assert root.nodes.key("before").get(db) is None

    db.apply(
        [root.clear(), root.nodes.key("after").set("kept")],
        Durability.SYNC_WAL,
    )
    assert root.nodes.key("after").get(db) == "kept"


def test_add_ordering_with_put_delete_and_prefix_delete(db):
    count = Store().root().scopes.key("s").item_count

    db.apply([count.set(10), count.add(5)], Durability.SYNC_WAL)
    assert count.get(db) == 15

    db.apply([count.add(7), count.set(3)], Durability.SYNC_WAL)
    assert count.get(db) == 3

    db.apply([count.delete(), count.add(4)], Durability.SYNC_WAL)
    assert count.get(db) == 4

    scope = Store().root().scopes.key("s")
    db.apply([scope.clear(), count.add(9)], Durability.SYNC_WAL)
    assert count.get(db) == 9

    db.apply([count.add(1), scope.clear()], Durability.SYNC_WAL)
    assert count.get(db) == 0


def test_nested_path_selectors(db):
    root = Store().root()
    path = root.scopes.key("rust").edges.key((1, 2))
    db.run(path.add(3.5), Durability.SYNC_WAL)
    assert path.get(db) == 3.5
    assert root.scopes.key("rust").edges.contains(db, (1, 2))
    assert not root.scopes.key("rust").edges.contains(db, (9, 9))


def test_raw_prefix_scan_supports_start_and_limit(db):
    nodes = Store().root().nodes
    db.apply(
        [nodes.key(key).set(key.upper()) for key in ["a", "b", "c", "d"]],
        Durability.SYNC_WAL,
    )
    rows = list(db.scan_prefix(nodes.prefix, limit=2))
    assert len(rows) == 2
    resumed = list(db.scan_prefix(nodes.prefix, start=rows[1][0], limit=2))
    assert resumed[0] == rows[1]  # raw start is inclusive
    assert len(resumed) == 2
    assert list(db.scan_prefix(nodes.prefix, limit=0)) == []
    with pytest.raises(ValueError, match="non-negative"):
        list(db.scan_prefix(nodes.prefix, limit=-1))


def test_map_pages_are_bounded_and_cursor_resumes_nested_maps(db):
    scopes = Store().root().scopes
    expected = ["alpha", "beta", "delta", "epsilon", "gamma"]
    for i, key in enumerate(expected):
        db.apply(
            [
                scopes.key(key).item_count.add(i + 1),
                scopes.key(key).edges.key((0, i)).add(float(i)),
            ],
            Durability.WAL_ONLY,
        )

    all_keys = scopes.keys(db)
    cursor = None
    paged: list[str] = []
    page_sizes: list[int] = []
    while True:
        page = scopes.keys_page(db, cursor=cursor, limit=2)
        paged.extend(page.items)
        page_sizes.append(len(page.items))
        if page.cursor is None:
            break
        cursor = page.cursor

    assert paged == all_keys
    assert all(size <= 2 for size in page_sizes)
    assert len(paged) == len(set(paged)) == len(expected)

    first = scopes.entries_page(db, limit=2)
    assert len(first.items) == 2
    assert all(path.item_count.get(db) > 0 for _, path in first.items)


def test_leaf_map_iter_page_returns_values_without_full_materialization(db):
    nodes = Store().root().nodes
    db.apply(
        [nodes.key(key).set(value) for key, value in zip("abcde", range(5))],
        Durability.WAL_ONLY,
    )
    first = nodes.iter_page(db, limit=2)
    second = nodes.iter_page(db, cursor=first.cursor, limit=2)
    third = nodes.iter_page(db, cursor=second.cursor, limit=2)

    assert [len(first.items), len(second.items), len(third.items)] == [2, 2, 1]
    assert first.cursor is not None
    assert second.cursor is not None
    assert third.cursor is None
    assert dict(first.items + second.items + third.items) == dict(nodes.iter(db))


def test_single_writer_serialization(db):
    counter = Store().root().scopes.key("s").item_count
    errors: list[BaseException] = []
    n_threads = 8
    increments_per = 50

    def worker():
        try:
            for _ in range(increments_per):
                db.run(counter.add(1), Durability.WAL_ONLY)
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert counter.get(db) == n_threads * increments_per


def test_key_separation_siblings_do_not_share_prefixes(db):
    root = Store().root()
    db.apply(
        [
            root.nodes.key("a").set(1),
            root.nodes.key("ab").set(2),
            root.nodes.key("b").set(3),
        ],
        Durability.SYNC_WAL,
    )
    assert root.nodes.key("a").get(db) == 1
    assert root.nodes.key("ab").get(db) == 2
    assert root.nodes.key("b").get(db) == 3
    db.run(root.nodes.key("a").delete(), Durability.SYNC_WAL)
    assert root.nodes.key("a").get(db) is None
    assert root.nodes.key("ab").get(db) == 2


@given(
    ops=st.lists(
        st.tuples(
            st.sampled_from(["set", "delete", "add"]),
            st.text(min_size=1, max_size=8, alphabet=st.characters(whitelist_categories=("L", "N"))),
            st.integers(min_value=-50, max_value=50),
        ),
        max_size=40,
    )
)
@settings(deadline=None, max_examples=40)
def test_property_map_leaf_and_sum_match_model(tmp_path_factory, ops):
    path = tmp_path_factory.mktemp("prop") / "db.rocks"
    db = RocksDb.open(path)
    try:
        schema = Record(vals=Map(Leaf()), scores=Map(Sum()))
        root = schema.root()
        model_vals: dict[str, int] = {}
        model_scores: dict[str, int] = {}

        for kind, key, n in ops:
            if kind == "set":
                db.run(root.vals.key(key).set(n), Durability.WAL_ONLY)
                model_vals[key] = n
            elif kind == "delete":
                db.run(root.vals.key(key).delete(), Durability.WAL_ONLY)
                model_vals.pop(key, None)
            else:
                db.run(root.scores.key(key).add(n), Durability.WAL_ONLY)
                model_scores[key] = model_scores.get(key, 0) + n

        assert dict(root.vals.iter(db)) == model_vals
        for k, v in model_scores.items():
            if v != 0 or root.scores.contains(db, k):
                assert root.scores.key(k).get(db) == v
        for k in root.scores.keys(db):
            assert root.scores.key(k).get(db) == model_scores.get(k, 0)
    finally:
        db.close()


@given(values=st.lists(st.integers(), max_size=30))
@settings(deadline=None, max_examples=30)
def test_property_deque_matches_collections_deque(tmp_path_factory, values):
    path = tmp_path_factory.mktemp("dq") / "db.rocks"
    db = RocksDb.open(path)
    try:
        dq_path = Record(q=Deque(Leaf())).root().q
        model: PyDeque[int] = PyDeque()
        for i, v in enumerate(values):
            if i % 3 == 0:
                dq_path.push_front_commit(db, v)
                model.appendleft(v)
            else:
                dq_path.push_back_commit(db, v)
                model.append(v)
            if i % 5 == 4 and model:
                if i % 2 == 0:
                    assert dq_path.pop_front(db) == model.popleft()
                else:
                    assert dq_path.pop_back(db) == model.pop()
        assert dq_path.iter(db) == list(model)
        assert dq_path.len(db) == len(model)
    finally:
        db.close()


def test_batch_commit_is_atomic_on_success(db):
    root = Store().root()
    batch = db.batch()
    batch.write(root.nodes.key("a").set("1"))
    batch.write(root.nodes.key("b").set("2"))
    batch.write(root.log.push(99))
    batch.commit(Durability.SYNC_WAL)
    assert root.nodes.key("a").get(db) == "1"
    assert root.nodes.key("b").get(db) == "2"
    assert root.log.iter(db) == [99]


def test_persistence_across_reopen_all_collection_kinds(tmp_path):
    path = tmp_path / "all.rocks"
    db = RocksDb.open(path)
    try:
        root = Store().root()
        s = root.scopes.key("s")
        db.run(root.nodes.key("n").set("N"), Durability.SYNC_WAL)
        root.log.push_commit(db, 42)
        db.run(s.edges.key((1, 2)).add(9.0), Durability.SYNC_WAL)
        s.recent_votes.push_back_commit(db, {"a": "a", "b": "b", "ratio": 3})
    finally:
        db.close()

    db = RocksDb.open(path)
    try:
        root = Store().root()
        s = root.scopes.key("s")
        assert root.nodes.key("n").get(db) == "N"
        assert root.log.iter(db) == [42]
        assert s.edges.key((1, 2)).get(db) == 9.0
        assert s.recent_votes.front(db) == {"a": "a", "b": "b", "ratio": 3}
    finally:
        db.close()
