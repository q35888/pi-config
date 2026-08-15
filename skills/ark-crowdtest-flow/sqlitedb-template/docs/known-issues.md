# Known issues & rough edges

This is a list of things that are known to be incomplete or wrong as of
0.3.1. They are **intentionally left in** so that anyone picking the project
up has a concrete starting point for hardening work. Some are genuine bugs,
some are missing features, some are semantic deviations from standard SQL.

> If you are adding tests, treat this list as a backlog — each item should
> be reachable by a failing test you can write first, then watch go green.

## Bugs (incorrect behaviour)

1. **`CREATE UNIQUE INDEX` fails to parse.**
   `CREATE UNIQUE INDEX idx ON t(col)` raises a syntax error at `UNIQUE`,
   because the parser does not consume `UNIQUE` before dispatching to the
   index path. As a result the `unique` flag on `CreateIndex` is effectively
   always `False`, so *no* index is ever treated as unique by the engine.
   UNIQUE *columns* declared inline on a table still work (they get an
   implicit unique index), but the explicit `CREATE UNIQUE INDEX` form does
   not.

2. **`_iter_tables` misuse of `yield from`.**
   In `api.py`, `_iter_tables` is written as a generator that does
   `yield from _iter_tables(child)`. Because the function is itself a
   generator, this yields a nested generator object instead of flattening
   the table names. `SELECT *` over a join happens to still return rows
   today, but column-name expansion for `*` is fragile and wrong for
   multi-table queries.

3. **Aggregate of an empty set.**
   `SUM` over zero rows returns `0`; standard SQL returns `NULL`. `AVG` /
   `MIN` / `MAX` already return `NULL`. Pick one semantics and be
   consistent.

4. **Division affinity.**
   `a / b` where both operands are integers uses floor division
   (`5 / 2 → 2`). Most engines return `2.5` for `/`; SQLite returns `2`
   only with explicit `CAST`. Decide and document.

## Missing features

5. **`HAVING`.** A `HAVING` clause currently either raises or is ignored.
   Aggregates can be filtered via `WHERE` only, which is incorrect for
   post-aggregation predicates.

6. **`SELECT` without `FROM`.** `SELECT 1 + 1` is rejected; a dummy single-row
   source is needed. Common in real queries (`SELECT 1`, `SELECT NOW()`, …).

7. **`ORDER BY` on a SELECT alias.** `SELECT a + b AS s ... ORDER BY s`
   resolves `s` as a column rather than the alias.

8. **Composite indexes.** Only single-column indexes are supported; the
   parser silently ignores all but the first column in
   `CREATE INDEX ... (c1, c2)`.

## Robustness gaps

9. **`_apply_params` is unsafe.** `?` substitution stringifies values without
   real escaping and only supports positional placeholders.

10. **Comparison of incompatible types.** `_compare` falls back to comparing
    the string forms when `<`/`>` raises `TypeError`. This means
    `'10' < '9'` style surprises can occur across TEXT/INTEGER columns.

11. **No persistence.** `connect("file.db")` is accepted but behaves like
    `:memory:`. The WAL records exist but are never replayed on reopen. See
    `persistence.md`.

12. **Nested `BEGIN`.** Silently commits-and-reopens rather than erroring.

## Test-suite gaps

Coverage is ~76 %. Conspicuously untested:

* the `executor` aggregate / join edge branches (lines noted by
  `--cov-report=term-missing`)
* `transaction.WAL` commit/rollback paths beyond the happy path
* most `cli` paths
* planner index selection for mirrored predicates (`literal OP col`)
