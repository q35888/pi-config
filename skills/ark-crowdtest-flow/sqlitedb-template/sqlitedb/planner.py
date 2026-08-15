"""Query planner.

Translates a parsed :class:`~sqlitedb.parser.Select` AST into a physical
plan the executor can run. The planner is small: it decides, per
conjunction in the WHERE clause, whether an existing B-tree index can
satisfy the predicate (point lookup or range scan). Everything else falls
back to a sequential scan.

Plan node shapes (plain dicts, to keep them lightweight)::

    {"op": "scan", "table": "users", "alias": "u"}
    {"op": "index_lookup", "table": "users", "index": "idx_age", "key": 30}
    {"op": "index_range", "table": "users", "index": "idx_age",
     "lo": 30, "hi": 40, "lo_inc": True, "hi_inc": True}
    {"op": "filter", "input": <plan>, "pred": <expr>}
    {"op": "join", "left": <plan>, "right": <plan>, "on": <expr?>, "kind": "INNER"}

For this toy the executor mostly consumes AST directly; the planner's job is
to *annotate* the FROM clause with which index (if any) to use, so the
executor can pick an index scan over a seq scan.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import parser as P
from .errors import OperationalError
from .storage import Schema


class Planner:
    """Builds a scan/filter plan and records index hints per table."""

    def __init__(self, schema: Schema,
                 indexes: Dict[str, Dict[str, Any]]) -> None:
        """
        ``indexes`` maps ``table_name -> {column_name -> {"tree": BTree, ...}}``.
        """
        self.schema = schema
        self.indexes = indexes

    def plan_select(self, stmt: P.Select) -> Dict[str, Any]:
        """Return a plan dict for a SELECT.

        The plan embeds the original AST pieces the executor needs, plus an
        ``index_hint`` describing any usable index on the driving table.
        """
        # resolve the (single) driving table for index selection purposes.
        # multi-table joins always seq-scan the right side in this toy.
        driving = _driving_table(stmt.from_)
        index_hint = None
        if driving is not None and stmt.where is not None:
            index_hint = self._best_index(driving, stmt.where)
        return {
            "stmt": stmt,
            "index_hint": index_hint,
        }

    def _best_index(self, table: str, where: P.Expr) -> Optional[Dict[str, Any]]:
        """Scan the WHERE tree for a simple predicate on an indexed column.

        Recognises ``col = literal`` (point lookup) and
        ``col < / <= / > / >= literal`` or ``col BETWEEN a AND b`` (range).
        Picks the first match; no cost model.
        """
        idx_map = self.indexes.get(table, {})
        if not idx_map:
            return None
        found: List[Dict[str, Any]] = []

        def walk(expr: P.Expr) -> None:
            if isinstance(expr, P.BinaryOp):
                op = expr.op
                left, right = expr.left, expr.right
                col = _as_col_ref(left, table)
                lit = _as_literal(right)
                if col and lit is not None and col in idx_map:
                    if op == "=":
                        found.append({"index": idx_map[col], "col": col,
                                      "kind": "point", "key": lit})
                    elif op in ("<", "<=", ">", ">="):
                        found.append({"index": idx_map[col], "col": col,
                                      "kind": "range", "op": op, "val": lit})
                # also handle the mirrored form literal <op> col
                col2 = _as_col_ref(right, table)
                lit2 = _as_literal(left)
                if col2 and lit2 is not None and col2 in idx_map:
                    mirrored = _mirror_op(op)
                    if mirrored == "=":
                        found.append({"index": idx_map[col2], "col": col2,
                                      "kind": "point", "key": lit2})
                    elif mirrored in ("<", "<=", ">", ">="):
                        found.append({"index": idx_map[col2], "col": col2,
                                      "kind": "range", "op": mirrored, "val": lit2})
                # descend into both sides for AND-chains
                walk(left)
                walk(right)
            elif isinstance(expr, P.Between):
                col = _as_col_ref(expr.expr, table)
                lo = _as_literal(expr.low)
                hi = _as_literal(expr.high)
                if col and lo is not None and hi is not None and col in idx_map:
                    found.append({"index": idx_map[col], "col": col,
                                  "kind": "range_between", "lo": lo, "hi": hi})

        walk(where)
        return found[0] if found else None


def _driving_table(from_: Any) -> Optional[str]:
    """Return the name of the left-most base table in the FROM clause."""
    if isinstance(from_, P.TableRef):
        return from_.name
    if isinstance(from_, P.Join):
        return _driving_table(from_.left)
    return None


def _as_col_ref(expr: P.Expr, table: str) -> Optional[str]:
    """If ``expr`` is an unqualified or table-qualified column of ``table``,
    return the column name; else None."""
    if isinstance(expr, P.ColumnRef):
        if expr.table is None or expr.table == table:
            return expr.name
    return None


def _as_literal(expr: P.Expr) -> Any:
    if isinstance(expr, P.Literal):
        return expr.value
    return None


def _mirror_op(op: str) -> str:
    """Mirror a comparison operator for ``literal OP col`` rewrites."""
    return {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "=": "=",
            "!=": "!=", "<>": "<>"}.get(op, op)
