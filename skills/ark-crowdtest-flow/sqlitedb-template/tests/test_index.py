"""B-tree index unit tests."""

import pytest

from sqlitedb.index import BTree


def test_insert_and_search():
    bt = BTree(order=4)
    bt.insert(10, "r10")
    bt.insert(20, "r20")
    bt.insert(5, "r5")
    assert bt.search(10) == ["r10"]
    assert bt.search(20) == ["r20"]
    assert bt.search(5) == ["r5"]
    assert bt.search(999) == []


def test_duplicate_keys_append():
    bt = BTree(order=4)
    bt.insert(5, "a")
    bt.insert(5, "b")
    bt.insert(5, "c")
    assert bt.search(5) == ["a", "b", "c"]


def test_split_on_full_root():
    bt = BTree(order=3)  # max 2*3-1 = 5 keys per node
    for k in range(20):
        bt.insert(k, k)
    for k in range(20):
        assert bt.search(k) == [k]
    assert bt.size == 20


def test_range_query():
    bt = BTree(order=4)
    for k in [1, 3, 5, 7, 9, 11]:
        bt.insert(k, k)
    # inclusive range [3, 9]
    assert sorted(bt.range(3, 9)) == [3, 5, 7, 9]
    # half-open via flags
    assert sorted(bt.range(3, 9, lo_inclusive=False, hi_inclusive=False)) \
        == [5, 7]


def test_delete_removes_rowid():
    bt = BTree(order=4)
    bt.insert(5, "a")
    bt.insert(5, "b")
    assert bt.delete(5, "a") is True
    assert bt.search(5) == ["b"]
    # deleting last rowid under a key removes the key
    assert bt.delete(5, "b") is True
    assert bt.search(5) == []
    assert bt.size == 0


def test_items_sorted():
    bt = BTree(order=4)
    for k in [5, 1, 9, 3, 7]:
        bt.insert(k, k)
    keys = [k for k, _ in bt.items()]
    assert keys == sorted(keys)
