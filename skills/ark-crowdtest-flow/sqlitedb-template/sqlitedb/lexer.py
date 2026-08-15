"""SQL lexer.

Turns a string of SQL into a flat list of :class:`Token` objects. The lexer
is deliberately hand-written (no regex library for the core path) so it is
easy to read and step through.

Supported tokens
----------------
* keywords (case-insensitive): SELECT, FROM, WHERE, ...
* identifiers: ``foo``, ``"quoted name"``, ``t.col``
* integer and floating-point literals
* string literals with single quotes and doubled-quote escaping (``''``)
* operators: ``= != <> < <= > >= + - * / % ( ) , . ;``
* line comments ``-- ...`` and block comments ``/* ... */``

Notes
-----
* Whitespace is insignificant except to separate tokens.
* Unknown characters raise :class:`~sqlitedb.errors.SyntaxError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .errors import SyntaxError


# Keywords are matched case-insensitively. Keep this set in sync with the
# parser — anything here is reserved and cannot be used as a bare identifier.
KEYWORDS = {
    "SELECT", "FROM", "WHERE", "INSERT", "INTO", "VALUES", "UPDATE", "SET",
    "DELETE", "CREATE", "TABLE", "INDEX", "ON", "DROP", "AND", "OR", "NOT",
    "NULL", "PRIMARY", "KEY", "UNIQUE", "INTEGER", "TEXT", "REAL", "BLOB",
    "ORDER", "BY", "GROUP", "HAVING", "ASC", "DESC", "LIMIT", "OFFSET",
    "JOIN", "INNER", "LEFT", "RIGHT", "OUTER", "AS", "DISTINCT", "IS",
    "BEGIN", "COMMIT", "ROLLBACK", "TRANSACTION", "AND", "OR", "BETWEEN",
    "LIKE", "IN", "EXISTS", "TRUE", "FALSE", "DEFAULT", "AUTOINCREMENT",
    "CHECK", "REFERENCES", "FOREIGN", "COUNT", "SUM", "AVG", "MIN", "MAX",
}

# Single-character operators map directly to their token text.
# Note: '<' and '>' are also single ops but their two-char forms ('<=','>=',
# '<>') are resolved in _read_operator, so they must appear here too.
_SINGLE_OPS = set("=+-*/%(),.;<>")


@dataclass(frozen=True)
class Token:
    """A single lexical token.

    ``kind`` is one of: ``keyword``, ``ident``, ``int``, ``float``, ``string``,
    ``op``, ``eof``.
    """

    kind: str
    text: str
    line: int
    col: int

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"Token({self.kind!r}, {self.text!r}, line={self.line})"


class Lexer:
    """A pull-style lexer.

    ``Lexer(sql).tokens()`` returns the full token list with a trailing
    ``eof`` token. Whitespace and comments are skipped.
    """

    def __init__(self, source: str) -> None:
        self.src = source
        self.pos = 0
        self.line = 1
        self.col = 1

    # -- low-level helpers -------------------------------------------------

    def _peek(self, offset: int = 0) -> str:
        idx = self.pos + offset
        if idx >= len(self.src):
            return ""
        return self.src[idx]

    def _advance(self) -> str:
        if self.pos >= len(self.src):
            return ""
        ch = self.src[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def _error(self, msg: str) -> "SyntaxError":
        return SyntaxError(f"lex error at line {self.line}, col {self.col}: {msg}")

    # -- main loop ---------------------------------------------------------

    def tokens(self) -> List[Token]:
        toks: List[Token] = []
        while True:
            self._skip_trivia()
            if self.pos >= len(self.src):
                toks.append(Token("eof", "", self.line, self.col))
                return toks
            ch = self._peek()
            if ch.isalpha() or ch == "_":
                toks.append(self._read_word())
            elif ch.isdigit():
                toks.append(self._read_number())
            elif ch == "'":
                toks.append(self._read_string())
            elif ch == '"':
                toks.append(self._read_quoted_ident())
            elif ch in _SINGLE_OPS or ch == "!":
                toks.append(self._read_operator())
            else:
                raise self._error(f"unexpected character {ch!r}")

    # -- trivia: whitespace + comments ------------------------------------

    def _skip_trivia(self) -> None:
        while self.pos < len(self.src):
            ch = self._peek()
            if ch in " \t\r\n":
                self._advance()
            elif ch == "-" and self._peek(1) == "-":
                # line comment
                while self.pos < len(self.src) and self._peek() != "\n":
                    self._advance()
            elif ch == "/" and self._peek(1) == "*":
                # block comment — note: a missing close is silently tolerated
                # here on purpose to match common permissive lexers, but we
                # still must consume the body.
                self._advance()
                self._advance()
                while self.pos < len(self.src):
                    if self._peek() == "*" and self._peek(1) == "/":
                        self._advance()
                        self._advance()
                        break
                    self._advance()
            else:
                break

    # -- readers -----------------------------------------------------------

    def _read_word(self) -> Token:
        line, col = self.line, self.col
        start = self.pos
        while self.pos < len(self.src) and (self._peek().isalnum() or self._peek() == "_"):
            self._advance()
        word = self.src[start:self.pos]
        upper = word.upper()
        if upper in KEYWORDS:
            return Token("keyword", upper, line, col)
        return Token("ident", word, line, col)

    def _read_number(self) -> Token:
        line, col = self.line, self.col
        start = self.pos
        is_float = False
        while self.pos < len(self.src) and self._peek().isdigit():
            self._advance()
        if self._peek() == ".":
            # peek ahead: a digit must follow the dot to be a real literal,
            # otherwise the dot is the start of a qualified-name operator.
            if self._peek(1).isdigit():
                is_float = True
                self._advance()  # consume '.'
                while self.pos < len(self.src) and self._peek().isdigit():
                    self._advance()
        if self._peek() in "eE" and self._peek():
            is_float = True
            self._advance()
            if self._peek() in "+-":
                self._advance()
            while self.pos < len(self.src) and self._peek().isdigit():
                self._advance()
        text = self.src[start:self.pos]
        if is_float:
            return Token("float", text, line, col)
        return Token("int", text, line, col)

    def _read_string(self) -> Token:
        line, col = self.line, self.col
        self._advance()  # opening quote
        buf: List[str] = []
        while True:
            if self.pos >= len(self.src):
                raise self._error("unterminated string literal")
            ch = self._advance()
            if ch == "'":
                # doubled single-quote is an escaped single quote inside the
                # string, per the SQL standard.
                if self._peek() == "'":
                    buf.append("'")
                    self._advance()
                    continue
                break
            buf.append(ch)
        return Token("string", "".join(buf), line, col)

    def _read_quoted_ident(self) -> Token:
        line, col = self.line, self.col
        self._advance()  # opening quote
        start = self.pos
        while self.pos < len(self.src) and self._peek() != '"':
            self._advance()
        if self.pos >= len(self.src):
            raise self._error("unterminated quoted identifier")
        name = self.src[start:self.pos]
        self._advance()  # closing quote
        return Token("ident", name, line, col)

    def _read_operator(self) -> Token:
        line, col = self.line, self.col
        ch = self._advance()
        # two-character operators
        two = ch + self._peek()
        if two in ("!=", "<>", "<=", ">=", "||"):
            self._advance()
            return Token("op", two, line, col)
        if ch == "!":
            raise self._error("'!' is only valid as part of '!='")
        return Token("op", ch, line, col)


def tokenize(source: str) -> List[Token]:
    """Convenience: tokenize a SQL string into a list of tokens (with eof)."""
    return Lexer(source).tokens()
