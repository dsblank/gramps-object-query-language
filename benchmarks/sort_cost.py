#
# gramps-object-query-language - Object query language and SQL compiler for Gramps data
#
# Copyright (C) 2026      Douglas Blank
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#

"""What sorting costs, by kind of sort column.

Produces the numbers quoted in `docs/where_expr.md`'s "What sorting costs"
section. Run it rather than trusting them:

    python benchmarks/sort_cost.py [--people 20000]

Deliberately synthetic and in-memory, so it measures the *shape* of the
cost -- indexed vs. full scan vs. full scan plus a lookup per row -- not
what any particular deployment will see. A real, disk-backed Gramps tree
is slower in absolute terms; the ratios are the point.

The table it builds mirrors a real Gramps SQLite tree closely enough for
that purpose: the same flat secondary columns, the same `json_data` blob,
and the same indexes Gramps itself creates (`surname`, `given_name`,
`gramps_id` -- confirmed by reading `sqlite_master` on a freshly created
tree). Nothing indexes the *inside* of `json_data`, on a real tree or
here, which is the whole reason the numbers differ.
"""

import argparse
import json
import random
import sqlite3
import time

from gramps_object_query_language.query import (
    PERSON,
    Dialect,
    OrderBy,
    Query,
    compile_query,
)

#: Indexes a real Gramps SQLite tree has on `person`, verified against one
#: created via `CLIDbManager`. `handle` additionally gets SQLite's implicit
#: PRIMARY KEY index.
_GRAMPS_PERSON_INDEXES = ("surname", "given_name", "gramps_id")

SORT_COLUMNS = [
    ("surname", "flat column, indexed"),
    ("given_name", "flat column, indexed"),
    ("primary_name.first_name", "JSON path"),
    ("birth.date.sortval", "relationship hop"),
]


def build(people: int, seed: int = 7) -> sqlite3.Connection:
    """A person/event pair of tables shaped like a Gramps SQLite tree."""
    random.seed(seed)
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE person (handle TEXT PRIMARY KEY, gramps_id TEXT, surname TEXT, "
        "given_name TEXT, birth_ref_index INTEGER, json_data TEXT)"
    )
    conn.execute("CREATE TABLE event (handle TEXT PRIMARY KEY, json_data TEXT)")
    for column in _GRAMPS_PERSON_INDEXES:
        conn.execute(f"CREATE INDEX person_{column} ON person({column})")

    for i in range(people):
        first = f"F{random.randint(0, 99999):05d}"
        last = f"L{random.randint(0, 99999):05d}"
        conn.execute(
            "INSERT INTO person VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"p{i}",
                f"I{i}",
                last,
                first,
                0,
                json.dumps(
                    {
                        "primary_name": {"first_name": first},
                        "event_ref_list": [{"ref": f"e{i}"}],
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO event VALUES (?, ?)",
            (f"e{i}", json.dumps({"date": {"sortval": 2400000 + random.randint(0, 50000)}})),
        )
    conn.commit()
    return conn


def first_page(conn: sqlite3.Connection, column: str, size: int, repeats: int = 5):
    """Time (and explain) one unpaged `ORDER BY ... LIMIT size`."""
    query = Query(select=["handle"], order_by=[OrderBy(column, "asc")], limit=size)
    sql, params = compile_query(PERSON, query, dialect=Dialect.SQLITE)
    plan = [row[-1] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]
    start = time.perf_counter()
    for _ in range(repeats):
        conn.execute(sql, params).fetchall()
    return (time.perf_counter() - start) / repeats * 1000, plan


def walk_pages(conn: sqlite3.Connection, column: str, pages: int, size: int) -> float:
    """Time keyset-paging through `pages` pages -- the cost that repeats."""
    cursor = None
    start = time.perf_counter()
    for _ in range(pages):
        query = Query(
            select=["handle", column],
            order_by=[OrderBy(column, "asc")],
            limit=size,
            after=cursor,
        )
        sql, params = compile_query(PERSON, query, dialect=Dialect.SQLITE)
        rows = conn.execute(sql, params).fetchall()
        if not rows:
            break
        cursor = (rows[-1][1], rows[-1][0])
    return (time.perf_counter() - start) * 1000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--people", type=int, default=20000)
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--pages", type=int, default=25)
    args = parser.parse_args()

    conn = build(args.people)
    print(f"{args.people:,} people, {args.page_size} rows per page\n")

    print(f"First page only:\n{'':2}{'sort column':34} {'ms':>7}  query plan")
    for column, kind in SORT_COLUMNS:
        ms, plan = first_page(conn, column, args.page_size)
        print(f"{'':2}{column:34} {ms:7.1f}  {plan[0]}")
        for line in plan[1:]:
            print(f"{'':45}{line}")
        print(f"{'':45}({kind})")

    print(f"\nWalking {args.pages} pages:")
    for column, _kind in SORT_COLUMNS:
        total = walk_pages(conn, column, args.pages, args.page_size)
        whole = total / args.pages * (args.people / args.page_size) / 1000
        print(
            f"{'':2}{column:34} {total:7.0f} ms total  "
            f"{total / args.pages:6.1f} ms/page  "
            f"(~{whole:.0f}s to walk all {args.people:,})"
        )


if __name__ == "__main__":
    main()
