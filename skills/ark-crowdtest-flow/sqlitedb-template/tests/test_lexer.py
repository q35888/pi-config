"""Smoke tests for the lexer.

Covers the common path: identifiers, numbers, strings, operators, keywords,
comment skipping. Deliberately light on edge cases — see the project README
for the known-gaps list.
"""

import pytest

from sqlitedb.lexer import Lexer, tokenize


def test_simple_select_tokens():
    toks = tokenize("SELECT id FROM users")
    kinds = [t.kind for t in toks if t.kind != "eof"]
    assert kinds == ["keyword", "ident", "keyword", "ident"]
    assert toks[0].text == "SELECT"
    assert toks[1].text == "id"


def test_integer_and_float():
    toks = tokenize("SELECT 42, 3.14, 1e9")
    nums = [t for t in toks if t.kind in ("int", "float")]
    assert nums[0].kind == "int" and nums[0].text == "42"
    assert nums[1].kind == "float" and nums[1].text == "3.14"
    assert nums[2].kind == "float" and nums[2].text == "1e9"


def test_string_with_escaped_quote():
    toks = tokenize("SELECT 'it''s ok'")
    s = [t for t in toks if t.kind == "string"][0]
    assert s.text == "it's ok"


def test_operators():
    toks = tokenize("a >= 10 AND b != 20")
    ops = [t.text for t in toks if t.kind == "op"]
    assert ">=" in ops and "!=" in ops


def test_line_comment_skipped():
    toks = tokenize("SELECT 1 -- this is a comment\n, 2")
    ints = [t for t in toks if t.kind == "int"]
    assert [t.text for t in ints] == ["1", "2"]


def test_unterminated_string_raises():
    with pytest.raises(Exception):
        tokenize("SELECT 'oops")


def test_keywords_case_insensitive():
    toks = tokenize("select from WHERE")
    assert all(t.kind == "keyword" for t in toks if t.kind != "eof")
