# sqlitedb

A minimal, pure-Python in-memory relational database with a real SQL engine:
a hand-written lexer → recursive-descent parser → query planner → executor,
backed by a page-oriented storage layer with B-tree indexes and WAL-style
transactions. No third-party dependencies — only the standard library.

> Status: **0.3.1 — early / rough edges**. This is a teaching-grade engine
> shipped on purpose with a thinned-out test suite (~75 % coverage) and a
> handful of known rough edges. See `docs/known-issues.md`.

## Architecture

```
            SQL text
              │
              ▼
         ┌─────────┐   tokens    ┌──────────┐   AST    ┌──────────┐
         │ lexer.py│ ──────────▶ │ parser.py│────────▶ │planner.py│
         └─────────┘             └──────────┘          └─────┬────┘
                                                        plan │
              ┌─────────────────────────────────────────────┘
              ▼
         ┌───────────┐  reads/writes  ┌───────────┐
         │executor.py│ ◀────────────▶ │ storage.py│  (pages, schema, rows)
         └─────┬─────┘                └───────────┘
               │ uses                  ▲
               ▼                       │ maintains
         ┌───────────┐            ┌─────────┐
         │  index.py │ ◀───────── │ api.py  │  (Engine, Connection, Cursor)
         │  (B-tree) │            └────┬────┘
         └───────────┘                 │ records
                                   ┌───┴──────────┐
                                   │transaction.py│ (WAL)
                                   └──────────────┘
```

## Quick start

```bash
pip install -e ".[dev]"
pytest                       # run the tests
python -m sqlitedb           # interactive REPL
```

```python
from sqlitedb import connect

db = connect(":memory:")
db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)")
db.execute("INSERT INTO users (name, age) VALUES ('Ada', 36), ('Linus', 55)")
db.execute("CREATE INDEX idx_age ON users(age)")

cur = db.execute("SELECT name, age FROM users WHERE age > 30 ORDER BY age DESC")
print(cur.columns)      # ['name', 'age']
print(cur.fetchall())   # [('Linus', 55), ('Ada', 36)]

# joins, grouping, aggregates
db.execute("SELECT u.name, COUNT(*) FROM users u "
           "JOIN orders o ON u.id = o.uid GROUP BY u.name")
```

## Supported SQL subset

* **DDL**: `CREATE TABLE` (with `INTEGER PRIMARY KEY`, `NOT NULL`, `UNIQUE`,
  `DEFAULT`), `CREATE INDEX`, `DROP TABLE [IF EXISTS]`
* **DML**: `INSERT [OR REPLACE] INTO ... VALUES (...), (...)`, `UPDATE ... SET`,
  `DELETE FROM ... [WHERE ...]`
* **Queries**: `SELECT [DISTINCT]`, `*` / `t.*`, column aliases (`AS` or
  implicit), `WHERE`, `INNER` / `LEFT` / `RIGHT JOIN ... ON`, `GROUP BY`,
  `ORDER BY ... [ASC|DESC]`, `LIMIT [OFFSET]`
* **Expressions**: literals, qualified column refs (`t.col`), `+ - * / % ||`,
  comparisons, `AND` / `OR` / `NOT`, `IS [NOT] NULL`, `BETWEEN`, `IN (...)`,
  `LIKE`
* **Aggregates**: `COUNT`, `SUM`, `AVG`, `MIN`, `MAX` (with optional `DISTINCT`)
* **Scalar functions**: `ABS`, `LOWER`, `UPPER`, `LENGTH`, `ROUND`
* **Transactions**: `BEGIN` / `COMMIT` / `ROLLBACK` (WAL-backed undo)

## Project layout

```
sqlitedb/
  lexer.py        tokeniser
  parser.py       AST + recursive-descent parser
  planner.py      index-aware query planning
  storage.py      pages, schema catalog, row (de)serialisation
  index.py        B-tree index
  executor.py     statement execution + expression evaluation
  transaction.py  write-ahead log / transaction manager
  api.py          Engine, Connection, Cursor
  errors.py       exception hierarchy
  cli.py          interactive REPL
tests/
  test_lexer.py / test_parser.py / test_storage.py
  test_index.py / test_api.py
docs/
  known-issues.md   the rough edges (start here when extending the tests)
  persistence.md    notes on the (stubbed) on-disk backend
samples/
  demo.sql          a runnable demo script
```

## Extending the project

The test suite is intentionally incomplete. Good places to add coverage:

* aggregate edge cases (empty groups, `DISTINCT` interaction, `NULL` handling)
* the `HAVING` clause (currently unsupported)
* `CREATE UNIQUE INDEX` (currently broken at parse time — see known-issues)
* multi-key / composite indexes
* persistence (the on-disk backend is stubbed)
* type-affinity edge cases in `coerce_value`

Run `pytest --cov=sqlitedb --cov-report=term-missing` to see the gaps.

## License

MIT
