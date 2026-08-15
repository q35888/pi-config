"""Exception hierarchy for sqlitedb.

Mirrors the shape of the DB-API 2.0 (PEP 249) exception hierarchy so the
public surface feels familiar to Python developers, even though the engine
itself is a toy.
"""


class DatabaseError(Exception):
    """Base class for all errors raised by the engine."""


class SyntaxError(DatabaseError):
    """Raised when the lexer/parser cannot understand a statement."""


class IntegrityError(DatabaseError):
    """Raised when a constraint (PRIMARY KEY / NOT NULL / UNIQUE) is violated."""


class OperationalError(DatabaseError):
    """Raised for runtime problems (missing table, type mismatch, ...)."""


class InternalError(DatabaseError):
    """Raised when the engine reaches a state it should never reach."""
