"""Storage / serialization unit tests."""

from sqlitedb.storage import (ColumnDef, Schema, TableDef, coerce_value,
                              deserialize_row, serialize_row)


def test_serialize_roundtrip_primitives():
    row = [1, "hello", 3.14, None, True, b"\x00\x01\x02"]
    assert deserialize_row(serialize_row(row)) == row


def test_serialize_float_precision():
    row = [1.0 / 3.0]
    assert deserialize_row(serialize_row(row)) == [1.0 / 3.0]


def test_schema_add_get_drop():
    s = Schema()
    t = TableDef("users", [ColumnDef("id", "INTEGER", primary_key=True),
                           ColumnDef("name", "TEXT")])
    s.add(t)
    assert s.get("users").name == "users"
    s.drop("users")
    try:
        s.get("users")
        assert False, "expected error"
    except Exception:
        pass


def test_schema_json_roundtrip():
    s = Schema()
    s.add(TableDef("t", [ColumnDef("id", "INTEGER", primary_key=True),
                         ColumnDef("v", "REAL")]))
    s2 = Schema.from_json(s.to_json())
    assert s2.get("t").columns[0].primary_key is True
    assert s2.get("t").columns[1].type == "REAL"


def test_coerce_value_integer_affinity():
    c = ColumnDef("x", "INTEGER")
    assert coerce_value("42", c) == 42
    assert coerce_value(3.0, c) == 3
    assert coerce_value(None, c) is None


def test_coerce_value_text():
    c = ColumnDef("x", "TEXT")
    assert coerce_value(123, c) == "123"
