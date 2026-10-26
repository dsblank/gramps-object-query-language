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

"""Evaluate `query.py`'s AST directly against Gramps records.

`query.py` compiles a `Query`/`where` expression to SQL, which is only safe
to run against an *unproxied* database -- it has no notion of privacy or any
other proxy-applied rule, and reimplementing one in SQL is exactly the
mistake this module exists to avoid (see `query.py`'s module docstring).
This module instead walks the same AST and evaluates it directly against a
record's raw data (the JSON-shaped dict Gramps stores, the same shape
`object_to_dict()` produces), read through whatever `db` the caller passes
in. When `db` is a proxy or another restricted database, every record this
module ever sees -- the row itself and anything reached via a
`RelatedObject` hop, at any depth -- has already been through that
database's own rules for what to hide and what to remove: `RecordReader`
only ever uses `db`'s public `get_raw_<table>_data`, never a lower-level
method a proxy might pass straight through to the database underneath it.
Correctness follows
from that alone: there is no separate privacy guard here the way
`Comparison.compile()` needs a `CASE WHEN` guard for NULL-safe `Eq`/`Ne` --
a masked field already reads back as whatever the proxy's `sanitize_*` left
it as (typically `None`), and plain Python `==`/`!=`/`is None` against that
already-correct value is automatically the right, non-leaking answer.

A record is evaluated in whichever form it arrives in, never converted
wholesale: a Gramps object (what a Gramps filter rule's `apply_to_one` is
handed), or its raw data (the JSON-shaped dict Gramps stores). Related
records are read in the same form, through a `RecordReader`: by default
`ObjectReader` for an object row, which goes through `db`'s object getters
and so through any cache in front of them (`CacheProxyDb`), and
`RawDataReader` for a raw-data row. Reading a whole table as raw data
(`run_query(..., reader=RawDataReader(db))`) avoids building an object per
record, which is most of the cost of this path on a database that can hand
out raw data cheaply; a Gramps proxy can't (it builds the object anyway),
so objects stay the default.

Not fast: no SQL push-down, no keyset narrowing before records are read.
Intended for the proxied path, where the query's result set is expected to
be small relative to the table, or where correctness matters more than
p99 latency -- see `object_query.py`'s dispatch.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any, Iterator, Optional, Tuple

from gramps.gen.errors import HandleError
from gramps.gen.lib import json_utils

from .query import (
    And,
    BacklinkClassFilter,
    Backlinks,
    Collection,
    CollectionCount,
    ColumnIndex,
    ColumnRef,
    Comparison,
    Contains,
    Exists,
    FlatColumnRef,
    In,
    JsonArrayCount,
    JsonArrayExists,
    JsonPath,
    Length,
    Not,
    ObjectTypeSpec,
    Or,
    RelatedObject,
)

# Real getter method name for each `ObjectTypeSpec.table`, for callers that
# need the object itself through `db` (whatever proxy or plain database that
# is) -- never a direct/unproxied lookup. This module reads raw data instead,
# see `RecordReader`.
GETTER_BY_TABLE: dict[str, str] = {
    "person": "get_person_from_handle",
    "family": "get_family_from_handle",
    "event": "get_event_from_handle",
    "place": "get_place_from_handle",
    "repository": "get_repository_from_handle",
    "source": "get_source_from_handle",
    "citation": "get_citation_from_handle",
    "media": "get_media_from_handle",
    "note": "get_note_from_handle",
    "tag": "get_tag_from_handle",
}


class RecordReader:
    """Reads related records through `db`, remembering recent ones.

    Subclasses decide the form records come back in -- `ObjectReader`
    (Gramps objects) or `RawDataReader` (raw data) -- by implementing
    `read` and `iter_records`, only ever through `db`'s public methods,
    which a proxy or a restricted database answers with records as the
    user may see them (`None` for a hidden one), never the stored records.
    A missing handle reads as `None` too, whichever way `db` reports it
    (plain databases return `None`, Gramps proxies raise `HandleError`).

    One reader serves one query: many rows often reach the same related
    record (events sharing a place, children sharing a family), and reading
    it through a proxy or restricted database means cleaning it again each
    time. When the cache is full, the least recently used record is
    dropped, so memory stays bounded on large trees while the records many
    rows share stay cached.
    """

    def __init__(self, db: Any, cache_size: int = 10000) -> None:
        self.db = db
        self._cache: OrderedDict[Tuple[str, str], Any] = OrderedDict()
        self._cache_size = cache_size

    def get(self, table: str, handle: str) -> Any:
        """`table`'s record `handle`, or `None`, cached."""
        key = (table, handle)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        record = self.read(table, handle)
        if len(self._cache) >= self._cache_size:
            self._cache.popitem(last=False)
        self._cache[key] = record
        return record

    def read(self, table: str, handle: str) -> Any:
        """`table`'s record `handle`, or `None`, uncached: for reading
        through a whole table, which would only evict the related records
        worth keeping."""
        raise NotImplementedError

    def iter_records(self, table: str) -> Iterator[Any]:
        """Every record of `table` that `db` returns."""
        raise NotImplementedError


class ObjectReader(RecordReader):
    """Reads Gramps objects through `db`'s `get_<table>_from_handle`.

    The default: every database offers these getters, a Gramps proxy
    decides visibility and cleans records in them, and a `CacheProxyDb` in
    front caches what they return across a whole filter run.
    """

    def read(self, table: str, handle: str) -> Any:
        try:
            return getattr(self.db, GETTER_BY_TABLE[table])(handle)
        except HandleError:
            return None

    def iter_records(self, table: str) -> Iterator[Any]:
        for handle in getattr(self.db, f"iter_{table}_handles")():
            obj = self.read(table, handle)
            if obj is not None:
                yield obj


class RawDataReader(RecordReader):
    """Reads raw data through `db`'s `get_raw_<table>_data` and cursor.

    Opt-in: much faster than `ObjectReader` on a database that hands out
    raw data without building an object first -- a plain database, or a
    restricted database that cleans raw data directly -- but slower on a
    Gramps proxy, whose raw getters build the object anyway and then wrap
    its data.
    """

    def read(self, table: str, handle: str) -> Optional[dict]:
        try:
            return getattr(self.db, f"get_raw_{table}_data")(handle)
        except HandleError:
            return None

    def iter_records(self, table: str) -> Iterator[dict]:
        """Through the public cursor, so a database can read the whole
        table at once -- except for tags, which are few and not every
        database offers a cursor for."""
        if table == "tag":
            for handle in self.db.iter_tag_handles():
                data = self.read(table, handle)
                if data is not None:
                    yield data
            return
        with getattr(self.db, f"get_{table}_cursor")() as cursor:
            for _, data in cursor:
                if data is not None:
                    yield data


def _reader(db: Any, row: Any) -> RecordReader:
    """`db` if it's already a reader, else one reading related records in
    the same form as `row`."""
    if isinstance(db, RecordReader):
        return db
    return RawDataReader(db) if isinstance(row, dict) else ObjectReader(db)


def _handle(row: Any) -> Any:
    return row["handle"] if isinstance(row, dict) else row.handle


def _given_name_of_data(data: dict) -> str:
    primary_name = data.get("primary_name")
    return primary_name.get("first_name", "") if primary_name else ""


def _surname_of_data(data: dict) -> str:
    primary_name = data.get("primary_name")
    if not primary_name:
        return ""
    surname_list = primary_name.get("surname_list")
    if not surname_list or not surname_list[0]:
        return ""
    return surname_list[0].get("surname", "")


def _enclosed_by_of_data(data: dict) -> str:
    for placeref in data.get("placeref_list") or []:
        return placeref.get("ref", "")
    return ""


def _given_name(obj: Any) -> str:
    primary_name = obj.get_primary_name()
    return primary_name.get_first_name() if primary_name else ""


def _surname(obj: Any) -> str:
    primary_name = obj.get_primary_name()
    if not primary_name:
        return ""
    surname_list = primary_name.get_surname_list()
    if not surname_list or not surname_list[0]:
        return ""
    return surname_list[0].surname


def _enclosed_by(obj: Any) -> str:
    for placeref in obj.get_placeref_list():
        return placeref.ref
    return ""


# `given_name`/`surname`/`enclosed_by` are real SQL columns (populated by the
# DB layer at write time), but neither attributes of the object nor keys of
# its raw data -- mirrors gramps core's `gen/db/generic.py`
# `DbGeneric._get_person_data`/`_get_place_data` exactly, the functions that
# populate those same columns, for each form a record comes in.
_DERIVED_COLUMNS: dict[str, dict[str, Any]] = {
    "person": {"given_name": _given_name, "surname": _surname},
    "place": {"enclosed_by": _enclosed_by},
}
_DERIVED_COLUMNS_OF_DATA: dict[str, dict[str, Any]] = {
    "person": {"given_name": _given_name_of_data, "surname": _surname_of_data},
    "place": {"enclosed_by": _enclosed_by_of_data},
}


def get_flat_column(obj: Any, column: str, spec: ObjectTypeSpec) -> Any:
    """A flat column's value straight off a record, a Gramps object or its
    raw data.

    An attribute (or top-level key) for anything `get_secondary_fields()`
    names (`gramps_id`, `gender`, `private`, `handle`, `father_handle`,
    `place`, ...) -- which is everything except the small, fixed set of
    derived columns above.
    """
    if isinstance(obj, dict):
        derived = _DERIVED_COLUMNS_OF_DATA.get(spec.table, {})
        if column in derived:
            return derived[column](obj)
        return obj.get(column)
    derived = _DERIVED_COLUMNS.get(spec.table, {})
    if column in derived:
        return derived[column](obj)
    return getattr(obj, column)


def _walk_json_path(data: Any, segments: Any, root: Any) -> Any:
    """Walk `JsonPath.segments` against `data` (raw data, or part of it).

    A `ColumnIndex` segment resolves against `root` -- the row the path
    started from -- not whatever `data` has been narrowed to by that point,
    mirroring `_render_handle_ref`'s "this row's `<column>`" semantics
    exactly (only ever appears as a `RelatedObject.handle_ref` segment, so
    this only matters one level deep in practice).
    """
    current = data
    for segment in segments:
        if current is None:
            return None
        if isinstance(segment, ColumnIndex):
            index = root.get(segment.column) if isinstance(root, dict) else getattr(root, segment.column)
            if index is None or index < 0:
                return None
            segment = index
        if isinstance(segment, int):
            if not isinstance(current, list) or not -len(current) <= segment < len(current):
                return None
            current = current[segment]
        else:
            if not isinstance(current, dict):
                return None
            current = current.get(segment)
    return current


def get_json_path(obj: Any, path: JsonPath) -> Any:
    """`JsonPath.segments` extracted from a record's raw data (a Gramps
    object is converted with `object_to_dict()` first).

    The raw data is the same JSON-shaped structure the compiled SQL path
    navigates via `json_extract`/`->` against the stored `json_data`
    column, so the two paths interpret a `JsonPath` identically without
    hand-duplicating that shape.

    `obj` is either a whole record or, for a field inside an
    `any(...)`/`len(..., condition)` condition or a self-linked
    `Collection`'s own condition (any `base_column` other than
    `"json_data"` -- see `ObjectTypeSpec.element_base_column`), one array
    element's dict. Both are walked the same way: unlike SQL, where each
    unnest needs its own alias, the evaluator only ever has one dict in
    scope at a time.
    """
    if obj is None:
        return None
    data = obj if isinstance(obj, dict) else json_utils.object_to_dict(obj)
    return _walk_json_path(data, path.segments, obj)


def _resolve_related_object(
    reader: RecordReader, data: Any, ref: RelatedObject, spec: ObjectTypeSpec
) -> Any:
    if isinstance(ref.handle_ref, JsonPath):
        handle = get_json_path(data, ref.handle_ref)
    else:
        handle = get_flat_column(data, ref.handle_ref, spec)
    if not handle:
        return None
    return reader.get(ref.target.table, handle)


def resolve_column_ref(db: Any, obj: Any, ref: ColumnRef, spec: ObjectTypeSpec) -> Any:
    """`query.py`'s `_render_column`, evaluated in Python against a record
    instead of rendered as SQL.

    `obj` is a Gramps object or a record's raw data. `db` is whatever the
    caller is running under (proxy or plain), or a `RecordReader` wrapping
    it to share its cache across calls -- every
    `RelatedObject` hop is read through it (`_resolve_related_object`), so
    the same rules that applied to `obj` itself apply to everything reached
    from it, at any depth, with no separate handling needed here.
    """
    return _resolve(_reader(db, obj), obj, ref, spec)


def _resolve(
    reader: RecordReader, data: Any, ref: ColumnRef, spec: ObjectTypeSpec
) -> Any:
    if data is None:
        return None
    if isinstance(ref, RelatedObject):
        related = _resolve_related_object(reader, data, ref, spec)
        return _resolve(reader, related, ref.field, ref.target)
    if isinstance(ref, JsonPath):
        return get_json_path(data, ref)
    if isinstance(ref, CollectionCount):
        return _collection_count(reader, data, ref)
    if isinstance(ref, Length):
        return _length_of(reader, data, ref, spec)
    if isinstance(ref, JsonArrayCount):
        return _json_array_count(reader, data, ref, spec)
    if isinstance(ref, FlatColumnRef):
        return get_flat_column(data, ref.name, spec)
    return get_flat_column(data, ref, spec)


def _collection_handles(data: Any, collection: Collection) -> list:
    """The list of related handles for `collection` on `data` -- a plain
    handle string per element (`notes`), or each element's `ref_field`
    pulled out (`children`'s `ChildRef.ref`) -- mirrors `query.py`'s
    `_collection_source_sqlite`/`_collection_source_postgresql` exactly, just
    walking the real in-memory list instead of rendering SQL to iterate it.
    """
    items = get_json_path(data, collection.list_path) or []
    if collection.ref_field:
        return [item.get(collection.ref_field) for item in items if item]
    return [item for item in items if item]


def _self_linked_entry(related: Any, data: Any, collection: Collection) -> Optional[dict]:
    """The one entry (a dict, matching the SQL side's `link.value` shape --
    see `query.py`'s `_self_link_source_sqlite`) in `related`'s own
    `collection.self_link_field` array whose `collection.self_link_ref_field`
    equals `data`'s own handle -- `None` if no such entry exists (a
    data-integrity edge case, e.g. `person.handle` listed in a family's
    `parent_family_list` with no matching `ChildRef` in that family's own
    `child_ref_list` -- treated as "this related row contributes nothing,"
    not an error, unlike the real `HaveAltFamilies` rule this construct was
    built for, whose own `ref[0]` would raise `IndexError` on exactly this
    case; see `ROADMAP.md`'s "adopted" write-up).

    Walks a plain dict (via `get_json_path`), not a real `ChildRef` object,
    for the same reason `any()`'s own element-condition machinery does --
    the condition's fields were parsed against a synthetic, table-less
    `ObjectTypeSpec` (see `resolve_any_path`), so every one of them is a
    `JsonPath`, never a flat-column `getattr`.
    """
    entries = get_json_path(related, JsonPath((collection.self_link_field,))) or []
    return next(
        (
            e
            for e in entries
            if isinstance(e, dict) and e.get(collection.self_link_ref_field) == _handle(data)
        ),
        None,
    )


def _backlink_handles(
    reader: RecordReader, data: Any, condition: Optional[BacklinkClassFilter]
) -> Iterator[Tuple[str, str]]:
    """`(class_name, handle)` pairs for every object referencing `data`,
    matching `condition` (a `BacklinkClassFilter`, `Backlinks`' only
    supported condition shape -- see query.py) if given -- the evaluator
    counterpart to `query.py`'s `_backlinks_subquery_body`, walking
    `db.find_backlink_handles` directly instead of joining the physical
    `reference` table in SQL.

    "eq"/"in" map straight onto `find_backlink_handles`'s own
    `include_classes` parameter (already exactly "which classes to
    include"); "ne" has no such shape to pass through (there's no
    "every class except this one" parameter), so it walks the
    unrestricted result and filters in Python instead.

    Privacy comes from `db` itself, same as every other relationship this
    module evaluates (see the module docstring): a proxy or restricted
    database's own `find_backlink_handles` already leaves out referrers the
    user may not see before this function ever sees them -- no separate
    privacy handling needed here.
    """
    db = reader.db
    handle = _handle(data)
    if condition is None:
        yield from db.find_backlink_handles(handle)
    elif condition.op in ("eq", "in"):
        include_classes = [condition.value] if condition.op == "eq" else list(condition.value)
        yield from db.find_backlink_handles(handle, include_classes)
    else:
        for class_name, ref_handle in db.find_backlink_handles(handle):
            if class_name != condition.value:
                yield class_name, ref_handle


def _collection_count(reader: RecordReader, data: Any, count: CollectionCount) -> int:
    """How many related rows in `count.collection` match `count.condition`
    (every related row at all, if `condition` is `None`) -- the evaluator
    counterpart to `query.py`'s `CollectionCount` SQL rendering. Unlike
    `Exists`'s short-circuit on the first match, this has to walk every
    related row, matching `COUNT(*)`'s own semantics. A related record that
    doesn't read back (hidden, or missing) isn't counted, like the SQL
    join that finds no row for it.
    """
    if isinstance(count.collection, Backlinks):
        return sum(1 for _ in _backlink_handles(reader, data, count.condition))
    return sum(
        1
        for row, row_spec in _collection_rows(reader, data, count.collection)
        if count.condition is None or _matches(reader, row, count.condition, row_spec)
    )


def _collection_rows(
    reader: RecordReader, data: Any, collection: Collection
) -> Iterator[Tuple[Any, ObjectTypeSpec]]:
    """What `collection`'s condition is tested against, for each related
    record that reads back: the record itself, or for a self-linked
    collection its entry pointing back at `data` (`_self_linked_entry`)."""
    for handle in _collection_handles(data, collection):
        related = reader.get(collection.target.table, handle)
        if related is None:
            continue
        if collection.self_link_field:
            entry = _self_linked_entry(related, data, collection)
            if entry is not None:
                yield entry, collection.target
        else:
            yield related, collection.target


def _length_of(reader: RecordReader, data: Any, length: Length, spec: ObjectTypeSpec) -> int:
    """`len(x)`, evaluated against a real object -- the evaluator counterpart
    to `query.py`'s `Length` SQL rendering. Resolves `length.inner`
    (a `JsonPath`, or a `RelatedObject` chain whose own innermost `field` is
    the `Length` -- see `resolve_length_path`) exactly like any other
    `ColumnRef`, via a plain recursive `resolve_column_ref` call, so a
    relationship hop (`len(father.aka_surnames)`) is fetched through `db`
    the same privacy-respecting way any other `RelatedObject` field is.

    A missing path or a non-list value both come back as `0`, not `None` --
    matching `query.py`'s SQL rendering (see `Length`'s own docstring for
    why), rather than diverging on the one input SQL's `COALESCE`/`CASE`
    guards were built specifically to handle.
    """
    value = _resolve(reader, data, length.inner, spec)
    return len(value) if isinstance(value, list) else 0


def _json_array_items(reader: RecordReader, data: Any, array: ColumnRef, spec: ObjectTypeSpec) -> list:
    """The raw list of dicts `array` (a `JsonPath`, or a `RelatedObject`
    chain ending in one -- see query.py's `resolve_any_path`) resolves to
    on `data` -- shared by `JsonArrayExists`/`JsonArrayCount`'s own evaluator
    counterparts. Reuses `resolve_column_ref` verbatim, so a relationship-
    crossing array (`father.attribute_list`) gets the same privacy-
    respecting hop (`_resolve_related_object`) any other `RelatedObject`
    field already does, with no separate handling needed here. A missing
    path or a non-list value both come back as `[]`, matching `Length`'s
    own "missing/non-array is 0" reasoning one level up (`any`/`len` over
    nothing to iterate is simply nothing to match).
    """
    items = _resolve(reader, data, array, spec)
    return items if isinstance(items, list) else []


def _json_array_count(reader: RecordReader, data: Any, count: "JsonArrayCount", spec: ObjectTypeSpec) -> int:
    """How many elements of `count.array` match `count.condition` -- the
    evaluator counterpart to `query.py`'s `JsonArrayCount` SQL rendering.
    Each element is a plain dict (no real Gramps object exists per array
    element), evaluated against `count.element_spec` the same way
    `_collection_count` evaluates a related row against `collection.target`.
    """
    items = _json_array_items(reader, data, count.array, spec)
    if count.condition is None:
        return len(items)
    return sum(1 for item in items if _matches(reader, item, count.condition, count.element_spec))


def _like_to_regex(pattern: str) -> re.Pattern:
    """Translate a SQL `LIKE ... ESCAPE '\\'` pattern (`%`/`_` wildcards) to a
    regex, the way `query.py`'s `Like` renders it on both dialects.

    `\\` makes the next character literal (`\\%` is a literal `%`); a
    trailing lone `\\` -- which `Like` rejects up front for a literal
    pattern, but a field-vs-field pattern can still produce at runtime --
    matches nothing, same as SQLite. `re.DOTALL` because SQL's `%`/`_`
    match a newline too. Case-insensitive like SQLite's `LIKE` and
    PostgreSQL's `ILIKE`, though `re.IGNORECASE` folds non-ASCII letters as
    well, which SQLite doesn't -- see `query.py`'s `_like_op`.
    """
    out = []
    chars = iter(pattern)
    for char in chars:
        if char == "\\":
            escaped = next(chars, None)
            if escaped is None:
                return re.compile(r"(?!)")
            out.append(re.escape(escaped))
        elif char == "%":
            out.append(".*")
        elif char == "_":
            out.append(".")
        else:
            out.append(re.escape(char))
    return re.compile("".join(out), re.IGNORECASE | re.DOTALL)


_ORDERING_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def _compare(op: str, left: Any, right: Any) -> Optional[bool]:
    """`None` means SQL's `UNKNOWN` -- see `_evaluate_tri`'s docstring for
    why this three-valued result matters, not just a plain `bool`.
    """
    if op == "=":
        return left == right
    if op == "!=":
        return left != right
    if op == "LIKE":
        if left is None or right is None:
            return None
        return _like_to_regex(str(right)).fullmatch(str(left)) is not None
    if op == "REGEXP":
        # Mirrors gramps core's `dbapi/sqlite.py` `regexp(expr, value)` UDF
        # exactly (`re.search(expr, value, re.MULTILINE)`) -- an unanchored,
        # case-sensitive search, not `_like_to_regex`'s case-insensitive
        # full-match -- so this evaluator path and a real SQLite backend
        # agree on every row. `right` is the pattern (`Regex.value`), `left`
        # the haystack, same left/right convention as `LIKE` above.
        if left is None or right is None:
            return None
        return re.search(str(right), str(left), re.MULTILINE) is not None
    if op in _ORDERING_OPS:
        # SQL's ordering comparisons against a NULL operand are UNKNOWN, not
        # False, under standard three-valued logic -- Python raises
        # TypeError instead, so this has to be explicit. Collapsing this
        # straight to False here (rather than in `_evaluate_tri`, one layer
        # up) would be indistinguishable from a genuine False to `Not`,
        # which needs to leave UNKNOWN as UNKNOWN rather than flip it to
        # True -- see `_evaluate_tri`.
        if left is None or right is None:
            return None
        return _ORDERING_OPS[op](left, right)
    raise ValueError(f"unsupported operator: {op!r}")


def _evaluate_tri(reader: RecordReader, data: Any, expr: Any, spec: ObjectTypeSpec) -> Optional[bool]:
    """`evaluate_where`'s actual recursion, in SQL's three-valued logic
    (`True`/`False`/`None` for `UNKNOWN`) rather than a plain `bool`.

    A leaf comparison against a missing value (a masked field, or a path/
    relationship that doesn't resolve) is `UNKNOWN`, not `False` -- SQL's
    `NOT UNKNOWN` is still `UNKNOWN`, not `True`, so if this collapsed to a
    definite `False` at each leaf the way a plain `bool` would force it to,
    `Not` wrapping that leaf would incorrectly flip it to `True` where SQL
    would still exclude the row. Recursing in three-valued logic here, and
    collapsing to a real `bool` exactly once -- in `evaluate_where`, at the
    very end -- keeps `Not`/`And`/`Or` behaving the same as their compiled-
    SQL counterparts at any nesting depth, not just one level deep.

    `And`/`Or` follow SQL's own precedence for combining three values:
    a definite `False` always wins an `And` (regardless of any `UNKNOWN`
    sibling), a definite `True` always wins an `Or` the same way -- checked
    first, before falling back to "`UNKNOWN` if any operand is, else the
    identity value" -- matching `AND`/`OR`'s truth tables exactly, not just
    "any/all treat `None` as falsy" (which would get the dominance wrong).
    """
    if expr is None:
        return True
    if isinstance(expr, And):
        results = [_evaluate_tri(reader, data, e, spec) for e in expr.exprs]
        if any(r is False for r in results):
            return False
        if any(r is None for r in results):
            return None
        return True
    if isinstance(expr, Or):
        results = [_evaluate_tri(reader, data, e, spec) for e in expr.exprs]
        if any(r is True for r in results):
            return True
        if any(r is None for r in results):
            return None
        return False
    if isinstance(expr, Not):
        result = _evaluate_tri(reader, data, expr.expr, spec)
        return None if result is None else not result
    if isinstance(expr, Exists):
        # Always a definite True/False, never UNKNOWN -- matching SQL's own
        # EXISTS/NOT EXISTS, which never propagates NULL from a subquery
        # row that fails its WHERE, it's simply not counted. Unlike every
        # other branch here, there's no missing-value case to collapse to
        # None for.
        collection = expr.collection
        if isinstance(collection, Backlinks):
            return any(True for _ in _backlink_handles(reader, data, expr.condition))
        return any(
            expr.condition is None or _matches(reader, row, expr.condition, row_spec)
            for row, row_spec in _collection_rows(reader, data, collection)
        )
    if isinstance(expr, JsonArrayExists):
        # Same "always a definite True/False" reasoning as Exists just
        # above -- an intra-record array with nothing matching (or nothing
        # in it at all) is simply False, not UNKNOWN.
        items = _json_array_items(reader, data, expr.array, spec)
        if expr.condition is None:
            return len(items) > 0
        return any(_matches(reader, item, expr.condition, expr.element_spec) for item in items)
    if isinstance(expr, In):
        value = _resolve(reader, data, expr.column, spec)
        if value is None:
            return None
        return value in expr.values
    if isinstance(expr, Contains):
        # A plain substring test -- unlike `Like`'s SQL-pattern matching
        # (`_like_to_regex`), `expr.value` (when a literal) has no wildcard
        # characters to reinterpret, so a direct (case-insensitive, like
        # `_like_to_regex`) Python `in` check is both
        # correct and simpler than routing through the LIKE/regex machinery.
        # `expr.value` can also be a `JsonPath`/`RelatedObject` -- a
        # field-vs-field substring test -- resolved the same way `expr.column`
        # is; a missing needle (as opposed to a missing haystack) is just as
        # much "unknown" as a missing haystack, so it collapses to the same
        # `None` (SQL's `LIKE` against a NULL pattern is `UNKNOWN` too).
        value = _resolve(reader, data, expr.column, spec)
        if value is None:
            return None
        if isinstance(expr.value, (JsonPath, RelatedObject, FlatColumnRef)):
            substring = _resolve(reader, data, expr.value, spec)
            if substring is None:
                return None
        else:
            substring = expr.value
        return str(substring).lower() in str(value).lower()
    if isinstance(expr, Comparison):
        left = _resolve(reader, data, expr.column, spec)
        if isinstance(expr.value, (JsonPath, RelatedObject, FlatColumnRef)):
            right = _resolve(reader, data, expr.value, spec)
        else:
            right = expr.value
        return _compare(expr.op, left, right)
    raise TypeError(f"unsupported where expression: {expr!r}")


def evaluate_where(db: Any, obj: Any, expr: Any, spec: ObjectTypeSpec) -> bool:
    """`query.py`'s `.compile()` tree, evaluated in Python against a record
    instead of rendered as SQL. See module docstring for why no privacy
    guard is needed here the way `Comparison.compile()` needs one.

    `obj` and `db` are as for `resolve_column_ref`.

    Delegates the actual recursion to `_evaluate_tri`, which tracks SQL's
    three-valued logic (`True`/`False`/`UNKNOWN`) rather than a plain
    `bool` -- collapsed to a real `bool` here, exactly once, the same way
    a SQL `WHERE` clause only keeps rows that are definitely `True`,
    treating `UNKNOWN` the same as `False`.
    """
    return _matches(_reader(db, obj), obj, expr, spec)


def _matches(reader: RecordReader, data: Any, expr: Any, spec: ObjectTypeSpec) -> bool:
    return data is not None and _evaluate_tri(reader, data, expr, spec) is True
