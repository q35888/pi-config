"""A tiny REPL so the package can be used from the command line.

Run ``python -m sqlitedb`` or the installed ``sqlitedb`` script to drop into
an interactive prompt. Useful for manual smoke-testing.
"""

from __future__ import annotations

import sys
from typing import List

from .api import connect
from .errors import DatabaseError


HELP = """\
sqlitedb — commands end with ';'. Special commands:
  .tables            list tables
  .schema [table]    show table definitions
  .exit | .quit      leave the REPL
"""


def main(argv: List[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    db_name = ":memory:"
    if argv:
        db_name = argv[0]
    con = connect(db_name)
    print(f"sqlitedb connected to {db_name}. Type .help for help.")
    buf: List[str] = []
    while True:
        try:
            line = input("sqlitedb> " if not buf else "   ... > ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        stripped = line.strip()
        if not buf and stripped.startswith("."):
            cmd = stripped.lower()
            if cmd in (".exit", ".quit"):
                break
            if cmd == ".help":
                print(HELP)
                continue
            if cmd == ".tables":
                print("  ".join(con.table_names()) or "(no tables)")
                continue
            if cmd.startswith(".schema"):
                parts = stripped.split(maxsplit=1)
                name = parts[1] if len(parts) > 1 else None
                for tname, tdef in con.tables.items():
                    if name and tname != name:
                        continue
                    cols = ", ".join(
                        f"{c.name} {c.type}" + (" PRIMARY KEY" if c.primary_key else "")
                        for c in tdef.columns)
                    print(f"CREATE TABLE {tname} ({cols});")
                continue
            print(f"unknown command: {stripped}")
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            sql = "\n".join(buf).strip()
            buf = []
            try:
                cur = con.execute(sql)
                if cur.columns:
                    print(" | ".join(cur.columns))
                    print("-+-".join("-" * len(c) for c in cur.columns))
                    for row in cur.fetchall():
                        print(" | ".join("" if v is None else str(v)
                                         for v in row))
            except DatabaseError as exc:
                print(f"error: {exc}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
