"""Parser tests: AST shape for the main statement types."""

import pytest

from sqlitedb import parser as P
from sqlitedb.parser import parse_one


def test_create_table_basic():
    stmt = parse_one("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    assert isinstance(stmt, P.CreateTable)
    assert stmt.table == "t"
    assert stmt.columns[0].primary_key is True
    assert stmt.columns[1].not_null is True


def test_insert_multi_row():
    stmt = parse_one("INSERT INTO t (a, b) VALUES (1, 2), (3, 4)")
    assert isinstance(stmt, P.Insert)
    assert stmt.columns == ["a", "b"]
    assert len(stmt.values) == 2


def test_select_with_where_and_order():
    stmt = parse_one("SELECT a, b FROM t WHERE a > 5 ORDER BY b DESC LIMIT 10")
    assert isinstance(stmt, P.Select)
    assert stmt.where is not None
    assert stmt.order_by[0].desc is True
    assert stmt.limit == 10


def test_select_join():
    stmt = parse_one(
        "SELECT u.name FROM users u JOIN orders o ON u.id = o.uid")
    assert isinstance(stmt, P.Select)
    assert isinstance(stmt.from_, P.Join)
    assert stmt.from_.kind == "INNER"


def test_update_and_delete():
    upd = parse_one("UPDATE t SET a = 1 WHERE b = 2")
    assert isinstance(upd, P.Update)
    assert upd.assignments[0][0] == "a"
    dele = parse_one("DELETE FROM t WHERE x = 1")
    assert isinstance(dele, P.Delete)


def test_aggregate_func_call():
    stmt = parse_one("SELECT COUNT(*), SUM(amount) FROM orders")
    items = stmt.items
    assert items[0].expr.name == "COUNT"
    assert items[1].expr.name == "SUM"


def test_parse_syntax_error():
    with pytest.raises(Exception):
        parse_one("SELECT FROM")
