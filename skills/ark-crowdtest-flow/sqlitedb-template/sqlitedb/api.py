"""Public API.

``connect(name)`` returns a :class:`Connection`. A connection holds the
:class:`Engine` (catalog + tables + indexes + WAL) and exposes a DB-API
2.0-flavoured interface: ``execute``, ``executemany``, ``commit``,
``rollback``, and a result-bearing :class:`Cursor`.

Example
-------
>>> con = connect(":memory:")
>>> con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER)")
>>> con.execute("INSERT INTO t (v) VALUES (10), (20), (30)")
>>> cur = con.execute("SELECT SUM(v) FROM t")
>>> cur.fetchall()
[(60,)]
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import parser as P
from .errors import DatabaseError, IntegrityError, OperationalError
from .executor import Executor
from .index import BTree
from .planner import Planner
from .storage import (ColumnDef as SColumnDef, Schema, Table, TableDef,
                      coerce_value)
from .transaction import WAL


class Engine:
    """The running database: schema, live tables, indexes and WAL."""

    def __init__(self) -> None:
        self.schema = Schema()
        self.tables: Dict[str, Table] = {}
        # table_name -> {column_name -> {"tree": BTree, "unique": bool}}
        self.indexes: Dict[str, Dict[str, Dict[str, Any]]] = {}
        # index_name -> (table, column, unique)
        self.index_names: Dict[str, Tuple[str, str, bool]] = {}
        self.wal = WAL()

    # -- DDL --------------------------------------------------------------

    def create_table(self, stmt: P.CreateTable) -> None:
        if stmt.if_not_exists and stmt.table in self.schema.tables:
            return
        cols = [SColumnDef(c.name, c.type, c.primary_key, c.not_null, c.unique,
                           _default_of(c.default))
                for c in stmt.columns]
        # validate: at most one PRIMARY KEY column
        if sum(1 for c in cols if c.primary_key) > 1:
            raise OperationalError("multiple PRIMARY KEY columns")
        defn = TableDef(stmt.table, cols)
        self.schema.add(defn)
        self.tables[stmt.table] = Table(defn)
        self.indexes.setdefault(stmt.table, {})
        # UNIQUE columns get an implicit unique index
        for c in cols:
            if c.unique:
                self._make_index("_idx_auto_" + stmt.table + "_" + c.name,
                                 stmt.table, c.name, unique=True)
        self.wal.record({"op": "create_table", "table": stmt.table,
                         "defn": defn.to_dict()})

    def create_index(self, stmt: P.CreateIndex) -> None:
        self._make_index(stmt.index, stmt.table, stmt.columns[0],
                         unique=stmt.unique)

    def _make_index(self, name: str, table: str, column: str,
                    unique: bool) -> None:
        if table not in self.tables:
            raise OperationalError(f"no such table: {table}")
        defn = self.tables[table].defn
        if column not in defn.colnames:
            raise OperationalError(f"no such column: {column}")
        tree = BTree(order=8)
        # backfill existing rows
        tbl = self.tables[table]
        col_idx = defn.index_of(column)
        for rid, slot in tbl.row_index.items():
            tree.insert(tbl.pages[slot[0]].rows[slot[1]][col_idx], rid)
        self.indexes.setdefault(table, {})[column] = {"tree": tree,
                                                       "unique": unique,
                                                       "name": name}
        self.index_names[name] = (table, column, unique)
        self.wal.record({"op": "create_index", "index": name,
                         "table": table, "column": column})

    def drop_table(self, stmt: P.DropTable) -> None:
        if stmt.if_exists and stmt.table not in self.schema.tables:
            return
        defn = self.schema.get(stmt.table)
        self.wal.record({"op": "drop_table", "table": stmt.table,
                         "defn": defn.to_dict()})
        self.schema.drop(stmt.table)
        del self.tables[stmt.table]
        self.indexes.pop(stmt.table, None)
        # drop indexes that referenced this table
        for name in [n for n, (t, _, _) in self.index_names.items()
                     if t == stmt.table]:
            del self.index_names[name]

    # -- row mutation -----------------------------------------------------

    def insert_row(self, table: Table, row: List[Any]) -> None:
        defn = table.defn
        # NOT NULL / UNIQUE checks
        for i, col in enumerate(defn.columns):
            if col.not_null and row[i] is None:
                raise IntegrityError(f"NOT NULL constraint failed: {col.name}")
        # unique via explicit indexes
        idx_map = self.indexes.get(defn.name, {})
        for colname, info in idx_map.items():
            if not info["unique"]:
                continue
            ci = defn.index_of(colname)
            if row[ci] is not None and info["tree"].search(row[ci]):
                raise IntegrityError(
                    f"UNIQUE constraint failed: {defn.name}.{colname}")
        slot = table.append_row(row)
        # find rowid
        rid = _rowid_of(table, row)
        if rid is None:
            # no PK: synthesize one using the slot for index bookkeeping
            rid = id(slot)
        table.row_index[rid] = slot
        # maintain secondary indexes
        for colname, info in idx_map.items():
            ci = defn.index_of(colname)
            info["tree"].insert(row[ci], rid)
        self.wal.record({"op": "insert", "table": defn.name,
                         "rowid": rid, "row": list(row)})

    def replace_row(self, table: Table, rid: int,
                    before: List[Any], after: List[Any]) -> None:
        defn = table.defn
        slot = table.row_index[rid]
        table.pages[slot[0]].rows[slot[1]] = after
        table.pages[slot[0]].dirty = True
        # maintain indexes: remove old key, insert new key
        idx_map = self.indexes.get(defn.name, {})
        for colname, info in idx_map.items():
            ci = defn.index_of(colname)
            if before[ci] != after[ci]:
                info["tree"].delete(before[ci], rid)
                info["tree"].insert(after[ci], rid)
        self.wal.record({"op": "update", "table": defn.name, "rowid": rid,
                         "before": list(before), "after": list(after)})

    def delete_row(self, table: Table, rid: int) -> None:
        slot = table.row_index.pop(rid)
        defn = table.defn
        row = table.pages[slot[0]].rows[slot[1]]
        # maintain indexes
        idx_map = self.indexes.get(defn.name, {})
        for colname, info in idx_map.items():
            ci = defn.index_of(colname)
            info["tree"].delete(row[ci], rid)
        table.remove_row_at(slot)
        self.wal.record({"op": "delete", "table": defn.name,
                         "rowid": rid, "before": list(row)})

    # -- helpers used by the executor -------------------------------------

    def get_table(self, name: str) -> Table:
        if name not in self.tables:
            raise OperationalError(f"no such table: {name}")
        return self.tables[name]

    def _hint_table(self, plan: Dict[str, Any]) -> Optional[str]:
        return _driving_name(plan["stmt"].from_)

    def _width_for(self, alias: str, from_: Any) -> int:
        tname = _resolve_alias(alias, from_)
        return len(self.tables[tname].defn.columns)

    def _cols_for(self, alias: str, from_: Any) -> List[str]:
        tname = _resolve_alias(alias, from_)
        return self.tables[tname].defn.colnames


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _default_of(expr: Optional[P.Expr]) -> Any:
    if expr is None:
        return None
    if isinstance(expr, P.Literal):
        return expr.value
    return None


def _rowid_of(table: Table, row: List[Any]) -> Optional[int]:
    pk = table.defn.pk_column
    if pk is None:
        return None
    return row[table.defn.index_of(pk.name)]


def _driving_name(from_: Any) -> Optional[str]:
    if isinstance(from_, P.TableRef):
        return from_.name
    if isinstance(from_, P.Join):
        return _driving_name(from_.left)
    return None


def _resolve_alias(alias: str, from_: Any) -> str:
    """Map an alias-or-tablename back to a real table name.

    Returns the alias if nothing better is found (callers treat it as a
    table name as a fallback).
    """
    found = _find_alias(alias, from_)
    return found or alias


def _find_alias(alias: str, from_: Any) -> Optional[str]:
    if isinstance(from_, P.TableRef):
        if from_.alias == alias or from_.name == alias:
            return from_.name
        return None
    if isinstance(from_, P.Join):
        return _find_alias(alias, from_.left) or _find_alias(alias, from_.right)
    return None


# --------------------------------------------------------------------------
# Connection / Cursor
# --------------------------------------------------------------------------

class Cursor:
    """Holds the result of the last SELECT."""

    def __init__(self) -> None:
        self.columns: List[str] = []
        self.rows: List[Tuple[Any, ...]] = []

    def fetchone(self) -> Optional[Tuple[Any, ...]]:
        if not self.rows:
            return None
        return self.rows.pop(0)

    def fetchall(self) -> List[Tuple[Any, ...]]:
        out = self.rows
        self.rows = []
        return out

    def fetchmany(self, n: int) -> List[Tuple[Any, ...]]:
        out = self.rows[:n]
        self.rows = self.rows[n:]
        return out

    def __iter__(self):
        return iter(self.fetchall())


class Connection:
    """A database connection.

    ``autocommit`` governs whether DML outside an explicit BEGIN writes WAL
    records immediately (True) or buffers them until commit (False, the
    default and the DB-API norm).
    """

    def __init__(self, name: str = ":memory:") -> None:
        self.name = name
        self.engine = Engine()
        self._executor = Executor(self.engine)
        # connection-level transaction: BEGIN opens it.
        self.autocommit = False

    # -- statement execution ---------------------------------------------

    def execute(self, sql: str, params: Optional[Sequence[Any]] = None) \
            -> "Cursor":
        if params:
            sql = _apply_params(sql, params)
        statements = P.parse(sql)
        cur = Cursor()
        for stmt in statements:
            self._exec_one(stmt, cur)
        return cur

    def executemany(self, sql: str,
                    seq_of_params: Sequence[Sequence[Any]]) -> None:
        for params in seq_of_params:
            self.execute(sql, params)

    def _exec_one(self, stmt: P.Statement, cur: Cursor) -> None:
        if isinstance(stmt, P.Begin):
            self.engine.wal.begin()
            return
        if isinstance(stmt, P.Commit):
            self.engine.wal.commit()
            return
        if isinstance(stmt, P.Rollback):
            self._do_rollback()
            return
        if isinstance(stmt, (P.CreateTable, P.CreateIndex, P.DropTable)):
            self._exec_ddl(stmt)
            return
        if isinstance(stmt, P.Insert):
            msg = self._executor.exec_insert(stmt)
            self._after_write()
            return
        if isinstance(stmt, P.Update):
            self._executor.exec_update(stmt)
            self._after_write()
            return
        if isinstance(stmt, P.Delete):
            self._executor.exec_delete(stmt)
            self._after_write()
            return
        if isinstance(stmt, P.Select):
            self._exec_select(stmt, cur)
            return
        raise DatabaseError(f"unsupported statement {type(stmt).__name__}")

    def _exec_ddl(self, stmt: P.Statement) -> None:
        ex = self._executor
        if isinstance(stmt, P.CreateTable):
            ex.exec_create_table(stmt)
        elif isinstance(stmt, P.CreateIndex):
            ex.exec_create_index(stmt)
        elif isinstance(stmt, P.DropTable):
            ex.exec_drop_table(stmt)
        self._after_write()

    def _exec_select(self, stmt: P.Select, cur: Cursor) -> None:
        planner = Planner(self.engine.schema, self.engine.indexes)
        plan = planner.plan_select(stmt)
        columns, rows = self._executor.exec_select(stmt, plan)
        # expand Star into real column names
        real_cols = self._expand_columns(stmt, columns)
        cur.columns = real_cols
        cur.rows = [tuple(r[0]) for r in rows]

    def _expand_columns(self, stmt: P.Select, cols: List[str]) -> List[str]:
        out: List[str] = []
        for i, c in enumerate(cols):
            if c == "*":
                item = stmt.items[i].expr
                tbl = getattr(item, "table", None)
                from_ = stmt.from_
                names = _all_columns(self.engine, from_, tbl)
                out.extend(names)
            else:
                out.append(c)
        return out

    def _after_write(self) -> None:
        # auto-commit semantics: if not in an explicit transaction, the WAL
        # already recorded the op as applied (WAL auto-commits outside BEGIN).
        pass

    def _do_rollback(self) -> None:
        recs = self.engine.wal.rollback()
        # undo in reverse order
        for rec in reversed(recs):
            _undo(self.engine, rec)

    # -- transaction API --------------------------------------------------

    def commit(self) -> None:
        self.engine.wal.commit()

    def rollback(self) -> None:
        self._do_rollback()

    def close(self) -> None:
        # nothing persistent to flush in this toy
        pass

    # -- introspection ----------------------------------------------------

    @property
    def tables(self) -> Dict[str, TableDef]:
        return self.engine.schema.tables

    def table_names(self) -> List[str]:
        return list(self.engine.schema.tables.keys())


def connect(name: str = ":memory:") -> Connection:
    """Open a connection to a database.

    ``":memory:"`` creates a fresh in-memory database. Any other name is
    accepted but, in this build, is treated identically (persistence is
    stubbed out — see docs/persistence.md).
    """
    return Connection(name)


# --------------------------------------------------------------------------
# rollback replay + parameter substitution
# --------------------------------------------------------------------------

def _undo(engine: Engine, rec: Dict[str, Any]) -> None:
    op = rec["op"]
    if op == "insert":
        table = engine.tables.get(rec["table"])
        if table and rec["rowid"] in table.row_index:
            engine.delete_row(table, rec["rowid"])
    elif op == "delete":
        table = engine.tables.get(rec["table"])
        if table:
            engine.insert_row(table, rec["before"])
    elif op == "update":
        table = engine.tables.get(rec["table"])
        if table and rec["rowid"] in table.row_index:
            engine.replace_row(table, rec["rowid"], rec["after"],
                               rec["before"])
    elif op == "create_table":
        if rec["table"] in engine.tables:
            engine.schema.drop(rec["table"])
            engine.tables.pop(rec["table"], None)
            engine.indexes.pop(rec["table"], None)
    elif op == "drop_table":
        # recreate from saved defn
        defn = TableDef.from_dict(rec["defn"])
        engine.schema.tables[rec["table"]] = defn
        engine.tables[rec["table"]] = Table(defn)


def _apply_params(sql: str, params: Sequence[Any]) -> str:
    """Naive ``?`` placeholder substitution (stringification only).

    This is intentionally minimal and is one of the rough edges the project
    ships with — it does not escape values and should not be used with
    untrusted input.
    """
    out: List[str] = []
    pi = 0
    for ch in sql:
        if ch == "?":
            v = params[pi]
            pi += 1
            if v is None:
                out.append("NULL")
            elif isinstance(v, str):
                out.append("'" + v.replace("'", "''") + "'")
            elif isinstance(v, bool):
                out.append("1" if v else "0")
            else:
                out.append(str(v))
        else:
            out.append(ch)
    return "".join(out)


def _all_columns(engine: Engine, from_: Any, table: Optional[str]) -> List[str]:
    """Resolve column names for a ``SELECT *`` possibly qualified by table."""
    targets: List[str] = []
    for tname in _iter_tables(from_):
        if table is not None and tname != table:
            continue
        targets.extend(engine.tables[tname].defn.colnames)
    return targets


def _iter_tables(from_: Any):
    if isinstance(from_, P.TableRef):
        yield from_.name
    elif isinstance(from_, P.Join):
        yield from _iter_tables(from_.left)
        yield from _iter_tables(from_.right)
