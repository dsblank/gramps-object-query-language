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

"""Tests for the Person query AST and SQL compiler (`gramps_webapi.api.query`)."""

import pytest

from gramps_object_query_language.query import (
    CITATION,
    EVENT,
    FAMILY,
    MEDIA,
    NOTE,
    PERSON,
    PLACE,
    REPOSITORY,
    SOURCE,
    TAG,
    And,
    Collection,
    CollectionCount,
    ColumnIndex,
    Contains,
    Dialect,
    Eq,
    Exists,
    FlatColumnRef,
    Gt,
    Gte,
    In,
    JsonPath,
    Like,
    Lt,
    Lte,
    Ne,
    Not,
    Or,
    OrderBy,
    Query,
    QueryError,
    RelatedObject,
    after_columns,
    compile_count_query,
    compile_query,
    resolve_collection,
    resolve_column_path,
)


def test_person_columns_include_expected_flat_fields():
    assert {"handle", "gramps_id", "gender", "private", "given_name", "surname"} <= (
        PERSON.columns
    )


def test_compile_count_query_shape():
    query = Query(where=Eq("gender", 1))
    sql, params = compile_count_query(PERSON, query)
    assert sql == "SELECT COUNT(*) FROM person WHERE (gender IS NOT DISTINCT FROM ?)"
    assert params == [1]


def test_compile_count_query_ignores_select_order_by_limit_after():
    # A count has no columns, sort order, or page -- only `where` should
    # affect it, matching total rows across the whole result set.
    query = Query(
        select=["handle", "surname"],
        order_by=[OrderBy("surname", "desc")],
        limit=5,
        after=("Smith", "h1"),
    )
    sql, params = compile_count_query(PERSON, query)
    assert sql == "SELECT COUNT(*) FROM person"
    assert params == []


def test_compile_count_query_no_where_no_params():
    sql, params = compile_count_query(PERSON, Query())
    assert sql == "SELECT COUNT(*) FROM person"
    assert params == []


# --- treeid scoping (SharedPostgreSQL multi-tree isolation) -----------------
#
# SharedPostgreSQL stores every tree's rows in the same physical tables,
# distinguished only by a `treeid` column that's part of every table's
# primary key. Nothing applies this filter automatically -- without it,
# these queries would return rows from every tree sharing the instance, not
# just the caller's own.


def test_compile_query_omits_treeid_clause_by_default():
    # Single-tree-per-database backends (SQLite, single-user PostgreSQL)
    # have no `treeid` column at all -- omitting `treeid` must not add a
    # clause referencing a column that doesn't exist there.
    sql, params = compile_query(PERSON, Query())
    assert "treeid" not in sql
    assert None not in params


def test_compile_query_adds_treeid_clause_when_given():
    sql, params = compile_query(PERSON, Query(), treeid=7)
    assert "treeid = ?" in sql
    assert 7 in params


def test_compile_query_treeid_clause_combines_with_where():
    query = Query(where=Eq("gender", 1))
    sql, params = compile_query(PERSON, query, treeid=7)
    assert "(gender IS NOT DISTINCT FROM ?)" in sql
    assert "treeid = ?" in sql
    assert params[0] == 1  # where value
    assert 7 in params[1:-1]  # treeid, before LIMIT
    assert params[-1] == query.limit


def test_compile_count_query_adds_treeid_clause_when_given():
    sql, params = compile_count_query(PERSON, Query(), treeid=7)
    assert sql == "SELECT COUNT(*) FROM person WHERE treeid = ?"
    assert params == [7]


def test_compile_count_query_omits_treeid_clause_by_default():
    sql, params = compile_count_query(PERSON, Query())
    assert "treeid" not in sql
    assert params == []


# --- JsonPath -----------------------------------------------------------


def test_jsonpath_requires_at_least_one_segment():
    with pytest.raises(QueryError):
        JsonPath(())


def test_jsonpath_rejects_invalid_segment_types():
    with pytest.raises(QueryError):
        JsonPath(("primary_name", 1.5))  # float
    with pytest.raises(QueryError):
        JsonPath(("primary_name", None))
    with pytest.raises(QueryError):
        JsonPath(("primary_name", True))  # bool is an int subclass -- rejected anyway


def test_jsonpath_accepts_str_and_int_segments():
    path = JsonPath(("primary_name", "surname_list", 0, "surname"))
    assert path.segments == ("primary_name", "surname_list", 0, "surname")
    assert path.base_column == "json_data"


def test_compile_query_jsonpath_without_dialect_raises():
    path = JsonPath(("primary_name", "first_name"))
    with pytest.raises(QueryError):
        compile_query(PERSON, Query(select=["handle", path]))


def test_compile_query_jsonpath_select_sqlite():
    path = JsonPath(("primary_name", "surname_list", 0, "surname"))
    sql, params = compile_query(PERSON, Query(select=["handle", path]), dialect=Dialect.SQLITE)
    assert "json_extract(json_data, ?)" in sql
    assert params[0] == "$.primary_name.surname_list[0].surname"


def test_compile_query_jsonpath_select_postgresql():
    path = JsonPath(("primary_name", "surname_list", 0, "surname"))
    sql, params = compile_query(
        PERSON, Query(select=["handle", path]), dialect=Dialect.POSTGRESQL
    )
    assert "jsonb_extract_path_text(json_data::jsonb, ?, ?, ?, ?)" in sql
    assert params[:4] == ["primary_name", "surname_list", "0", "surname"]


def test_compile_query_jsonpath_where_eq():
    path = JsonPath(("primary_name", "first_name"))
    query = Query(select=["handle"], where=Eq(path, "Root"))
    sql, params = compile_query(PERSON, query, dialect=Dialect.SQLITE)
    assert "json_extract(json_data, ?) IS NOT DISTINCT FROM ?" in sql
    assert params == ["$.primary_name.first_name", "Root", 50]


def test_compile_query_jsonpath_where_in():
    path = JsonPath(("gender",))
    query = Query(select=["handle"], where=In(path, [1, 2]))
    sql, params = compile_query(PERSON, query, dialect=Dialect.SQLITE)
    assert "json_extract(json_data, ?) IN (?, ?)" in sql
    assert params == ["$.gender", 1, 2, 50]


def test_compile_query_jsonpath_combined_with_plain_column():
    path = JsonPath(("primary_name", "first_name"))
    query = Query(select=["handle"], where=And(Eq("gender", 1), Eq(path, "Root")))
    sql, params = compile_query(PERSON, query, dialect=Dialect.SQLITE)
    assert "gender IS NOT DISTINCT FROM ?" in sql
    assert "json_extract(json_data, ?) IS NOT DISTINCT FROM ?" in sql
    # plain-column param first, then the JsonPath's own [path, value] pair --
    # matches left-to-right order of appearance in the compiled SQL text.
    assert params == [1, "$.primary_name.first_name", "Root", 50]


def test_compile_query_jsonpath_select_params_precede_where_params():
    # SELECT appears before WHERE in the compiled SQL text, so a JsonPath in
    # `select` must contribute its params before any `where` params.
    select_path = JsonPath(("primary_name", "first_name"))
    where_path = JsonPath(("gender",))
    query = Query(select=["handle", select_path], where=Eq(where_path, 1))
    sql, params = compile_query(PERSON, query, dialect=Dialect.SQLITE)
    assert params[0] == "$.primary_name.first_name"  # select path
    assert params[1] == "$.gender"  # where path
    assert params[2] == 1  # where value
    assert params[-1] == query.limit  # LIMIT is always last


def test_compile_count_query_jsonpath_where():
    path = JsonPath(("primary_name", "first_name"))
    sql, params = compile_count_query(
        PERSON, Query(where=Eq(path, "Root")), dialect=Dialect.SQLITE
    )
    assert sql == (
        "SELECT COUNT(*) FROM person WHERE "
        "(json_extract(json_data, ?) IS NOT DISTINCT FROM ?)"
    )
    assert params == ["$.primary_name.first_name", "Root"]  # no LIMIT param -- it's a COUNT


def test_compile_query_jsonpath_where_gt_numeric_postgresql_casts_to_numeric():
    # jsonb_extract_path_text always returns TEXT -- comparing TEXT with `>`
    # is lexicographic ('10' < '9'), so a numeric `value` must use the
    # non-`_text` extractor + an explicit CAST instead.
    path = JsonPath(("attribute_list", 0, "value"))
    query = Query(select=["handle"], where=Gt(path, 5))
    sql, params = compile_query(PERSON, query, dialect=Dialect.POSTGRESQL)
    assert "CAST(jsonb_extract_path(json_data::jsonb, ?, ?, ?) AS NUMERIC) > ?" in sql
    assert params == ["attribute_list", "0", "value", 5, 50]


def test_compile_query_jsonpath_where_eq_bool_postgresql_casts_to_boolean():
    path = JsonPath(("private",))
    query = Query(select=["handle"], where=Eq(path, True))
    sql, params = compile_query(PERSON, query, dialect=Dialect.POSTGRESQL)
    assert "CAST(jsonb_extract_path(json_data::jsonb, ?) AS BOOLEAN) IS NOT DISTINCT FROM ?" in sql
    assert params == ["private", True, 50]


def test_compile_query_jsonpath_where_eq_str_postgresql_stays_text():
    path = JsonPath(("primary_name", "first_name"))
    query = Query(select=["handle"], where=Eq(path, "Root"))
    sql, params = compile_query(PERSON, query, dialect=Dialect.POSTGRESQL)
    assert "jsonb_extract_path_text(json_data::jsonb, ?, ?) IS NOT DISTINCT FROM ?" in sql
    assert params == ["primary_name", "first_name", "Root", 50]


def test_compile_query_jsonpath_select_unaffected_by_value_casting():
    # SELECT entries have no comparison value -- always text extraction,
    # same as before this cast logic was added.
    path = JsonPath(("attribute_list", 0, "value"))
    sql, params = compile_query(PERSON, Query(select=[path]), dialect=Dialect.POSTGRESQL)
    assert "jsonb_extract_path_text(json_data::jsonb, ?, ?, ?)" in sql


def test_compile_query_jsonpath_where_in_numeric_postgresql_casts_to_numeric():
    path = JsonPath(("attribute_list", 0, "value"))
    query = Query(select=["handle"], where=In(path, [1, 2]))
    sql, params = compile_query(PERSON, query, dialect=Dialect.POSTGRESQL)
    assert "CAST(jsonb_extract_path(json_data::jsonb, ?, ?, ?) AS NUMERIC) IN (?, ?)" in sql
    assert params == ["attribute_list", "0", "value", 1, 2, 50]


def test_jsonpath_not_subject_to_column_whitelist():
    # JsonPath's safety comes from segment-level type checking + parameter
    # binding, not the fixed column whitelist -- any path is structurally
    # valid, unlike an unrecognized plain column name.
    path = JsonPath(("anything", "goes", "here"))
    sql, params = compile_query(PERSON, Query(select=[path]), dialect=Dialect.SQLITE)
    assert "json_extract" in sql


# --- ColumnIndex / RelatedObject / resolve_column_path -------------------------

BIRTH_DATE = resolve_column_path(PERSON, ["birth", "date"])
DEATH_DATE = resolve_column_path(PERSON, ["death", "date"])
BIRTH_DATE_SORTVAL = resolve_column_path(PERSON, ["birth", "date", "sortval"])
DEATH_DATE_SORTVAL = resolve_column_path(PERSON, ["death", "date", "sortval"])
FATHER_SURNAME = resolve_column_path(FAMILY, ["father", "surname"])
MOTHER_SURNAME = resolve_column_path(FAMILY, ["mother", "surname"])
BIRTH_PLACE_TITLE = resolve_column_path(PERSON, ["birth", "place", "title"])


def test_resolve_column_path_flat_column():
    assert resolve_column_path(PERSON, ["gender"]) == "gender"


def test_resolve_column_path_plain_json_path_no_relationship():
    result = resolve_column_path(PERSON, ["primary_name", "first_name"])
    assert result == JsonPath(("primary_name", "first_name"))


def test_resolve_column_path_empty_raises():
    with pytest.raises(QueryError):
        resolve_column_path(PERSON, [])


def test_resolve_column_path_bare_relationship_name_rejected():
    # "birth" alone isn't a value -- needs a further path.
    with pytest.raises(QueryError):
        resolve_column_path(PERSON, ["birth"])


def test_resolve_column_path_birth_date_shape():
    assert isinstance(BIRTH_DATE, RelatedObject)
    assert BIRTH_DATE.name == "birth"
    assert BIRTH_DATE.target is EVENT
    assert BIRTH_DATE.handle_ref == JsonPath(
        ("event_ref_list", ColumnIndex("birth_ref_index"), "ref")
    )
    assert BIRTH_DATE.field == JsonPath(("date",))


def test_resolve_column_path_father_surname_shape():
    # A direct foreign key (father_handle), not a dynamic index -- handle_ref
    # is a plain string, not a JsonPath.
    assert isinstance(FATHER_SURNAME, RelatedObject)
    assert FATHER_SURNAME.name == "father"
    assert FATHER_SURNAME.target is PERSON
    assert FATHER_SURNAME.handle_ref == "father_handle"
    assert FATHER_SURNAME.field == "surname"


def test_resolve_column_path_two_hop_chain():
    # birth.place.title: Person -> Event (dynamic index) -> Place (direct FK).
    assert isinstance(BIRTH_PLACE_TITLE, RelatedObject)
    assert BIRTH_PLACE_TITLE.name == "birth"
    assert BIRTH_PLACE_TITLE.target is EVENT
    inner = BIRTH_PLACE_TITLE.field
    assert isinstance(inner, RelatedObject)
    assert inner.name == "place"
    assert inner.target is PLACE
    assert inner.handle_ref == "place"
    assert inner.field == "title"


def test_resolve_column_path_no_relationships_on_place():
    # PLACE has no registered relationships -- a path through it just
    # resolves as a flat column or JsonPath, same as any other type.
    assert resolve_column_path(PLACE, ["title"]) == "title"


# --- RelatedObject rendering (select) -------------------------------------------


def test_related_object_requires_dialect():
    with pytest.raises(QueryError):
        compile_query(PERSON, Query(select=["handle", BIRTH_DATE]))


def test_related_object_not_a_relationship_on_wrong_spec_falls_through_to_json_path():
    # "birth" is only a registered relationship on Person, not Event -- on
    # Event it's just an arbitrary (harmless, matches-nothing-at-runtime)
    # JsonPath segment, not an error. Only a bare relationship name with no
    # further path is rejected (see test_resolve_column_path_bare_relationship_name_rejected).
    result = resolve_column_path(EVENT, ["birth", "date"])
    assert result == JsonPath(("birth", "date"))


def test_related_object_sqlite_shape():
    sql, params = compile_query(
        PERSON, Query(select=["handle", BIRTH_DATE], limit=10), dialect=Dialect.SQLITE
    )
    assert "FROM person" in sql
    # Correlated subquery, not a JOIN -- the outer FROM stays single-table.
    assert "JOIN" not in sql
    # The subquery's own FROM event scopes json_data unambiguously without
    # needing an explicit "event." qualifier.
    assert "SELECT json_extract(json_data, ?) FROM event" in sql
    assert "person.birth_ref_index >= 0" in sql
    assert (
        "json_extract(person.json_data, '$.event_ref_list[' || "
        "person.birth_ref_index || '].ref')" in sql
    )
    # The field extraction's own path ('$.date', parameterized via the
    # shared _render_json_path -- an improvement over the old bespoke
    # inline-literal rendering) precedes the trailing LIMIT param.
    assert params == ["$.date", 10]


def test_related_object_postgresql_shape():
    sql, params = compile_query(
        PERSON, Query(select=["handle", BIRTH_DATE], limit=10), dialect=Dialect.POSTGRESQL
    )
    assert "jsonb_extract_path_text(json_data::jsonb, ?)" in sql
    assert (
        "person.json_data::jsonb -> 'event_ref_list' -> person.birth_ref_index ->> 'ref'"
        in sql
    )
    assert "date" in params


def test_related_object_father_surname_sqlite_shape():
    # A direct FK (father_handle) needs no CASE WHEN guard at all -- NULL
    # already fails the handle equality naturally. The target table is
    # aliased (person__hop0) unconditionally, even here where "person"
    # doesn't collide with anything -- see FlatColumnRef-adjacent note in
    # _render_related_object's docstring: a self-referencing relationship
    # (Place.enclosed_by) needs this same aliasing to be correct, so every
    # RelatedObject gets it rather than special-casing self-reference.
    sql, params = compile_query(
        FAMILY, Query(select=["handle", FATHER_SURNAME]), dialect=Dialect.SQLITE
    )
    assert "CASE WHEN" not in sql
    assert (
        "SELECT surname FROM person AS person__hop0 "
        "WHERE person__hop0.handle = (family.father_handle)"
    ) in sql


def test_related_object_sibling_subqueries_same_table_no_conflict():
    # father and mother both correlate to "person", unaliased -- confirmed
    # live this doesn't collide since each subquery is an independent scope.
    sql, params = compile_query(
        FAMILY, Query(select=["handle", FATHER_SURNAME, MOTHER_SURNAME]), dialect=Dialect.SQLITE
    )
    assert sql.count("FROM person") == 2
    assert "family.father_handle" in sql
    assert "family.mother_handle" in sql


def test_related_object_two_hop_chain_sqlite_shape():
    sql, params = compile_query(
        PERSON, Query(select=["handle", BIRTH_PLACE_TITLE]), dialect=Dialect.SQLITE
    )
    # Nested subquery: event lookup contains a place lookup -- each hop gets
    # its own alias (event__hop0, place__hop1), incrementing with depth.
    assert (
        "SELECT (SELECT title FROM place AS place__hop1 "
        "WHERE place__hop1.handle = (event__hop0.place)"
    ) in sql
    assert "FROM event AS event__hop0 WHERE event__hop0.handle = (CASE WHEN person.birth_ref_index" in sql


def test_related_object_treeid_applies_to_subquery():
    sql, params = compile_query(
        PERSON,
        Query(select=["handle", BIRTH_DATE, DEATH_DATE]),
        dialect=Dialect.SQLITE,
        treeid=7,
    )
    # Each subquery contributes its own field-extraction param ('$.date')
    # before its own treeid param, plus the outer query's own treeid clause,
    # plus the trailing LIMIT param.
    assert params == ["$.date", 7, "$.date", 7, 7, 50]
    assert sql.count("__hop0.treeid = ?") == 2


def test_related_object_death_ref_index():
    sql, params = compile_query(
        PERSON, Query(select=["handle", DEATH_DATE]), dialect=Dialect.SQLITE
    )
    assert "person.death_ref_index >= 0" in sql
    assert "person.birth_ref_index" not in sql


def test_related_object_end_to_end_sqlite_execution():
    # Not just "does it compile" -- does it actually run correctly,
    # including picking the right event_ref_list entry (index 1, not 0)
    # via the *dynamic* per-row index, and correctly returning null for a
    # ref_index of -1 (no such event recorded).
    import json
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE person (handle TEXT, birth_ref_index INTEGER, "
        "death_ref_index INTEGER, json_data TEXT)"
    )
    conn.execute("CREATE TABLE event (handle TEXT, json_data TEXT)")
    person_json = json.dumps(
        {
            "event_ref_list": [
                {"ref": "evt-other", "role": {"value": 3}},
                {"ref": "evt-birth", "role": {"value": 1}},
            ]
        }
    )
    conn.execute(
        "INSERT INTO person VALUES (?, ?, ?, ?)", ("p1", 1, -1, person_json)
    )
    conn.execute(
        "INSERT INTO event VALUES (?, ?)",
        ("evt-birth", json.dumps({"date": {"sortval": 2439857}})),
    )
    conn.execute(
        "INSERT INTO event VALUES (?, ?)",
        ("evt-other", json.dumps({"date": {"sortval": 999}})),
    )

    sql, params = compile_query(
        PERSON,
        Query(select=["handle", BIRTH_DATE, DEATH_DATE], limit=10),
        dialect=Dialect.SQLITE,
    )
    row = conn.execute(sql, params).fetchone()
    assert row[0] == "p1"
    assert json.loads(row[1]) == {"sortval": 2439857}  # correct entry, index 1
    assert row[2] is None  # death_ref_index == -1


def test_related_object_father_surname_end_to_end_sqlite_execution():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE person (handle TEXT, surname TEXT)")
    conn.execute("CREATE TABLE family (handle TEXT, father_handle TEXT, mother_handle TEXT)")
    conn.execute("INSERT INTO person VALUES ('p1', 'Smith')")
    conn.execute("INSERT INTO person VALUES ('p2', 'Jones')")
    conn.execute("INSERT INTO family VALUES ('f1', 'p1', NULL)")  # father=Smith
    conn.execute("INSERT INTO family VALUES ('f2', 'p2', NULL)")  # father=Jones
    conn.execute("INSERT INTO family VALUES ('f3', NULL, NULL)")  # no father

    sql, params = compile_query(
        FAMILY,
        Query(select=["handle"], where=Eq(FATHER_SURNAME, "Smith")),
        dialect=Dialect.SQLITE,
    )
    rows = conn.execute(sql, params).fetchall()
    assert rows == [("f1",)]


# --- RelatedObject in WHERE (BIRTH_DATE_SORTVAL / DEATH_DATE_SORTVAL) -----------


def test_related_object_sortval_where_sqlite_shape():
    sql, params = compile_query(
        PERSON,
        Query(select=["handle"], where=Gte(BIRTH_DATE_SORTVAL, 2439857)),
        dialect=Dialect.SQLITE,
    )
    assert "JOIN" not in sql
    assert "json_extract(json_data, ?)" in sql
    assert params == ["$.date.sortval", 2439857, 50]


def test_related_object_sortval_where_postgresql_numeric_cast():
    # Same numeric-cast correctness issue JsonPath already had: ->>'sortval'
    # is TEXT on PostgreSQL, which compares lexicographically, not
    # numerically -- must use -> + CAST(...AS NUMERIC) for a Gte/Lt/etc.
    sql, params = compile_query(
        PERSON,
        Query(select=["handle"], where=Gte(BIRTH_DATE_SORTVAL, 2439857)),
        dialect=Dialect.POSTGRESQL,
    )
    assert "CAST(jsonb_extract_path(json_data::jsonb, ?, ?) AS NUMERIC)" in sql
    assert "jsonb_extract_path_text" not in sql
    assert params[:2] == ["date", "sortval"]


def test_related_object_sortval_select_unaffected_by_where_addition():
    # BIRTH_DATE (select, whole struct) must render exactly as before --
    # value=None still takes the original (non-cast) code path.
    sql, params = compile_query(
        PERSON, Query(select=["handle", BIRTH_DATE]), dialect=Dialect.POSTGRESQL
    )
    assert "jsonb_extract_path_text(json_data::jsonb, ?)" in sql
    assert "CAST" not in sql


def test_related_object_sortval_range_query():
    query = Query(
        select=["handle"],
        where=And(Gte(BIRTH_DATE_SORTVAL, 2439857), Lt(BIRTH_DATE_SORTVAL, 2440222)),
    )
    sql, params = compile_query(PERSON, query, dialect=Dialect.SQLITE)
    assert sql.count("SELECT json_extract(json_data, ?) FROM event") == 2
    assert params == ["$.date.sortval", 2439857, "$.date.sortval", 2440222, 50]


def test_related_object_sortval_treeid_scoping():
    sql, params = compile_query(
        PERSON,
        Query(select=["handle"], where=Gte(BIRTH_DATE_SORTVAL, 2439857)),
        dialect=Dialect.SQLITE,
        treeid=7,
    )
    assert "event__hop0.treeid = ?" in sql
    assert 7 in params


def test_related_object_sortval_end_to_end_sqlite_execution():
    import json
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE person (handle TEXT, birth_ref_index INTEGER, json_data TEXT)"
    )
    conn.execute("CREATE TABLE event (handle TEXT, json_data TEXT)")
    conn.execute(
        "INSERT INTO person VALUES (?, ?, ?)",
        ("p1", 0, json.dumps({"event_ref_list": [{"ref": "e1"}]})),
    )
    conn.execute(
        "INSERT INTO person VALUES (?, ?, ?)",
        ("p2", 0, json.dumps({"event_ref_list": [{"ref": "e2"}]})),
    )
    conn.execute(
        "INSERT INTO event VALUES (?, ?)",
        ("e1", json.dumps({"date": {"sortval": 2439857}})),  # 1968 -- matches
    )
    conn.execute(
        "INSERT INTO event VALUES (?, ?)",
        ("e2", json.dumps({"date": {"sortval": 2415021}})),  # 1900 -- doesn't
    )
    sql, params = compile_query(
        PERSON,
        Query(select=["handle"], where=Gte(BIRTH_DATE_SORTVAL, 2439857)),
        dialect=Dialect.SQLITE,
    )
    rows = conn.execute(sql, params).fetchall()
    assert rows == [("p1",)]


# --- Field-vs-field comparisons (e.g. "mother died before father") -------------

MOTHER_DEATH_SORTVAL = resolve_column_path(FAMILY, ["mother", "death", "date", "sortval"])
FATHER_DEATH_SORTVAL = resolve_column_path(FAMILY, ["father", "death", "date", "sortval"])


def test_field_vs_field_sqlite_shape():
    sql, params = compile_query(
        FAMILY,
        Query(select=["handle"], where=Lt(MOTHER_DEATH_SORTVAL, FATHER_DEATH_SORTVAL)),
        dialect=Dialect.SQLITE,
    )
    assert "JOIN" not in sql
    # Both sides are independently-rendered correlated subqueries, joined
    # by the operator directly -- no ? placeholder between them.
    assert sql.count("SELECT (SELECT json_extract(json_data, ?)") == 2
    assert "family.mother_handle" in sql
    assert "family.father_handle" in sql
    assert " < " in sql
    assert params == ["$.date.sortval", "$.date.sortval", 50]


def test_field_vs_field_postgresql_numeric_cast_both_sides():
    sql, params = compile_query(
        FAMILY,
        Query(select=["handle"], where=Lt(MOTHER_DEATH_SORTVAL, FATHER_DEATH_SORTVAL)),
        dialect=Dialect.POSTGRESQL,
    )
    # Ordering comparison between two paths: both sides get the numeric
    # cast (via a dummy int hint, since there's no literal runtime value
    # to infer it from) -- not the default TEXT extraction, which would
    # compare lexicographically.
    assert sql.count("CAST(jsonb_extract_path(json_data::jsonb, ?, ?) AS NUMERIC)") == 2
    assert "jsonb_extract_path_text" not in sql


def test_field_vs_field_equality_stays_text_both_sides():
    # Eq/Ne don't need the numeric-cast hint -- exact TEXT match is correct
    # regardless of whether the underlying value is numeric or textual, as
    # long as both sides extract the same way (they do).
    sql, params = compile_query(
        FAMILY,
        Query(select=["handle"], where=Eq(MOTHER_DEATH_SORTVAL, FATHER_DEATH_SORTVAL)),
        dialect=Dialect.POSTGRESQL,
    )
    assert sql.count("jsonb_extract_path_text(json_data::jsonb, ?, ?)") == 2
    assert "CAST" not in sql


def test_field_vs_field_two_hop_chain():
    # birth.place.title compared against a literal still works fine
    # alongside field-vs-field elsewhere -- confirms the two mechanisms
    # (field-vs-value and field-vs-field) don't interfere.
    birth_place_title = resolve_column_path(PERSON, ["birth", "place", "title"])
    sql, params = compile_query(
        PERSON,
        Query(select=["handle"], where=Eq(birth_place_title, "Chicago")),
        dialect=Dialect.SQLITE,
    )
    assert params[-2:] == ["Chicago", 50]


def test_field_vs_field_treeid_applies_independently_to_each_side():
    sql, params = compile_query(
        FAMILY,
        Query(select=["handle"], where=Lt(MOTHER_DEATH_SORTVAL, FATHER_DEATH_SORTVAL)),
        dialect=Dialect.SQLITE,
        treeid=7,
    )
    # Each side's Event *and* Person subqueries get their own treeid clause
    # (2 chains x 2 hops = 4), plus the outer family query's own -- 5 total,
    # confirmed live rather than assumed.
    assert sql.count("treeid = ?") == 5


def test_field_vs_field_end_to_end_sqlite_execution():
    import json
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE person (handle TEXT, death_ref_index INTEGER, json_data TEXT)"
    )
    conn.execute("CREATE TABLE event (handle TEXT, json_data TEXT)")
    conn.execute("CREATE TABLE family (handle TEXT, father_handle TEXT, mother_handle TEXT)")

    # f1: mother died 1950 (earlier), father died 1980 (later) -- mother < father matches
    conn.execute(
        "INSERT INTO person VALUES (?, ?, ?)",
        ("mom1", 0, json.dumps({"event_ref_list": [{"ref": "mom1_death"}]})),
    )
    conn.execute(
        "INSERT INTO person VALUES (?, ?, ?)",
        ("dad1", 0, json.dumps({"event_ref_list": [{"ref": "dad1_death"}]})),
    )
    conn.execute(
        "INSERT INTO event VALUES (?, ?)", ("mom1_death", json.dumps({"date": {"sortval": 2433283}}))
    )
    conn.execute(
        "INSERT INTO event VALUES (?, ?)", ("dad1_death", json.dumps({"date": {"sortval": 2444239}}))
    )
    conn.execute("INSERT INTO family VALUES (?, ?, ?)", ("f1", "dad1", "mom1"))

    # f2: mother died 1990 (later), father died 1960 (earlier) -- doesn't match
    conn.execute(
        "INSERT INTO person VALUES (?, ?, ?)",
        ("mom2", 0, json.dumps({"event_ref_list": [{"ref": "mom2_death"}]})),
    )
    conn.execute(
        "INSERT INTO person VALUES (?, ?, ?)",
        ("dad2", 0, json.dumps({"event_ref_list": [{"ref": "dad2_death"}]})),
    )
    conn.execute(
        "INSERT INTO event VALUES (?, ?)", ("mom2_death", json.dumps({"date": {"sortval": 2447893}}))
    )
    conn.execute(
        "INSERT INTO event VALUES (?, ?)", ("dad2_death", json.dumps({"date": {"sortval": 2436935}}))
    )
    conn.execute("INSERT INTO family VALUES (?, ?, ?)", ("f2", "dad2", "mom2"))

    sql, params = compile_query(
        FAMILY,
        Query(select=["handle"], where=Lt(MOTHER_DEATH_SORTVAL, FATHER_DEATH_SORTVAL)),
        dialect=Dialect.SQLITE,
    )
    rows = conn.execute(sql, params).fetchall()
    assert rows == [("f1",)]


# --- FlatColumnRef: field-vs-field between two plain flat columns -------------
#
# Every field-vs-field test above crosses a relationship or a JSON path
# (RelatedObject/JsonPath) -- both unambiguous field references already.
# Two flat columns on the *same* table (e.g. "given_name == surname") is the
# one case where the natural translation, a bare `str`, is indistinguishable
# from an ordinary literal (Eq("surname", "Smith") is exactly as valid) --
# a real, pre-existing bug (found while adding Contains's field-vs-field
# support): without FlatColumnRef, "given_name == surname" silently compiled
# to comparing given_name against the *literal text* "surname", never
# caught because no existing test/doc example happened to compare two bare
# flat columns directly.


def test_flat_column_field_vs_field_renders_as_column_not_literal():
    sql, params = compile_query(
        PERSON,
        Query(select=["handle"], where=Eq("given_name", FlatColumnRef("surname"))),
        dialect=Dialect.SQLITE,
    )
    assert "given_name IS NOT DISTINCT FROM surname" in sql
    # No bound parameter for "surname" -- it's a column reference, not a value.
    assert "surname" not in params


def test_bare_str_value_still_treated_as_literal_not_flat_column_field():
    # The critical distinction FlatColumnRef exists to draw: Eq("surname",
    # "Smith") (an ordinary literal comparison) must be completely unaffected.
    sql, params = compile_query(
        PERSON, Query(select=["handle"], where=Eq("surname", "Smith")), dialect=Dialect.SQLITE
    )
    assert "surname IS NOT DISTINCT FROM ?" in sql
    assert params[0] == "Smith"


def test_flat_column_field_vs_field_end_to_end_sqlite_execution():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE person (handle TEXT, given_name TEXT, surname TEXT)")
    # p1: given_name and surname genuinely equal -- matches. p2: same
    # given_name text as p1's *surname*, but its own surname differs -- must
    # NOT match (proves the comparison is column-vs-column, not
    # column-vs-the-literal-text-"surname").
    conn.execute("INSERT INTO person VALUES ('p1', 'Same', 'Same')")
    conn.execute("INSERT INTO person VALUES ('p2', 'surname', 'Different')")
    sql, params = compile_query(
        PERSON,
        Query(select=["handle"], where=Eq("given_name", FlatColumnRef("surname"))),
        dialect=Dialect.SQLITE,
    )
    rows = conn.execute(sql, params).fetchall()
    assert rows == [("p1",)]


def test_flat_column_field_vs_field_sql_matches_evaluator():
    # The same regression-guard pattern used for evaluate_where's
    # Not/missing-value fix (see ROADMAP.md's Done section) -- run the same
    # AST through the real SQLite compiler and through evaluate_where
    # against equivalent data, and assert they agree, rather than trusting
    # either path in isolation.
    import sqlite3

    from gramps_object_query_language.evaluator import evaluate_where

    class _FakeSurname:
        def __init__(self, surname):
            self.surname = surname

    class _FakeName:
        def __init__(self, given_name, surname):
            self._given_name = given_name
            self._surname = surname

        def get_first_name(self):
            return self._given_name

        def get_surname_list(self):
            return [_FakeSurname(self._surname)]

    class _FakePerson:
        def __init__(self, given_name, surname):
            self._name = _FakeName(given_name, surname)

        def get_primary_name(self):
            return self._name

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE person (handle TEXT, given_name TEXT, surname TEXT)")
    conn.execute("INSERT INTO person VALUES ('p1', 'Same', 'Same')")
    conn.execute("INSERT INTO person VALUES ('p2', 'surname', 'Different')")
    where = Eq("given_name", FlatColumnRef("surname"))
    sql, params = compile_query(
        PERSON, Query(select=["handle"], where=where), dialect=Dialect.SQLITE
    )
    sql_matches = {row[0] for row in conn.execute(sql, params).fetchall()}

    people = {"p1": _FakePerson("Same", "Same"), "p2": _FakePerson("surname", "Different")}
    evaluator_matches = {
        handle for handle, obj in people.items() if evaluate_where(None, obj, where, PERSON)
    }
    assert sql_matches == evaluator_matches == {"p1"}


# --- NULL-safe equality (`=`/`!=` -> IS [NOT] DISTINCT FROM) ------------------
#
# Plain SQL `=`/`!=` use three-valued logic: if either side is NULL, the
# comparison is UNKNOWN, not TRUE or FALSE -- so a row where one side is
# missing satisfies neither `eq` nor `ne`. That's a sharp edge for
# field-vs-field comparisons specifically: "born and died in different
# places" (`birth.place.title != death.place.title`) should include "died
# in an unknown place", not silently drop it. `Eq`/`Ne` render as
# `IS [NOT] DISTINCT FROM` instead -- NULL-safe, so NULL is a normal,
# comparable value (NULL IS DISTINCT FROM 'x' is true; NULL IS DISTINCT
# FROM NULL is false) -- for both literal and field-vs-field comparisons.


def test_eq_ne_render_as_null_safe_distinct():
    sql, _ = compile_query(PERSON, Query(select=["handle"], where=Eq("gender", 1)))
    assert "gender IS NOT DISTINCT FROM ?" in sql
    assert "gender = ?" not in sql

    sql, _ = compile_query(PERSON, Query(select=["handle"], where=Ne("gender", 1)))
    assert "gender IS DISTINCT FROM ?" in sql
    assert "gender != ?" not in sql


def test_field_vs_field_eq_ne_render_as_null_safe_distinct():
    sql, _ = compile_query(
        FAMILY,
        Query(select=["handle"], where=Eq(MOTHER_DEATH_SORTVAL, FATHER_DEATH_SORTVAL)),
        dialect=Dialect.SQLITE,
    )
    # The two correlated subqueries are joined directly by the top-level
    # operator, e.g. "...LIMIT 1) IS NOT DISTINCT FROM (SELECT..." -- check
    # that specific join, not just "IS NOT DISTINCT FROM" appearing
    # somewhere (the subqueries' own internal `handle = ...` correlations
    # are unrelated `=` uses that must NOT be affected by this rewrite).
    assert ") IS NOT DISTINCT FROM (SELECT" in sql

    sql, _ = compile_query(
        FAMILY,
        Query(select=["handle"], where=Ne(MOTHER_DEATH_SORTVAL, FATHER_DEATH_SORTVAL)),
        dialect=Dialect.SQLITE,
    )
    assert ") IS DISTINCT FROM (SELECT" in sql
    assert "!=" not in sql


def test_ordering_and_like_and_in_ops_unaffected_by_null_safe_rewrite():
    # Only `=`/`!=` (Eq/Ne) get the NULL-safe rewrite -- ordering operators
    # have no natural NULL-safe equivalent in standard SQL, and In/Like are
    # unrelated classes that don't go through Comparison.compile() at all
    # (In) or use their own fixed `LIKE` operator (Like).
    for op_cls, op_text in [(Lt, "<"), (Lte, "<="), (Gt, ">"), (Gte, ">=")]:
        sql, _ = compile_query(PERSON, Query(select=["handle"], where=op_cls("gender", 1)))
        assert f"gender {op_text} ?" in sql

    sql, _ = compile_query(PERSON, Query(select=["handle"], where=Like("surname", "A%")))
    assert "surname LIKE ?" in sql

    sql, _ = compile_query(PERSON, Query(select=["handle"], where=In("gender", [1, 2])))
    assert "gender IN (?, ?)" in sql


def test_contains_wraps_value_in_wildcards_and_binds_as_param():
    sql, params = compile_query(PERSON, Query(select=["handle"], where=Contains("surname", "mit")))
    assert "surname LIKE ? ESCAPE '\\'" in sql
    assert params[0] == "%mit%"


def test_contains_escapes_like_metacharacters_in_value():
    # A literal `%`/`_` in the substring being searched for must match
    # literally, not be reinterpreted as a LIKE wildcard -- the whole point
    # of the ESCAPE clause over `Like`'s raw, user-authored pattern.
    sql, params = compile_query(
        PERSON, Query(select=["handle"], where=Contains("surname", "100%_off\\"))
    )
    assert params[0] == "%100\\%\\_off\\\\%"
    assert "ESCAPE '\\'" in sql


def test_contains_repr_shows_raw_substring_not_escaped_pattern():
    assert repr(Contains("surname", "100%")) == "Contains('surname', '100%')"


# --- Contains, field-vs-field ("other_field in field") -----------------------
#
# Unlike a literal substring, the needle here is only known at query
# *execution* time -- Python's compile-time `.replace(...)` escaping can't
# apply, so the same escaping has to happen in SQL itself via nested
# `REPLACE(...)`, in the same backslash-first order.
#
# A flat (same-table) column has to be wrapped in FlatColumnRef to be
# recognized as a field rather than a literal -- a bare `"given_name"`
# string is exactly what Contains("surname", "given_name") means when the
# *literal text* "given_name" is the substring being searched for; nothing
# distinguishes that from a column reference except this wrapper (see
# FlatColumnRef's docstring). The parser (query_lang.py) applies this
# wrapping automatically -- these tests construct the query.py objects
# directly, so they apply it explicitly.


def test_contains_field_vs_field_sqlite_shape():
    sql, params = compile_query(
        PERSON,
        Query(select=["handle"], where=Contains("surname", FlatColumnRef("given_name"))),
        dialect=Dialect.SQLITE,
    )
    assert "REPLACE(REPLACE(REPLACE(given_name" in sql
    assert "surname LIKE '%' || " in sql
    assert "ESCAPE '\\'" in sql
    assert params == [50]  # only the outer treeid param -- no bound pattern


def test_contains_field_vs_field_through_relationships_postgresql_shape():
    birth_place_title = resolve_column_path(PERSON, ["birth", "place", "title"])
    death_place_title = resolve_column_path(PERSON, ["death", "place", "title"])
    sql, params = compile_query(
        PERSON,
        Query(select=["handle"], where=Contains(death_place_title, birth_place_title)),
        dialect=Dialect.POSTGRESQL,
    )
    # Both sides are correlated subqueries (birth/death events -> place),
    # same as any other field-vs-field comparison -- Contains adds the
    # REPLACE/concatenation wrapper around the value side only.
    assert sql.count("SELECT") >= 2
    assert "REPLACE(REPLACE(REPLACE(" in sql
    assert "LIKE '%' || " in sql


def test_contains_field_vs_field_end_to_end_sqlite_execution():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE person (handle TEXT, given_name TEXT, surname TEXT)")
    # kid1's nickname ("Rob") is a substring of their given name ("Robert") --
    # matches. other1's nickname ("Al") is not a substring of their surname
    # ("Jones") -- doesn't match.
    conn.execute("INSERT INTO person VALUES ('kid1', 'Rob', 'Robert')")
    conn.execute("INSERT INTO person VALUES ('other1', 'Al', 'Jones')")
    sql, params = compile_query(
        PERSON,
        Query(select=["handle"], where=Contains("surname", FlatColumnRef("given_name"))),
        dialect=Dialect.SQLITE,
    )
    rows = conn.execute(sql, params).fetchall()
    assert rows == [("kid1",)]


def test_contains_field_vs_field_escapes_wildcard_characters_in_needle():
    # The needle field's *runtime* value contains a real "%" -- it must
    # still be matched literally (as the literal-substring form already
    # guarantees via Python's .replace(...)), not reinterpreted as a LIKE
    # wildcard, even though the escaping now has to happen in SQL via
    # REPLACE(...) instead of at compile time.
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE person (handle TEXT, given_name TEXT, surname TEXT)")
    conn.execute("INSERT INTO person VALUES ('p1', '50% off', 'a 50% off coupon')")
    conn.execute("INSERT INTO person VALUES ('p2', '50X off', 'a 50% off coupon')")
    sql, params = compile_query(
        PERSON,
        Query(select=["handle"], where=Contains("surname", FlatColumnRef("given_name"))),
        dialect=Dialect.SQLITE,
    )
    rows = conn.execute(sql, params).fetchall()
    # p1's needle ("50% off") really is a literal substring of its haystack
    # -- matches. p2's needle ("50X off") isn't -- if "%" had instead been
    # treated as a wildcard, p2 would incorrectly match too ("50" + anything
    # + " off" is a substring of "a 50% off coupon").
    assert rows == [("p1",)]


def test_null_safe_equality_end_to_end_sqlite_execution():
    # Direct proof that the NULL-safe rewrite actually changes matched rows,
    # not just the SQL text: a family where only one side's death date is
    # recorded is included by Ne (distinct: a real value vs. NULL) and
    # excluded by Eq; a family where *neither* side's death date is
    # recorded is excluded by Ne (NULL is not distinct from NULL) and
    # included by Eq.
    import json
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE person (handle TEXT, death_ref_index INTEGER, json_data TEXT)"
    )
    conn.execute("CREATE TABLE event (handle TEXT, json_data TEXT)")
    conn.execute("CREATE TABLE family (handle TEXT, father_handle TEXT, mother_handle TEXT)")

    # f3: mother's death is recorded, father's is not (death_ref_index -1,
    # "no such event") -- one side NULL, one side a real value.
    conn.execute(
        "INSERT INTO person VALUES (?, ?, ?)",
        ("mom3", 0, json.dumps({"event_ref_list": [{"ref": "mom3_death"}]})),
    )
    conn.execute("INSERT INTO person VALUES (?, ?, ?)", ("dad3", -1, json.dumps({})))
    conn.execute(
        "INSERT INTO event VALUES (?, ?)", ("mom3_death", json.dumps({"date": {"sortval": 2433283}}))
    )
    conn.execute("INSERT INTO family VALUES (?, ?, ?)", ("f3", "dad3", "mom3"))

    # f4: neither side's death is recorded -- both NULL.
    conn.execute("INSERT INTO person VALUES (?, ?, ?)", ("mom4", -1, json.dumps({})))
    conn.execute("INSERT INTO person VALUES (?, ?, ?)", ("dad4", -1, json.dumps({})))
    conn.execute("INSERT INTO family VALUES (?, ?, ?)", ("f4", "dad4", "mom4"))

    ne_sql, ne_params = compile_query(
        FAMILY,
        Query(select=["handle"], where=Ne(MOTHER_DEATH_SORTVAL, FATHER_DEATH_SORTVAL)),
        dialect=Dialect.SQLITE,
    )
    eq_sql, eq_params = compile_query(
        FAMILY,
        Query(select=["handle"], where=Eq(MOTHER_DEATH_SORTVAL, FATHER_DEATH_SORTVAL)),
        dialect=Dialect.SQLITE,
    )
    ne_rows = {row[0] for row in conn.execute(ne_sql, ne_params).fetchall()}
    eq_rows = {row[0] for row in conn.execute(eq_sql, eq_params).fetchall()}

    assert "f3" in ne_rows  # one side missing -> distinct -> matches Ne
    assert "f3" not in eq_rows
    assert "f4" not in ne_rows  # both sides missing -> not distinct -> matches Eq
    assert "f4" in eq_rows



def test_compile_query_uses_spec_table_and_columns():
    query = Query(select=["handle", "father_handle"])
    sql, _ = compile_query(FAMILY, query)
    assert "FROM family" in sql
    assert "father_handle" in sql


def test_compile_query_emits_logical_column_names_without_postgresql_dialect():
    # No dialect (or SQLite): plain logical column names exactly as
    # get_secondary_fields() names them -- SQLite never had SharedPostgreSQL's
    # column-renaming history, so nothing needs translating there.
    query = Query(select=["handle", "description"])
    sql, _ = compile_query(EVENT, query)
    assert "description" in sql
    assert "desc_ription" not in sql

    sql, _ = compile_query(EVENT, query, dialect=Dialect.SQLITE)
    assert "description" in sql
    assert "desc_ription" not in sql


def test_compile_query_maps_legacy_postgresql_column_names():
    # SharedPostgreSQL's Connection.execute() used to blindly string-replace
    # "desc" -> "desc_" on every query it ran (not just its own), which
    # happened to auto-correct a plain logical reference like `description`
    # into the real physical `desc_ription` column -- see
    # _POSTGRESQL_PHYSICAL_COLUMN_OVERRIDES's module-level note.
    #
    # addons-source PR #1001 removed that blind rewrite (rightly -- it
    # corrupted any identifier containing "desc" as a substring), replacing
    # it with a _quote_column() used only inside the addon's own generated
    # SQL. That means this compiler's plain, unmapped column names no longer
    # get auto-corrected downstream -- confirmed live post-#1001, a bare
    # `description`/`desc` reference now raises psycopg2's UndefinedColumn
    # instead of reaching the real column. This compiler must emit the
    # correct physical name itself for PostgreSQL now.
    sql, _ = compile_query(
        EVENT, Query(select=["handle", "description"]), dialect=Dialect.POSTGRESQL
    )
    assert "desc_ription" in sql
    assert "description" not in sql

    sql, _ = compile_query(
        MEDIA, Query(select=["handle", "desc"]), dialect=Dialect.POSTGRESQL
    )
    assert "desc_" in sql


def test_compile_query_maps_legacy_postgresql_column_in_order_by_and_keyset():
    # Media's default sort is by "desc" -- the override has to apply to
    # ORDER BY and keyset pagination too, not just SELECT/WHERE, since
    # PostgreSQL's physical table has the same renamed column either way.
    sql, _ = compile_query(
        MEDIA,
        Query(select=["handle"], order_by=[OrderBy("desc", "asc")]),
        dialect=Dialect.POSTGRESQL,
    )
    assert "ORDER BY desc_" in sql
    assert '"desc"' not in sql

    sql, _ = compile_query(
        MEDIA,
        Query(
            select=["handle"],
            order_by=[OrderBy("desc", "asc")],
            after=["Funeral photo", "H0001"],
        ),
        dialect=Dialect.POSTGRESQL,
    )
    assert "desc_ > ?" in sql
    assert '"desc"' not in sql


def test_text_columns_exclude_non_string_fields():
    # gender/change/private etc. are integer/boolean secondary fields --
    # not eligible for a locale COLLATE clause.
    assert {"surname", "given_name", "gramps_id", "handle"} <= PERSON.text_columns
    assert "gender" not in PERSON.text_columns
    assert "private" not in PERSON.text_columns
    assert "change" not in PERSON.text_columns


def test_no_collate_clause_without_collation_argument():
    query = Query(select=["handle"], order_by=[OrderBy("surname", "asc")])
    sql, _ = compile_query(PERSON, query)
    assert "COLLATE" not in sql


def test_collate_applied_to_text_order_by_columns():
    query = Query(select=["handle"], order_by=[OrderBy("surname", "asc")])
    sql, _ = compile_query(PERSON, query, collation="de_DE")
    assert 'surname COLLATE "de_DE" ASC' in sql
    # trailing handle tiebreaker is also text -- also collated
    assert 'handle COLLATE "de_DE" ASC' in sql


def test_collate_not_applied_to_non_text_order_by_columns():
    query = Query(select=["handle"], order_by=[OrderBy("gender", "asc")])
    sql, _ = compile_query(PERSON, query, collation="de_DE")
    assert "gender COLLATE" not in sql
    assert "gender ASC" in sql


def test_collate_applied_to_keyset_comparisons_for_text_columns():
    query = Query(
        select=["handle"],
        order_by=[OrderBy("surname", "desc"), OrderBy("gender", "asc")],
        after=("Smith", 1, "h123"),
    )
    sql, params = compile_query(PERSON, query, collation="de_DE")
    assert 'surname COLLATE "de_DE" < ?' in sql
    assert 'surname COLLATE "de_DE" = ?' in sql
    assert "gender > ?" in sql
    assert "gender COLLATE" not in sql
    assert 'handle COLLATE "de_DE" > ?' in sql
    assert params == ["Smith", "Smith", 1, "Smith", 1, "h123", 50]


def test_unknown_column_in_where_rejected():
    query = Query(where=Eq("; DROP TABLE person; --", 1))
    with pytest.raises(QueryError):
        compile_query(PERSON, query)


def test_unknown_column_in_select_rejected():
    query = Query(select=["handle", "not_a_real_column"])
    with pytest.raises(QueryError):
        compile_query(PERSON, query)


def test_unknown_column_in_order_by_rejected():
    query = Query(order_by=[OrderBy("not_a_real_column")])
    with pytest.raises(QueryError):
        compile_query(PERSON, query)


def test_simple_eq_compiles_with_bound_param():
    query = Query(select=["handle"], where=Eq("gender", 1))
    sql, params = compile_query(PERSON, query)
    assert "gender IS NOT DISTINCT FROM ?" in sql
    assert params[0] == 1
    # the value is bound as a parameter, never interpolated into the SQL text
    where_clause = sql.split("WHERE", 1)[1]
    assert "?" in where_clause and "1" not in where_clause


def test_and_or_not_compile_and_combine_params():
    query = Query(
        select=["handle"],
        where=And(Eq("gender", 1), Or(Like("surname", "A%"), Not(Eq("surname", "")))),
    )
    sql, params = compile_query(PERSON, query)
    assert "AND" in sql
    assert "OR" in sql
    assert "NOT" in sql
    assert 1 in params
    assert "A%" in params
    assert "" in params


def test_in_requires_at_least_one_value():
    with pytest.raises(QueryError):
        In("gender", [])


def test_in_compiles_placeholders():
    query = Query(select=["handle"], where=In("gender", [0, 1]))
    sql, params = compile_query(PERSON, query)
    assert "gender IN (?, ?)" in sql
    assert params[:2] == [0, 1]


def test_default_select_is_all_whitelisted_columns():
    query = Query()
    sql, _ = compile_query(PERSON, query)
    select_clause = sql.split(" FROM ")[0]
    for column in PERSON.columns:
        assert column in select_clause


def test_order_by_gets_trailing_handle_tiebreaker():
    query = Query(select=["handle"], order_by=[OrderBy("surname", "asc")])
    sql, _ = compile_query(PERSON, query)
    assert "ORDER BY surname ASC, handle ASC" in sql


def test_order_by_does_not_duplicate_explicit_handle():
    query = Query(
        select=["handle"],
        order_by=[OrderBy("surname", "asc"), OrderBy("handle", "desc")],
    )
    sql, _ = compile_query(PERSON, query)
    order_by_clause = sql.split("ORDER BY", 1)[1]
    assert order_by_clause.startswith(" surname ASC, handle DESC")
    assert order_by_clause.count("handle") == 1  # not duplicated


def test_default_order_by_is_handle_only():
    query = Query(select=["handle"])
    sql, _ = compile_query(PERSON, query)
    assert "ORDER BY handle ASC" in sql


def test_limit_appended_as_param():
    query = Query(select=["handle"], limit=25)
    sql, params = compile_query(PERSON, query)
    assert sql.rstrip().endswith("LIMIT ?")
    assert params[-1] == 25


def test_non_positive_limit_rejected():
    with pytest.raises(QueryError):
        Query(limit=0)
    with pytest.raises(QueryError):
        Query(limit=-5)


def test_after_columns_matches_effective_order_by():
    assert after_columns([OrderBy("surname", "asc")]) == ("surname", "handle")
    assert after_columns([]) == ("handle",)
    assert after_columns([OrderBy("handle", "desc")]) == ("handle",)


def test_after_wrong_length_rejected():
    query = Query(
        select=["handle"], order_by=[OrderBy("surname", "asc")], after=("Smith",)
    )
    with pytest.raises(QueryError):
        compile_query(PERSON, query)


def test_keyset_pagination_single_column_asc():
    query = Query(
        select=["handle"],
        order_by=[OrderBy("surname", "asc")],
        after=("Smith", "h123"),
    )
    sql, params = compile_query(PERSON, query)
    assert "surname > ?" in sql
    assert "Smith" in params
    assert "h123" in params


def test_keyset_pagination_mixed_directions_seek_expansion():
    query = Query(
        select=["handle"],
        order_by=[OrderBy("surname", "desc"), OrderBy("given_name", "asc")],
        after=("Smith", "Alice", "h123"),
    )
    sql, params = compile_query(PERSON, query)
    # OR-of-ANDs seek expansion, not a row-constructor comparison, so mixed
    # asc/desc directions stay correct.
    assert "surname < ?" in sql
    assert "given_name > ?" in sql
    assert "handle > ?" in sql
    assert sql.index("OR") > 0


def test_and_or_require_at_least_one_expr():
    with pytest.raises(QueryError):
        And()
    with pytest.raises(QueryError):
        Or()


# --- Collection / Exists (one-to-many membership) -----------------------------


def test_resolve_collection_children_shape():
    # A ref-object list (ChildRef.ref) -- the shape needing .ref extraction.
    children = resolve_collection(FAMILY, "children")
    assert isinstance(children, Collection)
    assert children.name == "children"
    assert children.target is PERSON
    assert children.list_path == JsonPath(("child_ref_list",))
    assert children.ref_field == "ref"


def test_resolve_collection_notes_shape():
    # A flat handle list -- no ref_field extraction needed.
    notes = resolve_collection(PERSON, "notes")
    assert notes.target is NOTE
    assert notes.list_path == JsonPath(("note_list",))
    assert notes.ref_field is None


def test_resolve_collection_unknown_raises():
    with pytest.raises(QueryError):
        resolve_collection(PERSON, "children")  # only registered on FAMILY
    with pytest.raises(QueryError):
        resolve_collection(FAMILY, "bogus")


def test_exists_requires_dialect():
    children = resolve_collection(FAMILY, "children")
    with pytest.raises(QueryError):
        compile_query(FAMILY, Query(where=Exists(children)))


def test_exists_sqlite_shape_with_condition():
    children = resolve_collection(FAMILY, "children")
    sql, params = compile_query(
        FAMILY,
        Query(select=["handle"], where=Exists(children, Eq("given_name", "Steve"))),
        dialect=Dialect.SQLITE,
    )
    assert "EXISTS (SELECT 1 FROM person AS person__target, json_each(family.json_data, ?) AS je" in sql
    assert "person__target.handle = json_extract(je.value, '$.ref')" in sql
    assert "given_name IS NOT DISTINCT FROM ?" in sql
    assert params == ["$.child_ref_list", "Steve", 50]


def test_exists_sqlite_shape_no_condition():
    # exists(children) with no condition -- "at least one child at all".
    children = resolve_collection(FAMILY, "children")
    sql, params = compile_query(
        FAMILY, Query(select=["handle"], where=Exists(children)), dialect=Dialect.SQLITE
    )
    assert "EXISTS (SELECT 1 FROM person AS person__target, json_each(family.json_data, ?) AS je" in sql
    assert "person__target.handle = json_extract(je.value, '$.ref'))" in sql
    assert params == ["$.child_ref_list", 50]


def test_exists_postgresql_shape_ref_object_list():
    children = resolve_collection(FAMILY, "children")
    sql, params = compile_query(
        FAMILY,
        Query(select=["handle"], where=Exists(children, Eq("given_name", "Steve"))),
        dialect=Dialect.POSTGRESQL,
    )
    assert (
        "jsonb_array_elements(family.json_data::jsonb -> 'child_ref_list') AS je(value)" in sql
    )
    assert "person__target.handle = je.value ->> 'ref'" in sql
    assert params == ["Steve", 50]


def test_exists_flat_handle_list_sqlite_shape():
    # notes: je.value is already the handle -- no json_extract needed, unlike
    # the ref-object shape above.
    notes = resolve_collection(PERSON, "notes")
    sql, params = compile_query(
        PERSON, Query(select=["handle"], where=Exists(notes)), dialect=Dialect.SQLITE
    )
    assert "json_each(person.json_data, ?) AS je" in sql
    assert "note__target.handle = je.value" in sql
    assert "json_extract(je.value" not in sql
    assert params == ["$.note_list", 50]


def test_exists_flat_handle_list_postgresql_shape():
    notes = resolve_collection(PERSON, "notes")
    sql, params = compile_query(
        PERSON, Query(select=["handle"], where=Exists(notes)), dialect=Dialect.POSTGRESQL
    )
    assert (
        "jsonb_array_elements_text(person.json_data::jsonb -> 'note_list') AS je(value)" in sql
    )
    assert "note__target.handle = je.value" in sql
    assert params == [50]


def test_exists_treeid_scoping():
    children = resolve_collection(FAMILY, "children")
    sql, params = compile_query(
        FAMILY,
        Query(select=["handle"], where=Exists(children, Eq("given_name", "Steve"))),
        dialect=Dialect.SQLITE,
        treeid=7,
    )
    assert "person__target.treeid = ?" in sql
    # the EXISTS subquery's own treeid clause, plus the outer query's own.
    assert sql.count("treeid = ?") == 2
    assert params == ["$.child_ref_list", "Steve", 7, 7, 50]


def test_not_exists_compiles():
    children = resolve_collection(FAMILY, "children")
    sql, params = compile_query(
        FAMILY, Query(select=["handle"], where=Not(Exists(children))), dialect=Dialect.SQLITE
    )
    assert "NOT (EXISTS" in sql


def test_exists_composes_with_and():
    children = resolve_collection(FAMILY, "children")
    sql, params = compile_query(
        FAMILY,
        Query(
            select=["handle"],
            where=And(Exists(children, Eq("given_name", "Steve")), Eq("gramps_id", "F001")),
        ),
        dialect=Dialect.SQLITE,
    )
    assert "EXISTS" in sql
    assert "AND" in sql


def test_exists_end_to_end_sqlite_execution_ref_object_list():
    import json
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE family (handle TEXT, json_data TEXT)")
    conn.execute("CREATE TABLE person (handle TEXT, given_name TEXT)")
    conn.execute("INSERT INTO person VALUES ('steve', 'Steve')")
    conn.execute("INSERT INTO person VALUES ('bob', 'Bob')")
    conn.execute(
        "INSERT INTO family VALUES ('fam-with-steve', ?)",
        (json.dumps({"child_ref_list": [{"ref": "steve"}]}),),
    )
    conn.execute(
        "INSERT INTO family VALUES ('fam-without-steve', ?)",
        (json.dumps({"child_ref_list": [{"ref": "bob"}]}),),
    )
    conn.execute(
        "INSERT INTO family VALUES ('fam-no-children', ?)",
        (json.dumps({"child_ref_list": []}),),
    )

    children = resolve_collection(FAMILY, "children")
    sql, params = compile_query(
        FAMILY,
        Query(select=["handle"], where=Exists(children, Eq("given_name", "Steve"))),
        dialect=Dialect.SQLITE,
    )
    rows = conn.execute(sql, params).fetchall()
    assert rows == [("fam-with-steve",)]


def test_exists_no_condition_end_to_end_sqlite_execution():
    import json
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE family (handle TEXT, json_data TEXT)")
    conn.execute("CREATE TABLE person (handle TEXT)")
    conn.execute("INSERT INTO person VALUES ('kid1')")
    conn.execute(
        "INSERT INTO family VALUES ('has-child', ?)",
        (json.dumps({"child_ref_list": [{"ref": "kid1"}]}),),
    )
    conn.execute(
        "INSERT INTO family VALUES ('no-children', ?)", (json.dumps({"child_ref_list": []}),)
    )

    children = resolve_collection(FAMILY, "children")
    sql, params = compile_query(
        FAMILY, Query(select=["handle"], where=Exists(children)), dialect=Dialect.SQLITE
    )
    assert conn.execute(sql, params).fetchall() == [("has-child",)]

    sql, params = compile_query(
        FAMILY, Query(select=["handle"], where=Not(Exists(children))), dialect=Dialect.SQLITE
    )
    assert conn.execute(sql, params).fetchall() == [("no-children",)]


def test_exists_flat_handle_list_end_to_end_sqlite_execution():
    import json
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE person (handle TEXT, json_data TEXT)")
    conn.execute("CREATE TABLE note (handle TEXT, format INTEGER)")
    conn.execute("INSERT INTO note VALUES ('note1', 0)")
    conn.execute(
        "INSERT INTO person VALUES ('has-note', ?)", (json.dumps({"note_list": ["note1"]}),)
    )
    conn.execute("INSERT INTO person VALUES ('no-note', ?)", (json.dumps({"note_list": []}),))

    notes = resolve_collection(PERSON, "notes")
    sql, params = compile_query(
        PERSON, Query(select=["handle"], where=Exists(notes)), dialect=Dialect.SQLITE
    )
    assert conn.execute(sql, params).fetchall() == [("has-note",)]


# --- CollectionCount / count(...) (Collection cardinality) --------------------


def test_collection_count_requires_dialect():
    children = resolve_collection(FAMILY, "children")
    with pytest.raises(QueryError):
        compile_query(FAMILY, Query(where=Gt(CollectionCount(children), 2)))


def test_collection_count_sqlite_shape_with_condition():
    children = resolve_collection(FAMILY, "children")
    sql, params = compile_query(
        FAMILY,
        Query(select=["handle"], where=Gt(CollectionCount(children, Eq("gender", 1)), 1)),
        dialect=Dialect.SQLITE,
    )
    assert "(SELECT COUNT(*) FROM person AS person__target, json_each(family.json_data, ?) AS je" in sql
    assert "person__target.handle = json_extract(je.value, '$.ref')" in sql
    assert "gender IS NOT DISTINCT FROM ?" in sql
    assert ") > ?" in sql
    assert params == ["$.child_ref_list", 1, 1, 50]


def test_collection_count_sqlite_shape_no_condition():
    children = resolve_collection(FAMILY, "children")
    sql, params = compile_query(
        FAMILY,
        Query(select=["handle"], where=Gt(CollectionCount(children), 2)),
        dialect=Dialect.SQLITE,
    )
    assert "(SELECT COUNT(*) FROM person AS person__target, json_each(family.json_data, ?) AS je" in sql
    assert "person__target.handle = json_extract(je.value, '$.ref'))" in sql
    assert params == ["$.child_ref_list", 2, 50]


def test_collection_count_postgresql_shape():
    children = resolve_collection(FAMILY, "children")
    sql, params = compile_query(
        FAMILY,
        Query(select=["handle"], where=Gt(CollectionCount(children), 2)),
        dialect=Dialect.POSTGRESQL,
    )
    assert (
        "(SELECT COUNT(*) FROM person AS person__target, jsonb_array_elements(family.json_data::jsonb -> "
        "'child_ref_list') AS je(value) WHERE person__target.handle = je.value ->> 'ref')" in sql
    )
    assert params == [2, 50]


def test_collection_count_flat_handle_list_sqlite_shape():
    notes = resolve_collection(PERSON, "notes")
    sql, params = compile_query(
        PERSON, Query(select=["handle"], where=Gt(CollectionCount(notes), 0)), dialect=Dialect.SQLITE
    )
    assert "note__target.handle = je.value" in sql
    assert "json_extract(je.value" not in sql
    assert params == ["$.note_list", 0, 50]


def test_collection_count_treeid_scoping():
    children = resolve_collection(FAMILY, "children")
    sql, params = compile_query(
        FAMILY,
        Query(select=["handle"], where=Gt(CollectionCount(children, Eq("gender", 1)), 1)),
        dialect=Dialect.SQLITE,
        treeid=7,
    )
    assert "person__target.treeid = ?" in sql
    assert sql.count("treeid = ?") == 2
    assert params == ["$.child_ref_list", 1, 7, 1, 7, 50]


def test_collection_count_composes_with_and():
    children = resolve_collection(FAMILY, "children")
    sql, params = compile_query(
        FAMILY,
        Query(
            select=["handle"],
            where=And(Gt(CollectionCount(children), 2), Eq("gramps_id", "F001")),
        ),
        dialect=Dialect.SQLITE,
    )
    assert "SELECT COUNT(*)" in sql
    assert "AND" in sql


def test_collection_count_in_operator():
    children = resolve_collection(FAMILY, "children")
    sql, params = compile_query(
        FAMILY,
        Query(select=["handle"], where=In(CollectionCount(children), [1, 2, 3])),
        dialect=Dialect.SQLITE,
    )
    assert "IN (?, ?, ?)" in sql
    assert params == ["$.child_ref_list", 1, 2, 3, 50]


def test_collection_count_end_to_end_sqlite_execution():
    import json
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE family (handle TEXT, json_data TEXT)")
    conn.execute("CREATE TABLE person (handle TEXT, gender INTEGER)")
    conn.execute("INSERT INTO person VALUES ('steve', 1)")
    conn.execute("INSERT INTO person VALUES ('anna', 0)")
    conn.execute("INSERT INTO person VALUES ('bob', 1)")
    conn.execute(
        "INSERT INTO family VALUES ('fam-3kids', ?)",
        (json.dumps({"child_ref_list": [{"ref": "steve"}, {"ref": "anna"}, {"ref": "bob"}]}),),
    )
    conn.execute(
        "INSERT INTO family VALUES ('fam-1kid', ?)",
        (json.dumps({"child_ref_list": [{"ref": "anna"}]}),),
    )

    children = resolve_collection(FAMILY, "children")
    sql, params = compile_query(
        FAMILY, Query(select=["handle"], where=Gt(CollectionCount(children), 2)), dialect=Dialect.SQLITE
    )
    assert conn.execute(sql, params).fetchall() == [("fam-3kids",)]

    sql, params = compile_query(
        FAMILY,
        Query(select=["handle"], where=Gt(CollectionCount(children, Eq("gender", 1)), 1)),
        dialect=Dialect.SQLITE,
    )
    assert conn.execute(sql, params).fetchall() == [("fam-3kids",)]


# --- Expanded Collection registry (all ten primary types) ---------------------
#
# `children`/`notes` proved out the two `Collection` shapes (ref-object list
# needing `.ref` extraction, flat handle list already the handle) against
# every base class Gramps core actually uses (`NoteBase`/`CitationBase`/
# `MediaBase`/`TagBase`, plus each type's own one-off lists) -- confirmed
# against `gramps/gen/lib/*.py`'s real class hierarchy, not guessed from
# naming. This section registers the rest and locks in the full inventory.

# (table, collection name) -> (target spec, json key, ref_field)
_EXPECTED_COLLECTIONS = {
    (PERSON, "notes"): (NOTE, "note_list", None),
    (PERSON, "citations"): (CITATION, "citation_list", None),
    (PERSON, "media"): (MEDIA, "media_list", "ref"),
    (PERSON, "tags"): (TAG, "tag_list", None),
    (PERSON, "families"): (FAMILY, "family_list", None),
    (PERSON, "parent_families"): (FAMILY, "parent_family_list", None),
    (PERSON, "associations"): (PERSON, "person_ref_list", "ref"),
    (PERSON, "events"): (EVENT, "event_ref_list", "ref"),
    (FAMILY, "children"): (PERSON, "child_ref_list", "ref"),
    (FAMILY, "notes"): (NOTE, "note_list", None),
    (FAMILY, "citations"): (CITATION, "citation_list", None),
    (FAMILY, "media"): (MEDIA, "media_list", "ref"),
    (FAMILY, "tags"): (TAG, "tag_list", None),
    (FAMILY, "events"): (EVENT, "event_ref_list", "ref"),
    (EVENT, "notes"): (NOTE, "note_list", None),
    (EVENT, "citations"): (CITATION, "citation_list", None),
    (EVENT, "media"): (MEDIA, "media_list", "ref"),
    (EVENT, "tags"): (TAG, "tag_list", None),
    (PLACE, "notes"): (NOTE, "note_list", None),
    (PLACE, "citations"): (CITATION, "citation_list", None),
    (PLACE, "media"): (MEDIA, "media_list", "ref"),
    (PLACE, "tags"): (TAG, "tag_list", None),
    (PLACE, "enclosing_places"): (PLACE, "placeref_list", "ref"),
    (SOURCE, "notes"): (NOTE, "note_list", None),
    (SOURCE, "media"): (MEDIA, "media_list", "ref"),
    (SOURCE, "tags"): (TAG, "tag_list", None),
    (SOURCE, "repositories"): (REPOSITORY, "reporef_list", "ref"),
    (CITATION, "notes"): (NOTE, "note_list", None),
    (CITATION, "media"): (MEDIA, "media_list", "ref"),
    (CITATION, "tags"): (TAG, "tag_list", None),
    (REPOSITORY, "notes"): (NOTE, "note_list", None),
    (REPOSITORY, "tags"): (TAG, "tag_list", None),
    (MEDIA, "notes"): (NOTE, "note_list", None),
    (MEDIA, "citations"): (CITATION, "citation_list", None),
    (MEDIA, "tags"): (TAG, "tag_list", None),
    (NOTE, "tags"): (TAG, "tag_list", None),
}


@pytest.mark.parametrize("spec_and_name,expected", list(_EXPECTED_COLLECTIONS.items()))
def test_expanded_collection_registry_shapes(spec_and_name, expected):
    spec, name = spec_and_name
    target, key, ref_field = expected
    collection = resolve_collection(spec, name)
    assert collection.target is target
    assert collection.list_path == JsonPath((key,))
    assert collection.ref_field == ref_field


def test_expanded_collection_registry_exhaustive():
    # Every table's registered names match _EXPECTED_COLLECTIONS exactly --
    # catches an accidental extra/missing registration that a per-name test
    # above wouldn't (it only checks names it's told to look for).
    import gramps_object_query_language.query as query_module

    actual = {
        (spec, name)
        for table, names in query_module._COLLECTIONS.items()
        for spec in [
            {
                PERSON.table: PERSON,
                FAMILY.table: FAMILY,
                EVENT.table: EVENT,
                PLACE.table: PLACE,
                SOURCE.table: SOURCE,
                CITATION.table: CITATION,
                REPOSITORY.table: REPOSITORY,
                MEDIA.table: MEDIA,
                NOTE.table: NOTE,
            }[table]
        ]
        for name in names
    }
    assert actual == set(_EXPECTED_COLLECTIONS)


def test_tag_has_no_collections_registered():
    # Tag is the one primary type with no note_list/citation_list/media_list/
    # tag_list of its own (a tag doesn't tag itself) and no other one-to-many
    # field -- confirm it's absent from the registry entirely, not just empty.
    import gramps_object_query_language.query as query_module

    assert TAG.table not in query_module._COLLECTIONS


# --- Citation.source (one-to-one, Citation -> Source) --------------------------


def test_citation_source_relationship_shape():
    ref = resolve_column_path(CITATION, ["source", "title"])
    assert isinstance(ref, RelatedObject)
    assert ref.name == "source"
    assert ref.target is SOURCE
    assert ref.handle_ref == "source_handle"
    assert ref.field == "title"


def test_citation_source_end_to_end_sqlite_execution():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE citation (handle TEXT, source_handle TEXT)")
    conn.execute("CREATE TABLE source (handle TEXT, title TEXT)")
    conn.execute("INSERT INTO source VALUES ('src1', 'Census Records')")
    conn.execute("INSERT INTO source VALUES ('src2', 'Church Records')")
    conn.execute("INSERT INTO citation VALUES ('c1', 'src1')")
    conn.execute("INSERT INTO citation VALUES ('c2', 'src2')")

    source_title = resolve_column_path(CITATION, ["source", "title"])
    sql, params = compile_query(
        CITATION,
        Query(select=["handle"], where=Eq(source_title, "Census Records")),
        dialect=Dialect.SQLITE,
    )
    assert conn.execute(sql, params).fetchall() == [("c1",)]


# --- Self-referencing Collection (Person.associations -> Person) --------------
#
# The one registered collection whose target table is the *same* as the
# outer table -- exposed a real bug (see `_collection_subquery_body`'s
# docstring in query.py): without aliasing the target row, the subquery's
# own `FROM person AS ...` reintroduces the bare `person` name already bound
# to the outer, correlated row, silently breaking correlation. These are the
# regression tests for that fix.


def test_associations_self_reference_sqlite_end_to_end():
    import json
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE person (handle TEXT, given_name TEXT, json_data TEXT)")
    conn.execute(
        "INSERT INTO person VALUES ('alice', 'Alice', ?)",
        (json.dumps({"person_ref_list": [{"ref": "bob"}]}),),
    )
    conn.execute(
        "INSERT INTO person VALUES ('bob', 'Bob', ?)", (json.dumps({"person_ref_list": []}),)
    )

    associations = resolve_collection(PERSON, "associations")
    sql, params = compile_query(
        PERSON,
        Query(select=["handle"], where=Exists(associations, Eq("given_name", "Bob"))),
        dialect=Dialect.SQLITE,
    )
    assert conn.execute(sql, params).fetchall() == [("alice",)]


def test_associations_self_reference_postgresql_shape():
    # Same aliasing fix, PostgreSQL rendering -- both branches of
    # _collection_subquery_body go through the same target_alias.
    associations = resolve_collection(PERSON, "associations")
    sql, params = compile_query(
        PERSON,
        Query(select=["handle"], where=Exists(associations, Eq("given_name", "Bob"))),
        dialect=Dialect.POSTGRESQL,
    )
    assert "person AS person__target" in sql
    assert "person__target.handle = je.value ->> 'ref'" in sql
    # the outer correlation (person.json_data) must still reference the
    # *unaliased* outer table, not the newly-introduced target alias.
    assert "person.json_data::jsonb -> 'person_ref_list'" in sql


# --- Place.enclosed_by: RelatedObject self-reference --------------------------
#
# The same class of bug as Person.associations above, one level more subtle:
# RelatedObject nests arbitrarily deep rather than being a single flat
# subquery, so a *fixed* alias suffix (sufficient for Collection) isn't
# enough here -- two nested self-referencing hops would still collide with
# each other. _render_related_object's _depth parameter gives every level a
# distinct alias instead.

ENCLOSED_BY_TITLE = resolve_column_path(PLACE, ["enclosed_by", "title"])
ENCLOSED_BY_ENCLOSED_BY_TITLE = resolve_column_path(PLACE, ["enclosed_by", "enclosed_by", "title"])


def test_place_enclosed_by_sqlite_shape():
    sql, params = compile_query(
        PLACE, Query(select=["handle", ENCLOSED_BY_TITLE]), dialect=Dialect.SQLITE
    )
    assert "FROM place AS place__hop0 WHERE place__hop0.handle = (place.enclosed_by)" in sql


def test_place_enclosed_by_two_hop_self_reference_sqlite_shape():
    # Each hop gets its own alias (place__hop0, place__hop1) even though
    # both target the same table as each other *and* as the outer query.
    sql, params = compile_query(
        PLACE, Query(select=["handle", ENCLOSED_BY_ENCLOSED_BY_TITLE]), dialect=Dialect.SQLITE
    )
    assert "FROM place AS place__hop0 WHERE place__hop0.handle = (place.enclosed_by)" in sql
    assert (
        "FROM place AS place__hop1 WHERE place__hop1.handle = (place__hop0.enclosed_by)"
    ) in sql


def test_place_enclosed_by_self_reference_end_to_end_sqlite_execution():
    # The actual regression guard: a real 3-level hierarchy
    # (city -> county -> state), proving both hops resolve to the correct
    # row rather than a subquery shadowing its own ancestor and matching
    # nothing (the bug this session found: enclosed_by.title == 'Cook
    # County' matched zero rows before _depth-based aliasing).
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE place (handle TEXT, title TEXT, enclosed_by TEXT)")
    conn.execute("INSERT INTO place VALUES ('city', 'Chicago', 'county')")
    conn.execute("INSERT INTO place VALUES ('county', 'Cook County', 'state')")
    conn.execute("INSERT INTO place VALUES ('state', 'Illinois', NULL)")

    sql, params = compile_query(
        PLACE,
        Query(select=["handle"], where=Eq(ENCLOSED_BY_TITLE, "Cook County")),
        dialect=Dialect.SQLITE,
    )
    assert conn.execute(sql, params).fetchall() == [("city",)]

    sql, params = compile_query(
        PLACE,
        Query(select=["handle"], where=Eq(ENCLOSED_BY_ENCLOSED_BY_TITLE, "Illinois")),
        dialect=Dialect.SQLITE,
    )
    assert conn.execute(sql, params).fetchall() == [("city",)]


def test_place_enclosed_by_postgresql_shape():
    sql, params = compile_query(
        PLACE, Query(select=["handle", ENCLOSED_BY_TITLE]), dialect=Dialect.POSTGRESQL
    )
    assert "FROM place AS place__hop0" in sql
    assert "place__hop0.handle = (place.enclosed_by)" in sql


def test_place_enclosed_by_sql_matches_evaluator():
    # The same SQL-vs-evaluator agreement pattern used for the associations
    # self-reference fix and evaluate_where's Not/missing-value fix (see
    # ROADMAP.md's Done section) -- run the same AST through the real
    # SQLite compiler and through evaluate_where against equivalent fake
    # data, and assert they agree.
    import sqlite3

    from gramps_object_query_language.evaluator import evaluate_where

    class _FakePlaceRef:
        def __init__(self, ref):
            self.ref = ref

    class _FakePlace:
        def __init__(self, handle, title, enclosed_by):
            self.handle = handle
            self.title = title
            self._enclosed_by = enclosed_by

        def get_placeref_list(self):
            return [_FakePlaceRef(self._enclosed_by)] if self._enclosed_by else []

    class _FakeDb:
        def __init__(self, places):
            self.places = places

        def get_place_from_handle(self, handle):
            return self.places[handle]

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE place (handle TEXT, title TEXT, enclosed_by TEXT)")
    conn.execute("INSERT INTO place VALUES ('city', 'Chicago', 'county')")
    conn.execute("INSERT INTO place VALUES ('county', 'Cook County', 'state')")
    conn.execute("INSERT INTO place VALUES ('state', 'Illinois', NULL)")

    where = Eq(ENCLOSED_BY_TITLE, "Cook County")
    sql, params = compile_query(PLACE, Query(select=["handle"], where=where), dialect=Dialect.SQLITE)
    sql_matches = {row[0] for row in conn.execute(sql, params).fetchall()}

    places = {
        "city": _FakePlace("city", "Chicago", "county"),
        "county": _FakePlace("county", "Cook County", "state"),
        "state": _FakePlace("state", "Illinois", None),
    }
    db = _FakeDb(places)
    evaluator_matches = {
        handle for handle, obj in places.items() if evaluate_where(db, obj, where, PLACE)
    }
    assert sql_matches == evaluator_matches == {"city"}
