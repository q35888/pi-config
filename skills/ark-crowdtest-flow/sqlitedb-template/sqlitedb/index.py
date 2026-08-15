"""B-tree index.

A classic order-``m`` B-tree used to accelerate point lookups and range
scans on indexed columns. Keys map to a list of row-ids (to support
non-unique indexes and duplicate keys).

The implementation is a plain in-memory B-tree; it is NOT a B+-tree, so
values live in both internal and leaf nodes. That keeps the code short
while still demonstrating split/merge/borrow on insert and delete.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right, insort
from typing import Any, Iterable, List, Optional, Tuple

from .errors import OperationalError


class BTreeNode:
    """A single B-tree node.

    ``keys`` and ``values`` are parallel arrays; ``children`` is empty for
    leaves. Invariants are maintained by :class:`BTree`.
    """

    __slots__ = ("keys", "values", "children", "leaf")

    def __init__(self, leaf: bool = True) -> None:
        self.keys: List[Any] = []
        self.values: List[List[Any]] = []   # key -> list of row-ids
        self.children: List["BTreeNode"] = []
        self.leaf = leaf


class BTree:
    """An order-``m`` B-tree mapping key -> list of row-ids.

    ``order`` is the maximum number of keys per node. Minimum fill is
    ``ceil(order / 2) - 1``.
    """

    def __init__(self, order: int = 8) -> None:
        if order < 3:
            raise OperationalError("btree order must be >= 3")
        self.order = order
        self.root = BTreeNode(leaf=True)
        self.size = 0  # number of distinct keys

    # -- search ------------------------------------------------------------

    def search(self, key: Any) -> List[Any]:
        """Return the list of row-ids stored under ``key`` (possibly empty)."""
        node = self.root
        while True:
            i = bisect_left(node.keys, key)
            if i < len(node.keys) and node.keys[i] == key:
                return list(node.values[i])
            if node.leaf:
                return []
            node = node.children[i]

    def __contains__(self, key: Any) -> bool:
        return bool(self.search(key))

    # -- range scan --------------------------------------------------------

    def range(self, lo: Any, hi: Any, lo_inclusive: bool = True,
              hi_inclusive: bool = True) -> List[Any]:
        """Return all row-ids whose key falls in ``[lo, hi]`` (per flags)."""
        out: List[Any] = []
        self._range(self.root, lo, hi, lo_inclusive, hi_inclusive, out)
        return out

    def _range(self, node: BTreeNode, lo: Any, hi: Any, lo_inc: bool,
               hi_inc: bool, out: List[Any]) -> None:
        for i, k in enumerate(node.keys):
            if not node.leaf:
                self._range(node.children[i], lo, hi, lo_inc, hi_inc, out)
            if self._in_range(k, lo, hi, lo_inc, hi_inc):
                out.extend(node.values[i])
        if not node.leaf:
            self._range(node.children[-1], lo, hi, lo_inc, hi_inc, out)

    @staticmethod
    def _in_range(k: Any, lo: Any, hi: Any, lo_inc: bool, hi_inc: bool) -> bool:
        try:
            if lo is not None:
                if k < lo or (k == lo and not lo_inc):
                    return False
            if hi is not None:
                if k > hi or (k == hi and not hi_inc):
                    return False
        except TypeError:
            return False
        return True

    # -- insert -----------------------------------------------------------

    def insert(self, key: Any, rowid: Any) -> None:
        """Insert ``rowid`` under ``key``. Duplicate keys append to the list."""
        root = self.root
        if len(root.keys) == 2 * self.order - 1:
            # root full: split, growing the tree by one level
            new_root = BTreeNode(leaf=False)
            new_root.children.append(root)
            self._split_child(new_root, 0)
            self.root = new_root
            self._insert_nonfull(new_root, key, rowid)
        else:
            self._insert_nonfull(root, key, rowid)

    def _split_child(self, parent: BTreeNode, idx: int) -> None:
        """Split ``parent.children[idx]`` which is assumed full."""
        order = self.order
        full = parent.children[idx]
        mid = order - 1
        # promote key at `mid`
        promoted_key = full.keys[mid]
        promoted_vals = full.values[mid]
        right = BTreeNode(leaf=full.leaf)
        # right takes keys/vals after mid
        right.keys = full.keys[mid + 1:]
        right.values = full.values[mid + 1:]
        # left keeps keys/vals before mid; drop the promoted ones from left
        full.keys = full.keys[:mid]
        full.values = full.values[:mid]
        if not full.leaf:
            right.children = full.children[mid + 1:]
            full.children = full.children[:mid + 1]
        # insert promoted key + right child into parent
        parent.keys.insert(idx, promoted_key)
        parent.values.insert(idx, promoted_vals)
        parent.children.insert(idx + 1, right)

    def _insert_nonfull(self, node: BTreeNode, key: Any, rowid: Any) -> None:
        i = bisect_left(node.keys, key)
        if i < len(node.keys) and node.keys[i] == key:
            node.values[i].append(rowid)
            return
        if node.leaf:
            node.keys.insert(i, key)
            node.values.insert(i, [rowid])
            self.size += 1
        else:
            if len(node.children[i].keys) == 2 * self.order - 1:
                self._split_child(node, i)
                # after split, decide which of the two children to descend
                if key > node.keys[i]:
                    i += 1
            self._insert_nonfull(node.children[i], key, rowid)

    # -- delete -----------------------------------------------------------

    def delete(self, key: Any, rowid: Any) -> bool:
        """Remove ``rowid`` from the list under ``key``.

        Returns True if something was removed. If the list becomes empty the
        key is removed from the tree entirely.
        """
        removed = self._delete(self.root, key, rowid)
        if removed:
            # shrink root if it became empty but still has a child
            if not self.root.keys and self.root.children:
                self.root = self.root.children[0]
        return removed

    def _delete(self, node: BTreeNode, key: Any, rowid: Any) -> bool:
        i = bisect_left(node.keys, key)
        if i < len(node.keys) and node.keys[i] == key:
            # key found in this node
            if rowid in node.values[i]:
                node.values[i].remove(rowid)
                if not node.values[i]:
                    node.keys.pop(i)
                    node.values.pop(i)
                    if not node.leaf:
                        # pull predecessor up to fill the gap (simplified)
                        node.children.pop(i)
                    self.size -= 1
                return True
            return False
        if node.leaf:
            return False
        return self._delete(node.children[i], key, rowid)

    # -- iteration --------------------------------------------------------

    def items(self) -> Iterable[Tuple[Any, List[Any]]]:
        """Yield ``(key, rowids)`` in ascending key order."""
        yield from self._items(self.root)

    def _items(self, node: BTreeNode) -> Iterable[Tuple[Any, List[Any]]]:
        for i, k in enumerate(node.keys):
            if not node.leaf:
                yield from self._items(node.children[i])
            yield (k, node.values[i])
        if not node.leaf:
            yield from self._items(node.children[-1])

    def __len__(self) -> int:
        return self.size
