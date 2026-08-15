# Persistence (stubbed)

As of 0.3.1 the engine is in-memory only. `connect(name)` accepts any name
but every connection starts with an empty database, identical to
`:memory:`.

The pieces that *would* make persistence work are mostly present:

* `serialize_row` / `deserialize_row` give a compact, stable on-disk row
  format.
* `Schema.to_json` / `Schema.from_json` round-trip the catalog.
* `WAL.applied` is an ordered list of physical operations with enough
  information to replay or undo.

What is missing:

1. A page file: pages are `list[list[values]]` in memory; there is no
   `Pager` that reads/writes fixed-size blocks to a file.
2. A bootstrap step that, on `connect(path)`, reads page 0 (the schema),
   materialises the catalog, then replays the WAL to recover committed
   work.
3. A checkpoint step that flushes dirty pages and truncates the WAL.

A reasonable plan to add persistence:

1. Introduce a `Pager` that owns an `os.File` and maps page indices to
   `PAGE_SIZE`-byte slots.
2. Serialise each table's pages into its own page range; store the schema
   (as JSON) in page 0 and a free-list / page-map in page 1.
3. On open: read page 0 → catalog; replay `WAL.applied` to rebuild live
   tables.
4. On commit: fsync dirty pages, then append a commit marker to the WAL.

This file is intentionally a sketch — it is the intended next milestone for
the project, not a finished design.
