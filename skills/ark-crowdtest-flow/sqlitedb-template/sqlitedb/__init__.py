"""
sqlitedb — a minimal in-memory relational database.

A small teaching-grade SQL engine: lexer → parser → planner → executor,
backed by a page-oriented storage layer with B-tree indexes and
WAL-style transactions. Pure Python, no third-party dependencies.

Public API
----------
>>> from sqlitedb import connect
>>> db = connect(":memory:")
>>> db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)")
>>> db.execute("INSERT INTO users (id, name, age) VALUES (1, 'Ada', 36)")
>>> db.execute("SELECT name FROM users WHERE age > 30").fetchall()
[('Ada',)]
"""

from .api import connect, Connection  # noqa: F401
from .errors import (  # noqa: F401
    DatabaseError,
    SyntaxError as SqlSyntaxError,
    IntegrityError,
)

__version__ = "0.3.1"
__all__ = [
    "connect",
    "Connection",
    "DatabaseError",
    "SqlSyntaxError",
    "IntegrityError",
]
