"""End-to-end tests through the public Connection API.

These cover the happy paths and a few common constraints. They intentionally
do not exercise every branch — the executor's aggregate edge cases, HAVING,
and several DDL paths are left for the reader to cover.
"""

import pytest

from sqlitedb import connect
from sqlitedb.errors import IntegrityError


@pytest.fixture
def db():
    con = connect(":memory:")
    con.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)")
    con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, uid INTEGER, "
                "amount REAL)")
    con.execute("INSERT INTO users (id, name, age) VALUES "
                "(1, 'Ada', 36), (2, 'Linus', 55), (3, 'Grace', 40)")
    con.execute("INSERT INTO orders (uid, amount) VALUES "
                "(1, 9.9), (1, 20.0), (2, 5.0), (3, 100.0)")
    return con


def test_select_filter_order(db):
    cur = db.execute("SELECT name, age FROM users WHERE age > 38 "
                     "ORDER BY age DESC")
    assert cur.fetchall() == [("Linus", 55), ("Grace", 40)]


def test_select_star(db):
    cur = db.execute("SELECT * FROM users ORDER BY id")
    assert cur.fetchall()[0] == (1, "Ada", 36)


def test_aggregates(db):
    assert db.execute("SELECT COUNT(*) FROM users").fetchone() == (3,)
    assert db.execute("SELECT SUM(age) FROM users").fetchone() == (131,)


def test_group_by(db):
    rows = db.execute("SELECT uid, SUM(amount) FROM orders "
                      "GROUP BY uid ORDER BY uid").fetchall()
    assert rows == [(1, 29.9), (2, 5.0), (3, 100.0)]


def test_join(db):
    rows = db.execute(
        "SELECT users.name, orders.amount FROM users "
        "JOIN orders ON users.id = orders.uid ORDER BY orders.amount DESC"
    ).fetchall()
    assert rows[0] == ("Grace", 100.0)
    assert len(rows) == 4


def test_index_point_lookup(db):
    db.execute("CREATE INDEX idx_age ON users(age)")
    assert db.execute("SELECT name FROM users WHERE age = 40").fetchall() \
        == [("Grace",)]


def test_primary_key_unique(db):
    with pytest.raises(IntegrityError):
        db.execute("INSERT INTO users (id, name) VALUES (1, 'Dup')")


def test_update_and_delete(db):
    db.execute("UPDATE users SET age = 99 WHERE name = 'Ada'")
    assert db.execute("SELECT age FROM users WHERE name='Ada'").fetchone() \
        == (99,)
    db.execute("DELETE FROM users WHERE name = 'Linus'")
    assert db.execute("SELECT COUNT(*) FROM users").fetchone() == (2,)


def test_transaction_rollback(db):
    db.execute("BEGIN")
    db.execute("INSERT INTO users (name, age) VALUES ('Z', 1)")
    db.rollback()
    assert db.execute("SELECT COUNT(*) FROM users").fetchone() == (3,)


def test_like(db):
    rows = db.execute("SELECT name FROM users WHERE name LIKE 'A%'").fetchall()
    assert rows == [("Ada",)]
