"""Storage layer.

A small page-oriented storage engine. Data lives in a list of fixed-size
pages; each page holds a list of rows. The page abstraction is intentionally
simple (in-memory) but mirrors the shape a real page store would have, so it
is easy to swap for an on-disk backend later.

Row representation
------------------
Internally a row is a plain ``list`` whose values are in declared-column
order. :func:`serialize_row` / :func:`deserialize_row` convert between the
Python value list and a compact byte representation (used by the WAL).

Schema representation
---------------------
:class:`TableDef` and :class:`Schema` describe a database's tables and
columns. The schema is kept in memory and, for a persisted database, also
written as JSON in page 0.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .errors import OperationalError


PAGE_SIZE = 4096
ROWS_PER_PAGE = 256  # soft cap; real pages are variable-size in this toy

# Sentinel placed in a page's row list when a row has been deleted but not
# physically removed (a tombstone). Keeping the slot occupied keeps every
# other row's (page, slot) pointer stable.
_TOMBSTONE = object()


@dataclass
class ColumnDef:
    """Mirror of parser.ColumnDef but storage-flavoured (no expr default)."""
    name: str
    type: str               # INTEGER | TEXT | REAL | BLOB
    primary_key: bool = False
    not_null: bool = False
    unique: bool = False
    default: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "type": self.type, "pk": self.primary_key,
            "not_null": self.not_null, "unique": self.unique,
            "default": self.default,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ColumnDef":
        return cls(d["name"], d["type"], d.get("pk", False),
                   d.get("not_null", False), d.get("unique", False),
                   d.get("default"))


@dataclass
class TableDef:
    name: str
    columns: List[ColumnDef]
    next_rowid: int = 1   # monotonic row id, used for INTEGER PRIMARY KEY

    @property
    def colnames(self) -> List[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> ColumnDef:
        for c in self.columns:
            if c.name == name:
                return c
        raise OperationalError(f"no such column: {name}")

    def index_of(self, name: str) -> int:
        for i, c in enumerate(self.columns):
            if c.name == name:
                return i
        raise OperationalError(f"no such column: {name}")

    @property
    def pk_column(self) -> Optional[ColumnDef]:
        for c in self.columns:
            if c.primary_key:
                return c
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name,
                "columns": [c.to_dict() for c in self.columns],
                "next_rowid": self.next_rowid}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TableDef":
        return cls(d["name"], [ColumnDef.from_dict(c) for c in d["columns"]],
                   d.get("next_rowid", 1))


@dataclass
class Page:
    """A page is just a list of rows plus a flag for whether it is dirty."""
    rows: List[List[Any]] = field(default_factory=list)
    dirty: bool = False


@dataclass
class Table:
    """A live table: its schema plus its pages and its row-id index."""
    defn: TableDef
    pages: List[Page] = field(default_factory=list)
    # rowid -> (page_index, slot_index); the physical location of each row.
    row_index: Dict[int, Any] = field(default_factory=dict)

    def all_rows(self) -> List[List[Any]]:
        out: List[List[Any]] = []
        for p in self.pages:
            out.extend(p.rows)
        return out

    def __len__(self) -> int:
        return sum(len(p.rows) for p in self.pages)

    def append_row(self, row: List[Any]) -> int:
        """Append a row, returning its physical slot id.

        Rows are packed into the last page; a new page is allocated when the
        last one is full. The caller is responsible for assigning the row id.
        """
        if self.pages and len(self.pages[-1].rows) < ROWS_PER_PAGE:
            page = self.pages[-1]
        else:
            page = Page()
            self.pages.append(page)
        page.rows.append(row)
        page.dirty = True
        slot = (len(self.pages) - 1, len(page.rows) - 1)
        return slot

    def remove_row_at(self, slot: Any) -> None:
        """Mark the row at ``slot`` as deleted (a tombstone).

        We do NOT physically pop the row: popping would shift the slot
        indices of every later row in the same page and silently invalidate
        every (page, slot) pointer still held in :attr:`row_index`. Marking
        a tombstone keeps slot addresses stable, which is what the rest of
        the engine relies on. Scanners skip tombstones via :meth:`is_tomb`.
        """
        page_idx, slot_idx = slot
        self.pages[page_idx].rows[slot_idx] = _TOMBSTONE
        self.pages[page_idx].dirty = True

    @staticmethod
    def is_tomb(row: Any) -> bool:
        return row is _TOMBSTONE


# --------------------------------------------------------------------------
# Row serialization (used by WAL + optional persistence)
# --------------------------------------------------------------------------

# Tag bytes for the type of each value within a serialized row.
_T_NULL = b"\x00"
_T_INT = b"\x01"
_T_FLOAT = b"\x02"
_T_TEXT = b"\x03"
_T_BLOB = b"\x04"


def serialize_row(row: List[Any]) -> bytes:
    """Serialize a row to a compact, type-tagged byte string."""
    parts: List[bytes] = []
    parts.append(struct.pack(">H", len(row)))  # column count
    for val in row:
        if val is None:
            parts.append(_T_NULL)
        elif isinstance(val, bool):
            # bool is a subclass of int — store as int 0/1.
            parts.append(_T_INT)
            parts.append(struct.pack(">q", int(val)))
        elif isinstance(val, int):
            parts.append(_T_INT)
            parts.append(struct.pack(">q", val))
        elif isinstance(val, float):
            parts.append(_T_FLOAT)
            parts.append(struct.pack(">d", val))
        elif isinstance(val, (bytes, bytearray)):
            parts.append(_T_BLOB)
            b = bytes(val)
            parts.append(struct.pack(">I", len(b)))
            parts.append(b)
        elif isinstance(val, str):
            parts.append(_T_TEXT)
            b = val.encode("utf-8")
            parts.append(struct.pack(">I", len(b)))
            parts.append(b)
        else:
            # fallback: stringify unknown types
            parts.append(_T_TEXT)
            b = str(val).encode("utf-8")
            parts.append(struct.pack(">I", len(b)))
            parts.append(b)
    return b"".join(parts)


def deserialize_row(data: bytes) -> List[Any]:
    """Inverse of :func:`serialize_row`."""
    pos = 0
    (count,) = struct.unpack_from(">H", data, pos)
    pos += 2
    out: List[Any] = []
    for _ in range(count):
        tag = data[pos:pos + 1]
        pos += 1
        if tag == _T_NULL:
            out.append(None)
        elif tag == _T_INT:
            (v,) = struct.unpack_from(">q", data, pos)
            pos += 8
            out.append(v)
        elif tag == _T_FLOAT:
            (v,) = struct.unpack_from(">d", data, pos)
            pos += 8
            out.append(v)
        elif tag == _T_BLOB:
            (n,) = struct.unpack_from(">I", data, pos)
            pos += 4
            out.append(data[pos:pos + n])
            pos += n
        elif tag == _T_TEXT:
            (n,) = struct.unpack_from(">I", data, pos)
            pos += 4
            out.append(data[pos:pos + n].decode("utf-8"))
            pos += n
        else:
            raise OperationalError(f"unknown value tag {tag!r}")
    return out


# --------------------------------------------------------------------------
# Schema (catalog)
# --------------------------------------------------------------------------

@dataclass
class Schema:
    """The database catalog: all tables keyed by name."""
    tables: Dict[str, TableDef] = field(default_factory=dict)

    def get(self, name: str) -> TableDef:
        if name not in self.tables:
            raise OperationalError(f"no such table: {name}")
        return self.tables[name]

    def add(self, table: TableDef) -> None:
        if table.name in self.tables:
            raise OperationalError(f"table already exists: {table.name}")
        self.tables[table.name] = table

    def drop(self, name: str) -> None:
        if name not in self.tables:
            raise OperationalError(f"no such table: {name}")
        del self.tables[name]

    def to_json(self) -> str:
        return json.dumps({"tables": [t.to_dict() for t in self.tables.values()]})

    @classmethod
    def from_json(cls, text: str) -> "Schema":
        data = json.loads(text)
        return cls({t["name"]: TableDef.from_dict(t)
                    for t in data.get("tables", [])})


def coerce_value(value: Any, col: ColumnDef) -> Any:
    """Coerce a value to the column's declared type, applying affinity.

    The rules are deliberately SQLite-like and permissive: an INTEGER column
    accepts a numeric string, a TEXT column stringifies everything.
    """
    if value is None:
        return None
    t = col.type.upper()
    if t == "INTEGER":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value) if value.is_integer() else value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                try:
                    return float(value)
                except ValueError:
                    return value
        return value
    if t == "REAL":
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return value
        return value
    if t == "BLOB":
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        return value
    # TEXT / default: stringify per affinity
    return value if isinstance(value, str) else str(value)
