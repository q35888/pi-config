"""Query executor.

Walks the AST produced by :mod:`sqlitedb.parser`, evaluated against the
live tables and indexes held by a :class:`~sqlitedb.api.Connection`'s
:class:`Engine`. The executor owns row-level logic: expression evaluation,
filtering, joins, grouping/aggregation, ordering, limit/offset and
projection.

Design notes
------------
* A "row stream" is a list of ``(row_values, rowid_map)`` tuples where
  ``row_values`` is a flat list and ``rowid_map`` maps table-alias ->
  column-offset within that flat list (to resolve qualified column refs
  during joins).
* DML (INSERT/UPDATE/DELETE) mutates the table and writes WAL records via
  the engine's transaction.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from . import parser as P
from .errors import IntegrityError, OperationalError
from .index import BTree
from .planner import Planner
from .storage import Table, TableDef, coerce_value


# A processed row during SELECT execution:
#   (flat_values, {alias_or_table: start_offset})
Stream = List[Tuple[List[Any], Dict[str, int]]]


class Executor:
    def __init__(self, engine: "Engine") -> None:  # type: ignore[name-defined]
        self.engine = engine

    # ------------------------------------------------------------------ DDL

    def exec_create_table(self, stmt: P.CreateTable) -> str:
        self.engine.create_table(stmt)
        return ""

    def exec_create_index(self, stmt: P.CreateIndex) -> str:
        self.engine.create_index(stmt)
        return ""

    def exec_drop_table(self, stmt: P.DropTable) -> str:
        self.engine.drop_table(stmt)
        return ""

    # ------------------------------------------------------------------ DML

    def exec_insert(self, stmt: P.Insert) -> str:
        table = self.engine.get_table(stmt.table)
        defn = table.defn
        target_cols = stmt.columns or defn.colnames
        # validate column names
        for c in target_cols:
            if c not in defn.colnames:
                raise OperationalError(f"no such column: {c}")
        count = 0
        for row_exprs in stmt.values:
            if len(row_exprs) != len(target_cols):
                raise OperationalError(
                    f"INSERT has {len(row_exprs)} values for {len(target_cols)} columns")
            # build a full row in declared order, defaulting missing columns
            row_dict: Dict[str, Any] = {}
            for colname, expr in zip(target_cols, row_exprs):
                row_dict[colname] = eval_expr(expr, {}, {}, None)
            row: List[Any] = []
            for col in defn.columns:
                if col.name in row_dict:
                    row.append(coerce_value(row_dict[col.name], col))
                elif col.default is not None:
                    row.append(coerce_value(col.default, col))
                else:
                    row.append(None)
            # assign rowid for INTEGER PRIMARY KEY
            pk = defn.pk_column
            if pk is not None:
                pk_idx = defn.index_of(pk.name)
                rid = row[pk_idx]
                if rid is None:
                    rid = defn.next_rowid
                    defn.next_rowid += 1
                    row[pk_idx] = rid
                else:
                    # user supplied an explicit id; advance the counter past it
                    if isinstance(rid, int) and rid >= defn.next_rowid:
                        defn.next_rowid = rid + 1
                if rid in table.row_index:
                    if stmt.or_replace:
                        self.engine.delete_row(table, rid)
                    else:
                        raise IntegrityError(
                            f"UNIQUE constraint failed: {pk.name}")
            self.engine.insert_row(table, row)
            count += 1
        return str(count)

    def exec_update(self, stmt: P.Update) -> str:
        table = self.engine.get_table(stmt.table)
        defn = table.defn
        # snapshot the rowids we will touch (we mutate during iteration)
        targets: List[int] = []
        for rid, slot in list(table.row_index.items()):
            row = table.pages[slot[0]].rows[slot[1]]
            ctx = {stmt.table: 0}
            if stmt.where is None or _truthy(
                    eval_expr(stmt.where, {defn.colnames[i]: row[i]
                                           for i in range(len(row))},
                              ctx, None)):
                targets.append(rid)
        changed = 0
        for rid in targets:
            slot = table.row_index[rid]
            row = table.pages[slot[0]].rows[slot[1]]
            new_row = list(row)
            for colname, expr in stmt.assignments:
                idx = defn.index_of(colname)
                # evaluate RHS with access to the current row values
                env = {defn.colnames[j]: new_row[j] for j in range(len(new_row))}
                new_row[idx] = coerce_value(
                    eval_expr(expr, env, {stmt.table: 0}, None),
                    defn.columns[idx])
            # re-insert into index if an indexed column changed
            self.engine.replace_row(table, rid, row, new_row)
            changed += 1
        return str(changed)

    def exec_delete(self, stmt: P.Delete) -> str:
        table = self.engine.get_table(stmt.table)
        defn = table.defn
        targets: List[int] = []
        for rid, slot in list(table.row_index.items()):
            row = table.pages[slot[0]].rows[slot[1]]
            if stmt.where is None:
                targets.append(rid)
                continue
            env = {defn.colnames[i]: row[i] for i in range(len(row))}
            if _truthy(eval_expr(stmt.where, env, {stmt.table: 0}, None)):
                targets.append(rid)
        for rid in targets:
            self.engine.delete_row(table, rid)
        return str(len(targets))

    # ------------------------------------------------------------------ SELECT

    def exec_select(self, stmt: P.Select, plan: Optional[Dict[str, Any]]) \
            -> Tuple[List[str], Stream]:
        rows = self._scan_from(stmt, plan)
        if stmt.where is not None:
            rows = [r for r in rows
                    if _truthy(self._eval_row(stmt.where, r, stmt.from_))]
        # group / aggregate
        if stmt.group_by or _has_agg(stmt.items):
            rows = self._aggregate(stmt, rows)
        elif stmt.having is not None:
            raise OperationalError("HAVING without GROUP BY / aggregates")
        # order by (on pre-projection rows so it can reference any column)
        if stmt.order_by:
            rows = self._order(stmt, rows)
        # project non-aggregate select items onto final columns
        if not (stmt.group_by or _has_agg(stmt.items)):
            rows = [self._project(stmt, r) for r in rows]
        # distinct
        if stmt.distinct:
            seen = set()
            deduped: Stream = []
            for r in rows:
                key = tuple(r[0])
                if key not in seen:
                    seen.add(key)
                    deduped.append(r)
            rows = deduped
        # limit / offset
        if stmt.offset:
            rows = rows[stmt.offset:]
        if stmt.limit is not None:
            rows = rows[:stmt.limit]
        return _select_columns(stmt), rows

    # -- FROM / JOIN scanning --------------------------------------------

    def _scan_from(self, stmt: P.Select,
                   plan: Optional[Dict[str, Any]]) -> Stream:
        from_ = stmt.from_
        if isinstance(from_, P.TableRef):
            return self._scan_table(from_, stmt.where, plan)
        if isinstance(from_, P.Join):
            left = self._scan_sub(from_.left, stmt.where, plan)
            right = self._scan_table(from_.right, stmt.where, None)
            return self._do_join(from_, left, right, stmt.from_)
        raise OperationalError("unsupported FROM")

    def _scan_sub(self, node: Any, where: Optional[P.Expr],
                  plan: Optional[Dict[str, Any]], root_from: Any = None) -> Stream:
        if root_from is None:
            root_from = node
        if isinstance(node, P.TableRef):
            return self._scan_table(node, where, plan)
        if isinstance(node, P.Join):
            left = self._scan_sub(node.left, where, plan, root_from)
            right = self._scan_table(node.right, where, None)
            return self._do_join(node, left, right, root_from)
        raise OperationalError("unsupported FROM subexpression")

    def _scan_table(self, ref: P.TableRef, where: Optional[P.Expr],
                    plan: Optional[Dict[str, Any]]) -> Stream:
        table = self.engine.get_table(ref.name)
        alias = ref.alias or ref.name
        # try to use an index when the hint points at THIS table
        rowids: Optional[List[Any]] = None
        if plan and plan.get("index_hint"):
            ih = plan["index_hint"]
            if ih.get("col") and ref.name == self.engine._hint_table(plan):
                rowids = self._rowids_via_index(ih, table)
        out: Stream = []
        if rowids is not None:
            for rid in rowids:
                slot = table.row_index.get(rid)
                if slot is None:
                    continue
                row = table.pages[slot[0]].rows[slot[1]]
                out.append((list(row), {alias: 0}))
        else:
            for rid, slot in table.row_index.items():
                row = table.pages[slot[0]].rows[slot[1]]
                out.append((list(row), {alias: 0}))
        return out

    def _rowids_via_index(self, hint: Dict[str, Any], table: Table) -> List[Any]:
        tree: BTree = hint["index"]["tree"]
        kind = hint["kind"]
        if kind == "point":
            return tree.search(hint["key"])
        if kind == "range":
            op = hint["op"]
            val = hint["val"]
            if op == ">":
                return tree.range(val, None, lo_inclusive=False)
            if op == ">=":
                return tree.range(val, None, lo_inclusive=True)
            if op == "<":
                return tree.range(None, val, hi_inclusive=False)
            if op == "<=":
                return tree.range(None, val, hi_inclusive=True)
        if kind == "range_between":
            return tree.range(hint["lo"], hint["hi"], True, True)
        return []

    def _do_join(self, join: P.Join, left: Stream, right: Stream,
                 root_from: Any = None) -> Stream:
        out: Stream = []
        right_alias = join.right.alias or join.right.name
        right_width = len(self.engine.get_table(join.right.name).defn.columns)
        from_ctx = root_from if root_from is not None else join
        # determine left alias + width from the first left row (cheap heuristic)
        if join.kind in ("INNER", "LEFT"):
            if not left:
                return []
            left_alias = next(iter(left[0][1].keys()))
            left_width = len(left[0][0]) - 0
            for lrow, lmap in left:
                matched = False
                for rrow, rmap in right:
                    merged = lrow + rrow
                    mmap = dict(lmap)
                    mmap[right_alias] = left_width
                    if join.on is None or _truthy(
                            self._eval_row(join.on, (merged, mmap), from_ctx)):
                        out.append((merged, mmap))
                        matched = True
                if join.kind == "LEFT" and not matched:
                    nulls = [None] * right_width
                    mmap = dict(lmap)
                    mmap[right_alias] = left_width
                    out.append((lrow + nulls, mmap))
            return out
        if join.kind == "RIGHT":
            # symmetric to LEFT
            if not right:
                return []
            left_alias = ""
            for lrow, lmap in left:
                left_alias = next(iter(lmap.keys()))
                break
            left_width = len(left[0][0]) if left else 0
            for rrow, rmap in right:
                matched = False
                for lrow, lmap in left:
                    merged = lrow + rrow
                    mmap = dict(lmap)
                    mmap[right_alias] = left_width
                    if join.on is None or _truthy(
                            self._eval_row(join.on, (merged, mmap), from_ctx)):
                        out.append((merged, mmap))
                        matched = True
                if not matched:
                    nulls = [None] * left_width
                    mmap = {right_alias: left_width}
                    out.append((nulls + rrow, mmap))
            return out
        raise OperationalError(f"unsupported join kind {join.kind}")

    # -- projection (non-aggregate) --------------------------------------

    def _project(self, stmt: P.Select, row: Tuple[List[Any], Dict[str, int]]) \
            -> Tuple[List[Any], Dict[str, int]]:
        vals, amap = row
        out: List[Any] = []
        for item in stmt.items:
            if isinstance(item.expr, P.Star):
                # expand * / t.* to concrete column values
                tbl = item.expr.table
                for alias, start in amap.items():
                    if tbl is not None and alias != tbl:
                        continue
                    width = self.engine._width_for(alias, stmt.from_)
                    out.extend(vals[start:start + width])
            else:
                out.append(self._eval_row(item.expr, row, stmt.from_))
        return (out, {})

    # -- aggregation ------------------------------------------------------

    def _aggregate(self, stmt: P.Select, rows: Stream) -> Stream:
        groups: Dict[Tuple[Any, ...], Stream] = {}
        order: List[Tuple[Any, ...]] = []
        for r in rows:
            if stmt.group_by:
                key = tuple(self._eval_row(g, r, stmt.from_) for g in stmt.group_by)
            else:
                key = ()
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(r)
        out: Stream = []
        for key in order:
            members = groups[key]
            projected = self._project_aggregate(stmt, members)
            out.append((projected, {""}))
            # we lose the alias map post-aggregation; projection already
            # collapsed to final columns, so keep an empty map.
        # HAVING is applied on the aggregated rows
        if stmt.having is not None:
            raise OperationalError("HAVING evaluation on aggregates "
                                   "is not fully supported in this build")
        # rebuild rows as (values, {}) since alias map is gone
        return [(vals, {}) for (vals, _) in out]

    def _project_aggregate(self, stmt: P.Select, members: Stream) -> List[Any]:
        out: List[Any] = []
        for item in stmt.items:
            val = self._eval_agg_expr(item.expr, members, stmt.from_)
            out.append(val)
        return out

    def _eval_agg_expr(self, expr: P.Expr, members: Stream, from_: Any) -> Any:
        if isinstance(expr, P.FuncCall) and expr.name in _AGG:
            fn = expr.name
            if fn == "COUNT":
                if expr.args and isinstance(expr.args[0], P.Star):
                    return len(members)
                vals = [self._eval_row(expr.args[0], m, from_) for m in members]
                vals = [v for v in vals if v is not None]
                if expr.distinct:
                    vals = list({v for v in vals})
                return len(vals)
            vals = [self._eval_row(expr.args[0], m, from_) for m in members]
            vals = [v for v in vals if v is not None]
            if expr.distinct:
                vals = list({v for v in vals})
            if fn == "SUM":
                return sum(vals) if vals else 0
            if fn == "AVG":
                return sum(vals) / len(vals) if vals else None
            if fn == "MIN":
                return min(vals) if vals else None
            if fn == "MAX":
                return max(vals) if vals else None
        # non-aggregate in an aggregate query: take the value from the
        # first member of the group (standard SQL would require it to be a
        # grouping column).
        if members:
            return self._eval_row(expr, members[0], from_)
        return None

    # -- ordering ---------------------------------------------------------

    def _order(self, stmt: P.Select, rows: Stream) -> Stream:
        def sort_key(r: Tuple[List[Any], Dict[str, int]]):
            keys = []
            for item in stmt.order_by:
                v = self._eval_row(item.expr, r, stmt.from_)
                keys.append(_sort_key_value(v))
            return keys

        # Python can't sort with per-column DESC via key alone, so we sort
        # repeatedly from the least significant key to the most significant,
        # reversing per-column. Stable sort makes this correct.
        result = list(rows)
        for item in reversed(stmt.order_by):
            result.sort(
                key=lambda r, e=item.expr: _sort_key_value(
                    self._eval_row(e, r, stmt.from_)),
                reverse=item.desc)
        return result

    # -- expression evaluation against a processed row --------------------

    def _eval_row(self, expr: P.Expr,
                  row: Tuple[List[Any], Dict[str, int]], from_: Any) -> Any:
        vals, amap = row
        env: Dict[str, Any] = {}
        for alias, start in amap.items():
            # we need the table's column count; pull via engine lazily
            width = self.engine._width_for(alias, from_)
            cols = self.engine._cols_for(alias, from_)
            for i in range(width):
                v = vals[start + i]
                env[cols[i]] = v
                env[alias + "." + cols[i]] = v
        return eval_expr(expr, env, amap, None)

    def _eval_row_at(self, expr: P.Expr, vals: List[Any],
                     amap: Dict[str, int]) -> Any:
        env: Dict[str, Any] = {}
        for alias, start in amap.items():
            cols = self.engine._cols_for(alias, None)
            for i, c in enumerate(cols):
                v = vals[start + i]
                env[c] = v
                env[alias + "." + c] = v
        return eval_expr(expr, env, amap, None)


# --------------------------------------------------------------------------
# Expression evaluation (free function — used by DML too)
# --------------------------------------------------------------------------

_AGG = {"COUNT", "SUM", "AVG", "MIN", "MAX"}


def eval_expr(expr: P.Expr, env: Dict[str, Any],
              amap: Dict[str, int], row: Optional[List[Any]]) -> Any:
    if isinstance(expr, P.Literal):
        return expr.value
    if isinstance(expr, P.ColumnRef):
        if expr.table is not None:
            qualified = expr.table + "." + expr.name
            if qualified in env:
                return env[qualified]
            # fall through to unqualified lookup
        return env.get(expr.name)
    if isinstance(expr, P.Star):
        return 1  # COUNT(*) handled by caller
    if isinstance(expr, P.UnaryOp):
        v = eval_expr(expr.operand, env, amap, row)
        if expr.op == "-":
            return -v if v is not None else None
        if expr.op == "+":
            return v
        if expr.op == "NOT":
            return None if v is None else (not _truthy(v))
    if isinstance(expr, P.BinaryOp):
        return _eval_binary(expr, env, amap, row)
    if isinstance(expr, P.IsNull):
        v = eval_expr(expr.expr, env, amap, row)
        res = v is None
        return (not res) if expr.negated else res
    if isinstance(expr, P.Between):
        v = eval_expr(expr.expr, env, amap, row)
        lo = eval_expr(expr.low, env, amap, row)
        hi = eval_expr(expr.high, env, amap, row)
        if v is None or lo is None or hi is None:
            return None
        res = (v >= lo) and (v <= hi)
        return (not res) if expr.negated else res
    if isinstance(expr, P.InList):
        v = eval_expr(expr.expr, env, amap, row)
        if v is None:
            return None
        items = [eval_expr(i, env, amap, row) for i in expr.items]
        res = v in items
        return (not res) if expr.negated else res
    if isinstance(expr, P.FuncCall):
        return _eval_scalar_func(expr, env, amap, row)
    raise OperationalError(f"cannot evaluate expression {expr!r}")


def _eval_binary(expr: P.BinaryOp, env: Dict[str, Any],
                 amap: Dict[str, int], row: Optional[List[Any]]) -> Any:
    op = expr.op
    if op == "AND":
        l = eval_expr(expr.left, env, amap, row)
        if l is not None and not _truthy(l):
            return False
        r = eval_expr(expr.right, env, amap, row)
        if l is None or r is None:
            return None
        return _truthy(l) and _truthy(r)
    if op == "OR":
        l = eval_expr(expr.left, env, amap, row)
        if l is not None and _truthy(l):
            return True
        r = eval_expr(expr.right, env, amap, row)
        if l is None or r is None:
            return None
        return _truthy(l) or _truthy(r)
    l = eval_expr(expr.left, env, amap, row)
    r = eval_expr(expr.right, env, amap, row)
    if op == "LIKE":
        if l is None or r is None:
            return None
        return _like(str(l), str(r))
    if op == "||":
        if l is None or r is None:
            return None
        return f"{l}{r}"
    # arithmetic / comparison: SQL three-valued logic for NULL
    if l is None or r is None:
        if op in _COMPARISON_OPS:
            return None
        return None
    if op == "+":
        return l + r
    if op == "-":
        return l - r
    if op == "*":
        return l * r
    if op == "/":
        if r == 0:
            return None
        # integer division if both operands are ints (SQLite-ish affinity)
        if isinstance(l, int) and isinstance(r, int):
            return l // r
        return l / r
    if op == "%":
        if r == 0:
            return None
        return l % r
    if op in _COMPARISON_OPS:
        return _compare(op, l, r)
    raise OperationalError(f"unsupported operator {op}")


_COMPARISON_OPS = {"=", "!=", "<>", "<", "<=", ">", ">="}


def _compare(op: str, l: Any, r: Any) -> bool:
    try:
        if op == "=":
            return l == r
        if op in ("!=", "<>"):
            return l != r
        if op == "<":
            return l < r
        if op == "<=":
            return l <= r
        if op == ">":
            return l > r
        if op == ">=":
            return l >= r
    except TypeError:
        # incomparable types (e.g. str vs int): compare by type name as a
        # last resort so the engine never crashes.
        l, r = str(l), str(r)
        if op == "=":
            return l == r
        if op in ("!=", "<>"):
            return l != r
        if op == "<":
            return l < r
        if op == "<=":
            return l <= r
        if op == ">":
            return l > r
        return l >= r
    return False


def _like(text: str, pattern: str) -> bool:
    """SQL LIKE: ``%`` matches any run, ``_`` matches one char."""
    regex = "^"
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "%":
            regex += ".*"
        elif c == "_":
            regex += "."
        else:
            regex += re.escape(c)
        i += 1
    regex += "$"
    return re.match(regex, text) is not None


_SCALAR_FUNCS = {
    "ABS": abs,
    "LOWER": lambda s: None if s is None else str(s).lower(),
    "UPPER": lambda s: None if s is None else str(s).upper(),
    "LENGTH": lambda s: None if s is None else len(str(s)),
    "ROUND": lambda v: None if v is None else round(v),
}


def _eval_scalar_func(expr: P.FuncCall, env: Dict[str, Any],
                      amap: Dict[str, int], row: Optional[List[Any]]) -> Any:
    name = expr.name
    if name in _AGG:
        raise OperationalError(f"aggregate {name} not allowed here")
    if name in _SCALAR_FUNCS:
        args = [eval_expr(a, env, amap, row) for a in expr.args]
        return _SCALAR_FUNCS[name](*args)
    raise OperationalError(f"no such function: {name}")


def _truthy(v: Any) -> bool:
    """SQL truthiness for a WHERE result.

    Numbers are true iff non-zero. Strings are false (SQL does not treat
    non-empty strings as true). NULL is not true.
    """
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return False


def _sort_key_value(v: Any) -> Tuple[int, Any]:
    """Sort key that keeps NULLs first and avoids type errors."""
    if v is None:
        return (0, 0)
    if isinstance(v, (int, float)):
        return (1, v)
    return (2, str(v))


def _has_agg(items: List[P.SelectItem]) -> bool:
    def has(e: P.Expr) -> bool:
        if isinstance(e, P.FuncCall) and e.name in _AGG:
            return True
        if isinstance(e, P.BinaryOp):
            return has(e.left) or has(e.right)
        if isinstance(e, P.UnaryOp):
            return has(e.operand)
        return False
    return any(has(it.expr) for it in items)


def _select_columns(stmt: P.Select) -> List[str]:
    cols: List[str] = []
    for item in stmt.items:
        if isinstance(item.expr, P.Star):
            # resolved later by caller against the schema; placeholder name
            cols.append("*")
        elif item.alias:
            cols.append(item.alias)
        elif isinstance(item.expr, P.ColumnRef):
            cols.append(item.expr.name)
        elif isinstance(item.expr, P.FuncCall):
            cols.append(item.expr.name.lower())
        else:
            cols.append("?column?")
    return cols
