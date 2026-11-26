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

"""Random queries must mean the same on every backend.

Each case writes one random `where_expr` (`random_queries.QueryGenerator`)
against a random tree full of awkward data (`random_tree`), and runs it
four ways:

- the SQL `compile_query` writes, on SQLite;
- the evaluator, reading objects (`run_query`'s default);
- the evaluator, reading raw data (`RawDataReader`);
- the SQL on PostgreSQL, when `GOQL_TEST_POSTGRES_DSN` is set.

All must match the same records. Each case also selects one random value
(a field, a count, a pick or arithmetic) and checks that SQLite and the
evaluator give it the same value for every record. Expressions the parser
rejects are skipped; one the parser accepts but a backend can't run is a
failure.

`GOQL_RANDOM_QUERIES` sets the number of queries per namespace (default
200), `GOQL_RANDOM_SEED` the seed (default 0). A failure names its seed and
index, and the same pair always writes the same query. For a long hunt,
run `python -m gramps_object_query_language.tests.random_queries`.
"""

import math
import os
import random
from typing import Any, Dict, List, Optional, Tuple

import pytest
from gramps.cli.clidbman import CLIDbManager
from gramps.gen.db.utils import make_database
from gramps.gen.dbstate import DbState

from gramps_object_query_language.evaluator import RawDataReader
from gramps_object_query_language.proxied_query import run_query
from gramps_object_query_language.query import (
    _POSTGRESQL_PHYSICAL_COLUMN_OVERRIDES,
    Dialect,
    Query,
    compile_query,
    select_normalizer,
)
from gramps_object_query_language.query_lang import compile_expr, parse_select_entry
from gramps_object_query_language.tests.random_queries import Pools, QueryGenerator
from gramps_object_query_language.tests.random_tree import build_random_tree

COUNT = int(os.environ.get("GOQL_RANDOM_QUERIES", "200"))
SEED = int(os.environ.get("GOQL_RANDOM_SEED", "0"))

_TABLES = ("person", "family", "event", "place", "citation", "source",
           "repository", "media", "note", "tag", "reference")


def _same_value(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None or isinstance(a, bool) or isinstance(b, bool):
            return False
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return type(a) is type(b) and a == b


class Harness:
    """A random tree in a temporary SQLite database (and, given a DSN, the
    same rows in TEMP tables on PostgreSQL), and `check()` to run one
    random case against it."""

    NAMESPACES = ["Person", "Family", "Event", "Place", "Citation",
                  "Source", "Repository", "Media", "Note", "Tag"]

    def __init__(self, db, dbman, db_name, pg_conn):
        self.db = db
        self._dbman = dbman
        self._db_name = db_name
        self.pg_conn = pg_conn
        self.pools = Pools.from_db(db)
        #: `{"where ok": n, "where rejected": n, "select ok": n, ...}`
        self.stats: Dict[str, int] = {}
        self.gramps_ids = {}
        for table in _TABLES[:-1]:
            column = "name" if table == "tag" else "gramps_id"
            db.dbapi.execute(f"SELECT handle, {column} FROM {table}")
            self.gramps_ids.update(dict(db.dbapi.fetchall()))

    @classmethod
    def open(cls, seed: int = 0, people: int = 40) -> "Harness":
        dbman = CLIDbManager(DbState())
        dirpath, db_name = dbman.create_new_db_cli(f"_test_random_parity_{seed}", dbid="sqlite")
        db = make_database("sqlite")
        db.load(dirpath)
        build_random_tree(db, seed=seed, people=people)
        return cls(db, dbman, db_name, cls._postgres(db))

    @staticmethod
    def _postgres(db):
        """A connection holding TEMP copies of `db`'s tables, or `None`."""
        dsn = os.environ.get("GOQL_TEST_POSTGRES_DSN")
        if not dsn:
            return None
        import psycopg2

        conn = psycopg2.connect(dsn)
        with conn.cursor() as cur:
            for table in _TABLES:
                db.dbapi.execute(f"PRAGMA table_info({table})")
                info = db.dbapi.fetchall()
                quoted = [f'"{row[1]}"' for row in info]
                # Named as a SharedPostgreSQL database names them (`desc_`).
                physical = [
                    f'"{_POSTGRESQL_PHYSICAL_COLUMN_OVERRIDES.get(row[1], row[1])}"' for row in info
                ]
                types = ["INTEGER" if row[2].upper() == "INTEGER" else "TEXT" for row in info]
                cur.execute(
                    f"CREATE TEMP TABLE {table} "
                    f"({', '.join(f'{c} {t}' for c, t in zip(physical, types))})"
                )
                db.dbapi.execute(f"SELECT {', '.join(quoted)} FROM {table}")
                rows = db.dbapi.fetchall()
                if rows:
                    placeholders = ", ".join(["%s"] * len(info))
                    cur.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
        return conn

    def close(self) -> None:
        if self.pg_conn is not None:
            self.pg_conn.close()
        self.db.close()
        self._dbman.remove_database(self._db_name)

    # --- running -------------------------------------------------------------

    def _sqlite(self, spec, query) -> List[tuple]:
        sql, params = compile_query(spec, query, dialect=Dialect.SQLITE)
        self.db.dbapi.execute(sql, params)
        return self.db.dbapi.fetchall()

    def _postgresql(self, spec, query) -> List[tuple]:
        sql, params = compile_query(spec, query, dialect=Dialect.POSTGRESQL)
        with self.pg_conn.cursor() as cur:
            cur.execute("SAVEPOINT q")
            try:
                cur.execute(sql.replace("?", "%s"), params)
                return cur.fetchall()
            finally:
                cur.execute("ROLLBACK TO SAVEPOINT q")

    def _sql_text(self, spec, query) -> str:
        sql, params = compile_query(spec, query, dialect=Dialect.SQLITE)
        return f"{sql}\n  params={params!r}"

    def _name(self, handle: str) -> str:
        return f"{self.gramps_ids.get(handle, '?')}({handle})"

    def check(self, namespace: str, seed: int, index: int) -> Tuple[str, str]:
        """Runs case `index`: `("ok" | "rejected" | "failed", message)`."""
        rng = random.Random(f"{seed}/{namespace}/{index}")
        generator = QueryGenerator(namespace, self.pools, rng)
        expr = generator.generate()
        value = generator.value()
        where_msg = self._check_where(namespace, expr)
        value_msg = self._check_value(namespace, value)
        for part, (outcome, _message) in (("where", where_msg), ("select", value_msg)):
            key = f"{part} {outcome}"
            self.stats[key] = self.stats.get(key, 0) + 1
        for msg in (where_msg, value_msg):
            if msg[0] == "failed":
                return msg
        if where_msg[0] == "rejected" and value_msg[0] == "rejected":
            return "rejected", where_msg[1]
        return "ok", ""

    def _check_where(self, namespace: str, expr: str) -> Tuple[str, str]:
        header = f"{namespace} where_expr: {expr}"
        try:
            spec, where = compile_expr(namespace, expr)
        except ValueError as error:
            return "rejected", f"{header}\n  {error}"
        query = Query(select=["handle"], where=where, limit=None)
        results: Dict[str, Any] = {}
        runners = {
            "sqlite": lambda: {row[0] for row in self._sqlite(spec, query)},
            "evaluator": lambda: {row[0] for row in run_query(self.db, spec, where, select=["handle"])},
            "raw-evaluator": lambda: {
                row[0]
                for row in run_query(self.db, spec, where, select=["handle"], reader=RawDataReader(self.db))
            },
        }
        if self.pg_conn is not None:
            runners["postgresql"] = lambda: {row[0] for row in self._postgresql(spec, query)}
        for name, run in runners.items():
            try:
                results[name] = run()
            except Exception as error:  # a query the parser accepted must run
                return "failed", f"{header}\n  {name} raised {type(error).__name__}: {error}\n  {self._sql_text(spec, query)}"
        expected = results.pop("evaluator")
        lines = []
        for name, got in results.items():
            if got != expected:
                extra = ", ".join(sorted(self._name(h) for h in got - expected))
                missing = ", ".join(sorted(self._name(h) for h in expected - got))
                lines.append(f"  {name} vs evaluator: only {name}: [{extra}]  only evaluator: [{missing}]")
        if lines:
            return "failed", "\n".join([header, *lines, "  " + self._sql_text(spec, query)])
        return "ok", ""

    def _check_value(self, namespace: str, entry: str) -> Tuple[str, str]:
        header = f"{namespace} select: {entry}"
        try:
            spec, _ = compile_expr(namespace, "handle != ''")
            ref, _key = parse_select_entry(spec, entry)
        except ValueError as error:
            return "rejected", f"{header}\n  {error}"
        select = ["handle", ref]
        query = Query(select=select, limit=None)
        normalize = select_normalizer(spec, select)
        results: Dict[str, Dict[str, Any]] = {}
        runners = {
            "sqlite": lambda: [normalize(row) for row in self._sqlite(spec, query)],
            "evaluator": lambda: run_query(self.db, spec, None, select=select),
        }
        if self.pg_conn is not None:
            runners["postgresql"] = lambda: [normalize(row) for row in self._postgresql(spec, query)]
        for name, run in runners.items():
            try:
                results[name] = {row[0]: row[1] for row in run()}
            except Exception as error:
                return "failed", f"{header}\n  {name} raised {type(error).__name__}: {error}\n  {self._sql_text(spec, query)}"
        expected = results.pop("evaluator")
        lines = []
        for name, got in results.items():
            for handle in sorted(set(got) | set(expected)):
                a, b = got.get(handle), expected.get(handle)
                if not _same_value(a, b):
                    lines.append(f"  {self._name(handle)}: {name}={a!r} evaluator={b!r}")
        if lines:
            return "failed", "\n".join([header, *lines[:8], "  " + self._sql_text(spec, query)])
        return "ok", ""


@pytest.fixture(scope="module")
def harness():
    harness = Harness.open(seed=SEED)
    yield harness
    harness.close()


@pytest.mark.parametrize("namespace", Harness.NAMESPACES)
def test_random_queries_agree(harness, namespace):
    failures: List[str] = []
    counts = {"ok": 0, "rejected": 0, "failed": 0}
    for index in range(COUNT):
        outcome, message = harness.check(namespace, SEED, index)
        counts[outcome] += 1
        if outcome == "failed":
            failures.append(f"[seed {SEED}, index {index}] {message}")
    assert not failures, f"{len(failures)} of {COUNT} disagree:\n\n" + "\n\n".join(failures[:10])
    # Most of what the generator writes must get through the parser, or
    # this test checks nothing.
    assert counts["ok"] >= COUNT // 2, counts
