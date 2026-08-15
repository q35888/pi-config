"""Transactions and Write-Ahead Log (WAL).

The engine keeps a small in-memory WAL of physical operations. A transaction
appends records; COMMIT flushes them (here: marks them durable by appending
to the engine's applied log) and ROLLBACK replays them in reverse to undo.

Record types
------------
* ``{"op": "insert", "table": str, "rowid": int, "row": list}``
* ``{"op": "update", "table": str, "rowid": int, "before": list, "after": list}``
* ``{"op": "delete", "table": str, "rowid": int, "before": list}``
* ``{"op": "create_table", "table": str, "defn": dict}``
* ``{"op": "drop_table", "table": str, "defn": dict}``
* ``{"op": "create_index", "index": str, "table": str}``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class Transaction:
    """A single in-flight transaction.

    ``records`` are the physical changes accumulated since BEGIN. ``active``
    is False after COMMIT or ROLLBACK.
    """
    records: List[Dict[str, Any]] = field(default_factory=list)
    active: bool = True

    def append(self, rec: Dict[str, Any]) -> None:
        self.records.append(rec)


class WAL:
    """Write-ahead log + transaction manager.

    The engine calls :meth:`record` for every mutation. When no explicit
    transaction is open, every statement auto-commits (its records go
    straight to ``applied``). When a transaction *is* open, records buffer
    in ``current`` until COMMIT/ROLLBACK.
    """

    def __init__(self) -> None:
        self.applied: List[Dict[str, Any]] = []
        self.current: Transaction = Transaction(active=False)

    # -- transaction control ----------------------------------------------

    def begin(self) -> None:
        # nested BEGIN is an error in most engines; here we silently
        # commit-and-reopen to keep the API forgiving.
        if self.current.active:
            self.commit()
        self.current = Transaction(active=True)

    def commit(self) -> List[Dict[str, Any]]:
        if not self.current.active:
            return []
        recs = self.current.records
        self.applied.extend(recs)
        self.current = Transaction(active=False)
        return recs

    def rollback(self) -> List[Dict[str, Any]]:
        if not self.current.active:
            return []
        recs = self.current.records
        self.current = Transaction(active=False)
        return recs

    @property
    def in_transaction(self) -> bool:
        return self.current.active

    # -- recording --------------------------------------------------------

    def record(self, rec: Dict[str, Any]) -> None:
        """Record a mutation.

        Inside a transaction it buffers; outside it auto-commits.
        """
        if self.current.active:
            self.current.append(rec)
        else:
            self.applied.append(rec)
