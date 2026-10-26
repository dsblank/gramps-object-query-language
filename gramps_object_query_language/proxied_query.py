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

"""Run a `where` expression against a possibly-proxied `db`.

Reads every record of the type through `db`'s public methods (a
`RecordReader`'s `iter_records`) and tests it with `evaluate_where`. A
proxy or restricted database only returns the records the user may see,
already cleaned, so everything this module ever hands to `evaluate_where`
has already been through `db`'s own rules, at any relationship depth -- see
`evaluator.py`'s module docstring for why that's sufficient on its own,
with no separate privacy handling needed here.

Not fast: every record of the type is read (and, under a proxy or
restricted database, cleaned) to test it. See `object_query.py`'s dispatch
for when this path is used instead of `query.py`'s SQL compiler. By default
records are read as Gramps objects (`ObjectReader`); a caller whose
database hands out raw data cheaply passes `reader=RawDataReader(db)`,
which reads the whole table through its cursor and never builds an object
per record -- only the final page is turned into objects, when no `select`
is given. A caller that also needs the total number of matches (a paged
listing's count) calls `run_query_with_total`, which returns it from the
same pass instead of a second, unlimited `run_query`.

`run_query` also implements `order_by`/`limit`/`after` (keyset pagination)
and `select`, entirely in Python over the already-fetched match list, so a
`Query` means the same thing on this path as it does through `query.py`'s
SQL compiler -- see `ROADMAP.md`'s "Evaluator-path pagination/sort parity"
section for the gap this closes. Two deliberate scope caps, matching that
section's recommended v1:

- `order_by` is restricted to flat columns (`OrderBy.column` is always a
  plain string, checked against `spec.columns`) -- the same cap the SQL
  path's own `order_by` already has (a `JsonPath`/`RelatedObject` column
  can't be sorted by there either), so this path doesn't leapfrog ahead of
  what SQL itself can do.
- Collation matches the SQL path's own default exactly, not full locale
  parity: `_null_safe_cmp` ASCII-case-folds any `str` operand before
  comparing (`_nocase_key`), the same folding SQLite's built-in `NOCASE`
  collation does (`query.py`'s `_column_expr` default -- see ROADMAP.md's
  "Default `NOCASE` collation on the SQL path"). Only the 26 ASCII letters
  fold; accented/non-Latin letters don't, on either path, so the two stay
  in agreement. A real locale-aware `COLLATE` still has no Python-side
  equivalent here -- that remains a documented gap.

`NULL` placement, for both plain sorting and keyset seeking, matches
SQLite's own verified default (confirmed empirically, not assumed): `NULL`
sorts as the smallest value in both directions, so `DESC` is the exact
reverse of `ASC`, `NULL`s included. PostgreSQL's default is the opposite,
so the SQL path pins this order there with explicit `NULLS FIRST`/`NULLS
LAST` (`query.py`'s `_order_direction_sql`). Keyset seeking (`after`) mirrors
`query.py`'s `_compile_keyset` SQL, which (after its own NULL-safety fix --
see that function's docstring) applies this identical NULL-is-smallest rule
to the seek predicate too, rather than relying on SQL's raw three-valued
comparison semantics -- a plain `col > ?`/`col < ?` against a real `NULL`
is always `UNKNOWN`, which silently produced wrong seek results in two
different ways before that fix (a cursor taken from a `NULL` row couldn't
seek past it at all; a `desc`-sorted `NULL` row was silently dropped from
every later page regardless of the cursor). `_matches_keyset` below reuses
`_null_safe_cmp` directly for exactly this reason -- one NULL rule, shared
by sorting and seeking on both this path and the SQL path, not two.
"""

from __future__ import annotations

import functools
import string
from typing import Any, List, Optional, Sequence, Tuple

from gramps.gen.lib.json_utils import data_to_object

from .evaluator import ObjectReader, RecordReader, evaluate_where, resolve_column_ref
from .query import (
    ColumnRef,
    ObjectTypeSpec,
    OrderBy,
    QueryError,
    effective_order_by,
    order_by_key,
    resolve_order_by,
    resolve_ref_string,
)

_ASCII_NOCASE_TABLE = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _nocase_key(value: str) -> str:
    """ASCII-only case fold, matching SQLite's built-in `NOCASE` collation --
    the SQL path's own default for text `ORDER BY` columns (`query.py`'s
    `_column_expr`). Deliberately not `str.lower()`: Python's `.lower()`
    case-folds far beyond ASCII (e.g. accented letters), which would fold
    values `NOCASE` leaves alone and put the two paths back out of
    agreement -- see this module's own docstring.
    """
    return value.translate(_ASCII_NOCASE_TABLE)


def _null_safe_cmp(a: Any, b: Any, direction: str) -> int:
    """Column-level three-way compare matching SQLite's verified `ORDER BY`
    default: `NULL` sorts as the smallest value regardless of direction, so
    `DESC` is the exact reverse of `ASC`, `NULL`s included. String operands
    are ASCII-case-folded first (`_nocase_key`), matching the SQL path's own
    `NOCASE` default so the two paths agree on order.
    """
    if isinstance(a, str):
        a = _nocase_key(a)
    if isinstance(b, str):
        b = _nocase_key(b)
    if a is None and b is None:
        cmp = 0
    elif a is None:
        cmp = -1
    elif b is None:
        cmp = 1
    elif a < b:
        cmp = -1
    elif a > b:
        cmp = 1
    else:
        cmp = 0
    return -cmp if direction == "desc" else cmp


def _sort_key_row(
    reader: RecordReader, data: Any, ordering: Sequence[OrderBy], spec: ObjectTypeSpec
) -> Tuple[Any, ...]:
    return tuple(resolve_column_ref(reader, data, ob.column, spec) for ob in ordering)


def _sort_matches(
    reader: RecordReader, matches: List[Any], ordering: Sequence[OrderBy], spec: ObjectTypeSpec
) -> List[Tuple[Tuple[Any, ...], Any]]:
    """`matches` paired with its resolved sort-key row, sorted per `ordering`."""
    keyed = [(_sort_key_row(reader, data, ordering, spec), data) for data in matches]

    def compare(row_a: Tuple[Any, ...], row_b: Tuple[Any, ...]) -> int:
        key_a, _ = row_a
        key_b, _ = row_b
        for value_a, value_b, ob in zip(key_a, key_b, ordering):
            result = _null_safe_cmp(value_a, value_b, ob.direction)
            if result != 0:
                return result
        return 0

    keyed.sort(key=functools.cmp_to_key(compare))
    return keyed


def _matches_keyset(
    key_row: Sequence[Any], after: Sequence[Any], ordering: Sequence[OrderBy]
) -> bool:
    """Python equivalent of `query._compile_keyset`'s NULL-safe seek
    predicate, after its own NULL-safety fix -- "ranks strictly after the
    cursor" reuses `_null_safe_cmp`'s own total order (`NULL` is the
    smallest value in both directions, the same rule plain sorting uses),
    rather than SQL's raw three-valued comparison semantics. A tie always
    uses `"asc"` regardless of that column's own direction, since equality
    itself isn't direction-dependent (`_null_safe_cmp` returns `0` for a tie
    under either direction -- only strict ordering flips).
    """
    for i, ob in enumerate(ordering):
        if any(_null_safe_cmp(key_row[j], after[j], "asc") != 0 for j in range(i)):
            continue
        if _null_safe_cmp(key_row[i], after[i], ob.direction) > 0:
            return True
    return False


def run_query(
    db: Any,
    spec: ObjectTypeSpec,
    where: Any,
    *,
    order_by: Sequence[OrderBy] = (),
    limit: Optional[int] = None,
    after: Optional[Sequence[Any]] = None,
    select: Optional[Sequence[ColumnRef]] = None,
    reader: Optional[RecordReader] = None,
) -> List[Any]:
    """Every real object of `spec`'s type matching `where`, read through `db`.

    `db` may be a proxy, a restricted database or a plain database --
    either way, the returned objects (and match decisions) reflect whatever
    `db` itself would return for each handle, never an unproxied lookup.

    `reader`, when given, is a `RecordReader` for `db` to read it with
    instead of the default `ObjectReader` -- e.g. `RawDataReader(db)` for a
    database that hands out raw data cheaply. It must read `db` itself.

    `order_by`/`limit`/`after` mean exactly what they mean on `Query`/
    `compile_query` -- see this module's own docstring for the scope cap
    (flat-column `order_by` only) and its ASCII-`NOCASE` collation/NULL
    placement policies. A trailing `handle` tiebreaker is always
    applied (via `effective_order_by`), even when `order_by` is empty, so
    the result is always in a fully deterministic order, matching the SQL
    path's own always-present `ORDER BY`.

    `select`, when given, projects the final page into a list of value
    tuples (one per entry in `select`, resolved via `resolve_column_ref`)
    instead of returning full objects -- the evaluator-path equivalent of
    the SQL path's `SELECT` list. Omitting `select` (the default) keeps
    returning full objects, unchanged from before this parameter existed.
    """
    rows, _total = run_query_with_total(
        db,
        spec,
        where,
        order_by=order_by,
        limit=limit,
        after=after,
        select=select,
        reader=reader,
    )
    return rows


def run_query_with_total(
    db: Any,
    spec: ObjectTypeSpec,
    where: Any,
    *,
    order_by: Sequence[OrderBy] = (),
    limit: Optional[int] = None,
    after: Optional[Sequence[Any]] = None,
    select: Optional[Sequence[ColumnRef]] = None,
    reader: Optional[RecordReader] = None,
) -> Tuple[List[Any], int]:
    """`run_query`'s rows, and how many records match `where` in all.

    The total ignores `after` and `limit`, like the SQL path's
    `compile_count_query`. Every record is read and tested anyway to sort
    the matches, so the total costs nothing extra -- unlike a second
    `run_query` without `limit`, which reads and tests every record again.
    """
    if reader is None:
        reader = ObjectReader(db)
    elif reader.db is not db:
        raise ValueError("reader must read the db the query runs against")
    matches = [
        data
        for data in reader.iter_records(spec.table)
        if evaluate_where(reader, data, where, spec)
    ]
    total = len(matches)

    # Resolved, not whitelist-checked: `order_by` takes the same
    # `ColumnRef`s `select` does, and `_sort_key_row` already resolves
    # whatever it's given via `resolve_column_ref`. The Python sort needs
    # no CAST hint the way the SQL path does -- `<`/`>` on the extracted
    # values already compare by their own types.
    ordering = effective_order_by(resolve_order_by(spec, order_by))
    keyed = _sort_matches(reader, matches, ordering, spec)

    if after is not None:
        if len(after) != len(ordering):
            raise QueryError(
                f"after cursor has {len(after)} values, expected "
                f"{len(ordering)} "
                f"({', '.join(order_by_key(ob.column) for ob in ordering)})"
            )
        keyed = [(key, data) for key, data in keyed if _matches_keyset(key, after, ordering)]

    matches = [data for _, data in keyed]
    if limit is not None:
        matches = matches[:limit]

    if select is not None:
        # Resolve exactly as the SQL path's `compile_query` does, rather
        # than whitelist-checking strings here: a path entry
        # ("birth.place.title") has to mean the same thing on both paths.
        # `resolve_ref_string` still rejects everything `check_columns`
        # rejected -- a flat column against `spec.columns`, a JSON path
        # against the type's own Gramps schema -- just with a better error.
        columns = [
            resolve_ref_string(spec, column) if isinstance(column, str) else column
            for column in select
        ]
        rows = [
            tuple(resolve_column_ref(reader, data, column, spec) for column in columns)
            for data in matches
        ]
    else:
        rows = [data_to_object(data) if isinstance(data, dict) else data for data in matches]
    return rows, total
