"""SQL parser.

Recursive-descent parser that consumes the token stream from
:mod:`sqlitedb.lexer` and produces an Abstract Syntax Tree.

Grammar (informal)
------------------
::

    statement ::=
        select_stmt | insert_stmt | update_stmt | delete_stmt
      | create_table_stmt | create_index_stmt | drop_table_stmt
      | begin_stmt | commit_stmt | rollback_stmt

    select_stmt ::= SELECT [DISTINCT] select_list
                    FROM table_ref ( [join] ... )
                    [WHERE expr]
                    [GROUP BY expr_list [HAVING expr]]
                    [ORDER BY order_item_list]
                    [LIMIT int [OFFSET int]]

The AST nodes are plain dataclasses; the executor walks them directly.
Expression nodes form a small tree supporting literals, column refs,
binary ops, unary ops, function calls, BETWEEN/LIKE/IN and IS NULL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union

from .errors import SyntaxError
from .lexer import KEYWORDS, Lexer, Token


# --------------------------------------------------------------------------
# Expression AST
# --------------------------------------------------------------------------

@dataclass
class Literal:
    """A constant value. ``value`` is already typed (int/float/str/None/bool)."""
    value: object


@dataclass
class ColumnRef:
    """A column reference, optionally qualified by table name (``t.col``)."""
    table: Optional[str]
    name: str


@dataclass
class Star:
    """``*`` or ``t.*`` — used in select lists and COUNT(*)."""
    table: Optional[str] = None


@dataclass
class BinaryOp:
    op: str
    left: "Expr"
    right: "Expr"


@dataclass
class UnaryOp:
    op: str
    operand: "Expr"


@dataclass
class FuncCall:
    """A function call such as ``COUNT(*)`` or ``LOWER(name)``."""
    name: str
    args: List["Expr"]
    distinct: bool = False


@dataclass
class Between:
    """``expr BETWEEN lo AND hi``."""
    expr: "Expr"
    low: "Expr"
    high: "Expr"
    negated: bool = False


@dataclass
class InList:
    """``expr IN (a, b, c)``."""
    expr: "Expr"
    items: List["Expr"]
    negated: bool = False


@dataclass
class IsNull:
    """``expr IS NULL`` / ``expr IS NOT NULL``."""
    expr: "Expr"
    negated: bool = False


@dataclass
class Like:
    """``expr LIKE pattern``. Implemented with Python-style glob→regex."""
    expr: "Expr"
    pattern: "Expr"
    negated: bool = False


Expr = Union[Literal, ColumnRef, Star, BinaryOp, UnaryOp, FuncCall, Between,
             InList, IsNull, Like]


# --------------------------------------------------------------------------
# Statement AST
# --------------------------------------------------------------------------

@dataclass
class ColumnDef:
    name: str
    type: str          # "INTEGER" | "TEXT" | "REAL" | "BLOB"
    primary_key: bool = False
    not_null: bool = False
    unique: bool = False
    default: Optional[Expr] = None


@dataclass
class CreateTable:
    table: str
    columns: List[ColumnDef]
    if_not_exists: bool = False


@dataclass
class CreateIndex:
    index: str
    table: str
    columns: List[str]
    unique: bool = False


@dataclass
class DropTable:
    table: str
    if_exists: bool = False


@dataclass
class Insert:
    table: str
    columns: Optional[List[str]]   # None ⇒ all columns in declared order
    values: List[List[Expr]]       # multi-row INSERT supported
    or_replace: bool = False


@dataclass
class Update:
    table: str
    assignments: List[Tuple[str, Expr]]
    where: Optional[Expr] = None


@dataclass
class Delete:
    table: str
    where: Optional[Expr] = None


@dataclass
class TableRef:
    name: str
    alias: Optional[str] = None


@dataclass
class Join:
    left: "TableRef"
    right: TableRef
    on: Optional[Expr]
    kind: str = "INNER"            # INNER | LEFT | RIGHT


@dataclass
class OrderItem:
    expr: Expr
    desc: bool = False


@dataclass
class SelectItem:
    expr: Expr
    alias: Optional[str] = None


@dataclass
class Select:
    items: List[SelectItem]
    from_: Union[TableRef, Join]
    where: Optional[Expr] = None
    group_by: List[Expr] = field(default_factory=list)
    having: Optional[Expr] = None
    order_by: List[OrderItem] = field(default_factory=list)
    limit: Optional[int] = None
    offset: Optional[int] = None
    distinct: bool = False


@dataclass
class Begin:
    pass


@dataclass
class Commit:
    pass


@dataclass
class Rollback:
    pass


Statement = Union[CreateTable, CreateIndex, DropTable, Insert, Update, Delete,
                  Select, Begin, Commit, Rollback]


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

# Binary operator precedence, higher binds tighter. ``OR`` is weakest.
_PRECEDENCE = {
    "OR": 1,
    "AND": 2,
    "NOT": 3,
    "=": 4, "!=": 4, "<>": 4, "<": 4, "<=": 4, ">": 4, ">=": 4,
    "LIKE": 4, "IN": 4, "IS": 4, "BETWEEN": 4,
    "+": 5, "-": 5,
    "*": 6, "/": 6, "%": 6,
    "||": 7,
}

_COMPARISONS = {"=", "!=", "<>", "<", "<=", ">", ">="}
_AGG_FUNCS = {"COUNT", "SUM", "AVG", "MIN", "MAX"}


class Parser:
    """Hand-written recursive-descent parser."""

    def __init__(self, tokens: List[Token]) -> None:
        self.toks = tokens
        self.i = 0

    # -- token cursor helpers ---------------------------------------------

    @property
    def cur(self) -> Token:
        return self.toks[self.i]

    def _peek(self, offset: int = 0) -> Token:
        idx = self.i + offset
        if idx >= len(self.toks):
            return self.toks[-1]
        return self.toks[idx]

    def _advance(self) -> Token:
        tok = self.toks[self.i]
        if tok.kind != "eof":
            self.i += 1
        return tok

    def _err(self, msg: str) -> SyntaxError:
        return SyntaxError(f"parse error at line {self.cur.line}: {msg} "
                           f"(near {self.cur.text!r})")

    def _expect_kw(self, kw: str) -> Token:
        if self.cur.kind == "keyword" and self.cur.text == kw:
            return self._advance()
        raise self._err(f"expected keyword {kw}")

    def _match_kw(self, *kws: str) -> bool:
        if self.cur.kind == "keyword" and self.cur.text in kws:
            self._advance()
            return True
        return False

    def _is_kw(self, *kws: str) -> bool:
        return self.cur.kind == "keyword" and self.cur.text in kws

    def _expect_op(self, op: str) -> Token:
        if self.cur.kind == "op" and self.cur.text == op:
            return self._advance()
        raise self._err(f"expected {op!r}")

    def _match_op(self, *ops: str) -> bool:
        if self.cur.kind == "op" and self.cur.text in ops:
            self._advance()
            return True
        return False

    def _is_op(self, *ops: str) -> bool:
        return self.cur.kind == "op" and self.cur.text in ops

    def _expect_ident(self) -> str:
        if self.cur.kind == "ident":
            return self._advance().text
        raise self._err("expected identifier")

    # -- entry point ------------------------------------------------------

    def parse(self) -> List[Statement]:
        stmts: List[Statement] = []
        while self.cur.kind != "eof":
            stmts.append(self._parse_statement())
            # tolerate optional trailing ';'
            while self._match_op(";"):
                pass
        return stmts

    def parse_one(self) -> Statement:
        stmts = self.parse()
        if len(stmts) != 1:
            raise SyntaxError(f"expected exactly one statement, got {len(stmts)}")
        return stmts[0]

    def _parse_statement(self) -> Statement:
        if not self.cur.kind == "keyword":
            raise self._err("expected a statement keyword")
        kw = self.cur.text
        if kw == "SELECT":
            return self._parse_select()
        if kw == "INSERT":
            return self._parse_insert()
        if kw == "UPDATE":
            return self._parse_update()
        if kw == "DELETE":
            return self._parse_delete()
        if kw == "CREATE":
            return self._parse_create()
        if kw == "DROP":
            return self._parse_drop()
        if kw == "BEGIN":
            self._advance(); self._match_kw("TRANSACTION"); return Begin()
        if kw == "COMMIT":
            self._advance(); self._match_kw("TRANSACTION"); return Commit()
        if kw == "ROLLBACK":
            self._advance(); self._match_kw("TRANSACTION"); return Rollback()
        raise self._err(f"unexpected statement keyword {kw}")

    # -- CREATE / DROP ----------------------------------------------------

    def _parse_create(self) -> Statement:
        self._advance()  # CREATE
        if self._match_kw("TABLE"):
            return self._parse_create_table()
        if self._match_kw("INDEX"):
            return self._parse_create_index()
        raise self._err("expected TABLE or INDEX after CREATE")

    def _parse_create_table(self) -> CreateTable:
        if_not_exists = False
        if self._match_kw("IF"):
            self._expect_kw("NOT")
            self._expect_kw("EXISTS")
            if_not_exists = True
        table = self._expect_ident()
        self._expect_op("(")
        columns: List[ColumnDef] = []
        while True:
            columns.append(self._parse_column_def())
            if self._match_op(","):
                continue
            break
        self._expect_op(")")
        return CreateTable(table, columns, if_not_exists)

    def _parse_column_def(self) -> ColumnDef:
        name = self._expect_ident()
        col_type = "TEXT"
        if self.cur.kind == "keyword" and self.cur.text in {
                "INTEGER", "TEXT", "REAL", "BLOB"}:
            col_type = self._advance().text
        elif self.cur.kind == "ident":
            col_type = self._advance().text.upper()
        primary_key = not_null = unique = False
        default: Optional[Expr] = None
        # column constraints loop
        while self.cur.kind == "keyword":
            if self._match_kw("PRIMARY"):
                self._expect_kw("KEY")
                primary_key = True
                not_null = True
            elif self._match_kw("NOT"):
                self._expect_kw("NULL")
                not_null = True
            elif self._match_kw("UNIQUE"):
                unique = True
            elif self._match_kw("DEFAULT"):
                default = self._parse_primary()
            else:
                break
        return ColumnDef(name, col_type, primary_key, not_null, unique, default)

    def _parse_create_index(self) -> CreateIndex:
        unique = bool(self._match_kw("UNIQUE"))
        # NOTE: CREATE UNIQUE INDEX consumes UNIQUE before reaching here only if
        # we had handled it; for the common path "CREATE INDEX" UNIQUE is False.
        index = self._expect_ident()
        self._expect_kw("ON")
        table = self._expect_ident()
        self._expect_op("(")
        cols = [self._expect_ident()]
        while self._match_op(","):
            cols.append(self._expect_ident())
        self._expect_op(")")
        return CreateIndex(index, table, cols, unique)

    def _parse_drop(self) -> DropTable:
        self._advance()  # DROP
        self._expect_kw("TABLE")
        if_exists = False
        if self._match_kw("IF"):
            self._expect_kw("EXISTS")
            if_exists = True
        table = self._expect_ident()
        return DropTable(table, if_exists)

    # -- INSERT / UPDATE / DELETE ----------------------------------------

    def _parse_insert(self) -> Insert:
        self._advance()  # INSERT
        or_replace = False
        if self._match_kw("OR"):
            self._expect_kw("REPLACE")
            or_replace = True
        self._expect_kw("INTO")
        table = self._expect_ident()
        columns: Optional[List[str]] = None
        if self._match_op("("):
            columns = [self._expect_ident()]
            while self._match_op(","):
                columns.append(self._expect_ident())
            self._expect_op(")")
        self._expect_kw("VALUES")
        rows: List[List[Expr]] = []
        while True:
            self._expect_op("(")
            row = [self._parse_expr()]
            while self._match_op(","):
                row.append(self._parse_expr())
            self._expect_op(")")
            rows.append(row)
            if self._match_op(","):
                continue
            break
        return Insert(table, columns, rows, or_replace)

    def _parse_update(self) -> Update:
        self._advance()  # UPDATE
        table = self._expect_ident()
        self._expect_kw("SET")
        assigns: List[Tuple[str, Expr]] = []
        while True:
            col = self._expect_ident()
            self._expect_op("=")
            assigns.append((col, self._parse_expr()))
            if self._match_op(","):
                continue
            break
        where = self._parse_optional_where()
        return Update(table, assigns, where)

    def _parse_delete(self) -> Delete:
        self._advance()  # DELETE
        self._expect_kw("FROM")
        table = self._expect_ident()
        where = self._parse_optional_where()
        return Delete(table, where)

    def _parse_optional_where(self) -> Optional[Expr]:
        if self._match_kw("WHERE"):
            return self._parse_expr()
        return None

    # -- SELECT -----------------------------------------------------------

    def _parse_select(self) -> Select:
        self._advance()  # SELECT
        distinct = self._match_kw("DISTINCT")
        items = self._parse_select_items()
        self._expect_kw("FROM")
        from_ = self._parse_from()
        where = self._parse_optional_where()
        group_by: List[Expr] = []
        having: Optional[Expr] = None
        if self._match_kw("GROUP"):
            self._expect_kw("BY")
            group_by = self._parse_expr_list()
            if self._match_kw("HAVING"):
                having = self._parse_expr()
        order_by: List[OrderItem] = []
        if self._match_kw("ORDER"):
            self._expect_kw("BY")
            order_by = self._parse_order_list()
        limit: Optional[int] = None
        offset: Optional[int] = None
        if self._match_kw("LIMIT"):
            limit = self._parse_int_literal()
            if self._match_kw("OFFSET"):
                offset = self._parse_int_literal()
        return Select(items, from_, where, group_by, having, order_by,
                      limit, offset, distinct)

    def _parse_select_items(self) -> List[SelectItem]:
        items: List[SelectItem] = []
        while True:
            if self._is_op("*"):
                self._advance()
                items.append(SelectItem(Star(None)))
            elif (self.cur.kind == "ident" and self._peek(1).kind == "op"
                  and self._peek(1).text == "." and self._peek(2).kind == "op"
                  and self._peek(2).text == "*"):
                tbl = self._expect_ident()
                self._advance()  # '.'
                self._advance()  # '*'
                items.append(SelectItem(Star(tbl)))
            else:
                expr = self._parse_expr()
                alias: Optional[str] = None
                if self._match_kw("AS"):
                    alias = self._expect_ident()
                elif self.cur.kind == "ident":
                    # implicit alias: SELECT a b  ⇒  a AS b
                    alias = self._advance().text
                items.append(SelectItem(expr, alias))
            if self._match_op(","):
                continue
            break
        return items

    def _parse_from(self) -> Union[TableRef, Join]:
        left = TableRef(self._expect_ident())
        if self._match_kw("AS"):
            left.alias = self._expect_ident()
        elif self.cur.kind == "ident":
            left.alias = self._advance().text
        # joins
        while self._is_kw("JOIN", "INNER", "LEFT", "RIGHT"):
            kind = "INNER"
            if self._match_kw("INNER"):
                self._expect_kw("JOIN")
            elif self._match_kw("LEFT"):
                self._match_kw("OUTER")
                self._expect_kw("JOIN")
                kind = "LEFT"
            elif self._match_kw("RIGHT"):
                self._match_kw("OUTER")
                self._expect_kw("JOIN")
                kind = "RIGHT"
            else:
                self._expect_kw("JOIN")
            right = TableRef(self._expect_ident())
            if self._match_kw("AS"):
                right.alias = self._expect_ident()
            elif self.cur.kind == "ident" and not self._is_kw("ON"):
                right.alias = self._advance().text
            on: Optional[Expr] = None
            if self._match_kw("ON"):
                on = self._parse_expr()
            left = Join(left, right, on, kind)  # type: ignore[arg-type]
        return left

    def _parse_order_list(self) -> List[OrderItem]:
        items: List[OrderItem] = []
        while True:
            expr = self._parse_expr()
            desc = False
            if self._match_kw("ASC"):
                desc = False
            elif self._match_kw("DESC"):
                desc = True
            items.append(OrderItem(expr, desc))
            if self._match_op(","):
                continue
            break
        return items

    def _parse_expr_list(self) -> List[Expr]:
        exprs = [self._parse_expr()]
        while self._match_op(","):
            exprs.append(self._parse_expr())
        return exprs

    def _parse_int_literal(self) -> int:
        if self.cur.kind != "int":
            raise self._err("expected an integer literal")
        return int(self._advance().text)

    # -- expression precedence climbing ----------------------------------

    def _parse_expr(self) -> Expr:
        return self._parse_binary(1)

    def _parse_binary(self, min_prec: int) -> Expr:
        left = self._parse_unary()
        while True:
            op = self._peek_op()
            if op is None:
                break
            prec = _PRECEDENCE.get(op, 0)
            if prec < min_prec:
                break
            self._advance()  # consume operator / keyword op
            if op in ("NOT",) and False:  # placeholder, NOT handled as unary
                pass
            if op == "IS":
                negated = self._match_kw("NOT")
                self._expect_kw("NULL")
                left = IsNull(left, negated)
                continue
            if op == "BETWEEN":
                low = self._parse_binary(prec + 1)
                self._expect_kw("AND")
                high = self._parse_binary(prec + 1)
                left = Between(left, low, high, False)
                continue
            if op == "IN":
                self._expect_op("(")
                items = [self._parse_expr()]
                while self._match_op(","):
                    items.append(self._parse_expr())
                self._expect_op(")")
                left = InList(left, items)
                continue
            if op == "LIKE":
                right = self._parse_binary(prec + 1)
                left = BinaryOp("LIKE", left, right)
                continue
            right = self._parse_binary(prec + 1)
            left = BinaryOp(op, left, right)
        return left

    def _peek_op(self) -> Optional[str]:
        tok = self.cur
        if tok.kind == "op" and tok.text in _PRECEDENCE:
            return tok.text
        if tok.kind == "keyword" and tok.text in {
                "AND", "OR", "LIKE", "IN", "BETWEEN", "IS"}:
            return tok.text
        return None

    def _parse_unary(self) -> Expr:
        if self._is_op("-", "+"):
            op = self._advance().text
            return UnaryOp(op, self._parse_unary())
        if self._is_kw("NOT"):
            self._advance()
            return UnaryOp("NOT", self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> Expr:
        tok = self.cur
        if tok.kind == "int":
            self._advance()
            return Literal(int(tok.text))
        if tok.kind == "float":
            self._advance()
            return Literal(float(tok.text))
        if tok.kind == "string":
            self._advance()
            return Literal(tok.text)
        if tok.kind == "keyword" and tok.text in ("NULL", "TRUE", "FALSE"):
            self._advance()
            if tok.text == "NULL":
                return Literal(None)
            return Literal(tok.text == "TRUE")
        if tok.kind == "keyword" and tok.text in _AGG_FUNCS:
            return self._parse_func_call()
        if tok.kind == "op" and tok.text == "(":
            self._advance()
            inner = self._parse_expr()
            self._expect_op(")")
            return inner
        if tok.kind == "ident":
            name = self._advance().text
            if self._is_op("."):
                self._advance()
                if self._is_op("*"):
                    self._advance()
                    return Star(name)
                col = self._expect_ident()
                return ColumnRef(name, col)
            # unqualified ident could be a column or a 0-arg func call
            if self._is_op("("):
                return self._parse_func_call_named(name)
            return ColumnRef(None, name)
        raise self._err("unexpected token in expression")

    def _parse_func_call(self) -> FuncCall:
        name = self._advance().text  # keyword agg func
        return self._parse_func_call_named(name)

    def _parse_func_call_named(self, name: str) -> FuncCall:
        self._expect_op("(")
        distinct = self._match_kw("DISTINCT")
        args: List[Expr] = []
        if self._is_op("*"):
            self._advance()
            args.append(Star(None))
        elif not self._is_op(")"):
            args.append(self._parse_expr())
            while self._match_op(","):
                args.append(self._parse_expr())
        self._expect_op(")")
        return FuncCall(name.upper(), args, distinct)


# --------------------------------------------------------------------------
# Public helpers
# --------------------------------------------------------------------------

def parse(source: str) -> List[Statement]:
    """Parse one or more ``;``-separated SQL statements into AST nodes."""
    tokens = Lexer(source).tokens()
    return Parser(tokens).parse()


def parse_one(source: str) -> Statement:
    return Parser(Lexer(source).tokens()).parse_one()
