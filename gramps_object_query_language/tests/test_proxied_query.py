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

"""Tests for `proxied_query.py`'s `run_query`, end to end.

Same real-temporary-SQLite-db pattern as `test_evaluator.py` and
`tests/test_private_proxy.py` -- `run_query` drives Gramps' own
`Filter.apply()`, so a mock database would only prove the mock's behavior,
not that this composes correctly with core.
"""

import pytest
from gramps.cli.clidbman import CLIDbManager
from gramps.gen.db import DbTxn
from gramps.gen.db.utils import make_database
from gramps.gen.dbstate import DbState
from gramps.gen.lib import Family, Name, Person, Surname, Tag
from gramps.gen.proxy import PrivateProxyDb

from gramps_object_query_language.proxied_query import run_query
from gramps_object_query_language.query import (
    FAMILY,
    PERSON,
    TAG,
    Dialect,
    Eq,
    OrderBy,
    Query,
    QueryError,
    compile_query,
    resolve_column_path,
)


def _name(given: str, surname: str) -> Name:
    name = Name()
    name.set_first_name(given)
    surn = Surname()
    surn.set_surname(surname)
    name.set_surname_list([surn])
    return name


@pytest.fixture(scope="module")
def db_handles():
    dbman = CLIDbManager(DbState())
    dirpath, db_name = dbman.create_new_db_cli("_test_proxied_query", dbid="sqlite")
    db = make_database("sqlite")
    db.load(dirpath)

    handles = {}

    with DbTxn("setup", db) as trans:
        father = Person()
        father.set_primary_name(_name("Karl", "Anderson"))
        father.set_gender(Person.MALE)
        handles["father"] = db.add_person(father, trans)

        mother = Person()
        mother.set_primary_name(_name("Lena", "Baker"))
        mother.set_gender(Person.FEMALE)
        handles["mother"] = db.add_person(mother, trans)

        private_person = Person()
        private_person.set_primary_name(_name("Hidden", "Hermann"))
        private_person.set_privacy(True)
        handles["private_person"] = db.add_person(private_person, trans)

        family = Family()
        family.set_father_handle(handles["father"])
        family.set_mother_handle(handles["mother"])
        handles["family"] = db.add_family(family, trans)

        genealogy_tag = Tag()
        genealogy_tag.set_name("Genealogy")
        handles["genealogy_tag"] = db.add_tag(genealogy_tag, trans)

        research_tag = Tag()
        research_tag.set_name("Research")
        handles["research_tag"] = db.add_tag(research_tag, trans)

    yield db, handles

    db.close()
    dbman.remove_database(db_name)


@pytest.fixture(scope="module")
def proxy(db_handles):
    db, _handles = db_handles
    return PrivateProxyDb(db)


def test_run_query_flat_where_matches_only_expected(db_handles):
    db, handles = db_handles
    matches = run_query(db, PERSON, Eq("gender", Person.MALE))
    assert {p.handle for p in matches} == {handles["father"]}


def test_run_query_no_where_matches_everyone_unproxied(db_handles):
    db, handles = db_handles
    matches = run_query(db, PERSON, None)
    assert {p.handle for p in matches} == {
        handles["father"],
        handles["mother"],
        handles["private_person"],
    }


def test_run_query_related_object_where_on_family(db_handles):
    db, handles = db_handles
    father_surname = resolve_column_path(FAMILY, ["father", "surname"])
    matches = run_query(db, FAMILY, Eq(father_surname, "Anderson"))
    assert {f.handle for f in matches} == {handles["family"]}

    no_matches = run_query(db, FAMILY, Eq(father_surname, "Nobody"))
    assert no_matches == []


def test_run_query_excludes_private_person_via_proxy(db_handles, proxy):
    _db, handles = db_handles
    matches = run_query(proxy, PERSON, None)
    matched_handles = {p.handle for p in matches}
    assert handles["private_person"] not in matched_handles
    assert matched_handles == {handles["father"], handles["mother"]}


def test_run_query_tag_fallback_no_filter_class(db_handles):
    """Tag has no core Filter class (`GenericFilterFactory("Tag")` is
    `None`) -- confirms `run_query` still works via its direct-loop path.
    """
    db, handles = db_handles
    matches = run_query(db, TAG, Eq("name", "Genealogy"))
    assert {t.handle for t in matches} == {handles["genealogy_tag"]}

    all_tags = run_query(db, TAG, None)
    assert {t.handle for t in all_tags} == {
        handles["genealogy_tag"],
        handles["research_tag"],
    }


# --- order_by/limit/after/select parity with the SQL path --------------------
#
# A separate module-scoped fixture from `db_handles` above: those tests
# assert exact handle *sets*, which order/paging changes would be irrelevant
# to (and risky to perturb); this fixture is shaped instead around having
# several sortable people (and a NULL-able flat column) to actually exercise
# sort/seek/limit/select. Every test below runs the *same* `Query` shape
# through both `query.py`'s real compiled SQL (against this fixture's own
# underlying Gramps SQLite backend -- the same `json_data`/`handle` schema
# `query.py` targets) and through `run_query`'s Python-side sort/seek/limit,
# and asserts they agree -- the regression guard for `ROADMAP.md`'s
# "Evaluator-path pagination/sort parity" gap.


@pytest.fixture(scope="module")
def paging_handles():
    dbman = CLIDbManager(DbState())
    dirpath, db_name = dbman.create_new_db_cli("_test_proxied_query_paging", dbid="sqlite")
    db = make_database("sqlite")
    db.load(dirpath)

    handles = {}
    with DbTxn("setup", db) as trans:
        # Deliberately inserted out of alphabetical order, so a passing sort
        # test can't be an accident of insertion/handle order.
        for given in ["Eve", "Carl", "Alice", "Dave", "Bob"]:
            person = Person()
            person.set_primary_name(_name(given, "Smith"))
            person.set_gender(Person.MALE)
            handles[given] = db.add_person(person, trans)

        # Two families with a real father_handle, one with none (a genuine
        # SQL NULL, unlike Person.given_name -- which Gramps always stores
        # as `""`, never NULL) -- needed to exercise NULL placement in
        # sorting *and* keyset seeking (a single NULL row can't exercise the
        # "seek past a NULL cursor" or "NULL dropped from a later DESC page"
        # cases, both of which need at least one non-NULL row on each side).
        with_father_a = Family()
        with_father_a.set_father_handle(handles["Alice"])
        handles["family_with_father_a"] = db.add_family(with_father_a, trans)
        with_father_b = Family()
        with_father_b.set_father_handle(handles["Bob"])
        handles["family_with_father_b"] = db.add_family(with_father_b, trans)
        handles["family_no_father"] = db.add_family(Family(), trans)

    yield db, handles

    db.close()
    dbman.remove_database(db_name)


def _sql_rows(db, spec, query):
    sql, params = compile_query(spec, query, dialect=Dialect.SQLITE)
    db.dbapi.execute(sql, params)
    return db.dbapi.fetchall()


def test_run_query_order_by_matches_sql_asc(paging_handles):
    db, _handles = paging_handles
    order_by = [OrderBy("given_name", "asc")]
    query = Query(select=["handle", "given_name"], order_by=order_by, limit=100)
    expected = _sql_rows(db, PERSON, query)
    actual = run_query(
        db, PERSON, None, order_by=order_by, limit=100, select=["handle", "given_name"]
    )
    assert actual == expected
    assert [row[1] for row in actual] == ["Alice", "Bob", "Carl", "Dave", "Eve"]


def test_run_query_order_by_matches_sql_desc(paging_handles):
    db, _handles = paging_handles
    order_by = [OrderBy("given_name", "desc")]
    query = Query(select=["handle", "given_name"], order_by=order_by, limit=100)
    expected = _sql_rows(db, PERSON, query)
    actual = run_query(
        db, PERSON, None, order_by=order_by, limit=100, select=["handle", "given_name"]
    )
    assert actual == expected
    assert [row[1] for row in actual] == ["Eve", "Dave", "Carl", "Bob", "Alice"]


def test_run_query_limit_matches_sql(paging_handles):
    db, _handles = paging_handles
    order_by = [OrderBy("given_name", "asc")]
    query = Query(select=["handle", "given_name"], order_by=order_by, limit=2)
    expected = _sql_rows(db, PERSON, query)
    actual = run_query(
        db, PERSON, None, order_by=order_by, limit=2, select=["handle", "given_name"]
    )
    assert actual == expected
    assert len(actual) == 2


def test_run_query_after_keyset_matches_sql_next_page(paging_handles):
    db, _handles = paging_handles
    order_by = [OrderBy("given_name", "asc")]

    page1_query = Query(select=["handle", "given_name"], order_by=order_by, limit=2)
    page1 = _sql_rows(db, PERSON, page1_query)
    # `after_columns(order_by)` documents this shape: one resolved value per
    # effective sort column (`given_name`, then the implicit `handle`
    # tiebreaker) for the last row of the previous page.
    cursor = (page1[-1][1], page1[-1][0])

    page2_query = Query(select=["handle", "given_name"], order_by=order_by, limit=2, after=cursor)
    expected = _sql_rows(db, PERSON, page2_query)
    actual = run_query(
        db,
        PERSON,
        None,
        order_by=order_by,
        limit=2,
        after=cursor,
        select=["handle", "given_name"],
    )
    assert actual == expected
    assert [row[1] for row in actual] == ["Carl", "Dave"]


def test_run_query_after_keyset_rejects_wrong_length_cursor(paging_handles):
    db, _handles = paging_handles
    order_by = [OrderBy("given_name", "asc")]
    with pytest.raises(QueryError):
        run_query(db, PERSON, None, order_by=order_by, after=("only-one-value",))


def test_run_query_select_projects_same_rows_as_sql(paging_handles):
    db, _handles = paging_handles
    query = Query(select=["handle", "given_name", "surname"])
    expected = _sql_rows(db, PERSON, query)
    actual = run_query(db, PERSON, None, select=["handle", "given_name", "surname"], limit=100)
    # SQL's own default ordering (no explicit order_by -> just the implicit
    # handle tiebreaker) has to match too, not just the row content.
    assert actual == expected


def test_run_query_null_ordering_matches_sql(paging_handles):
    """`Family.father_handle` is a genuine nullable SQL column (unlike the
    derived `Person.given_name`) -- exercises `_null_safe_cmp`'s NULL
    placement against SQLite's own verified default (NULL sorts as the
    smallest value in both ASC and DESC).
    """
    db, handles = paging_handles
    for direction in ("asc", "desc"):
        order_by = [OrderBy("father_handle", direction)]
        query = Query(select=["handle", "father_handle"], order_by=order_by, limit=100)
        expected = _sql_rows(db, FAMILY, query)
        actual = run_query(
            db, FAMILY, None, order_by=order_by, limit=100, select=["handle", "father_handle"]
        )
        assert actual == expected, f"direction={direction}"
        # Confirm a real NULL was actually exercised, not a fluke pass.
        assert handles["family_no_father"] in {row[0] for row in actual}


def test_run_query_keyset_seeks_past_a_null_cursor_row(paging_handles):
    """Regression guard for a real bug found (and fixed) in `query.py`'s
    `_compile_keyset`: a plain `col > ?`/`col = ?` seek predicate against a
    bound `NULL` cursor value is always `UNKNOWN` in SQL, which used to mean
    seeking past a `NULL`-sort-column row returned *zero* further rows, even
    though real rows exist after it in the established order. `NULL` sorts
    first in `asc` (SQLite's own default), so page 1 of size 1 is the
    childless family; the cursor taken from it must still be able to seek to
    the two families that have a real `father_handle`.
    """
    db, handles = paging_handles
    order_by = [OrderBy("father_handle", "asc")]

    page1_query = Query(select=["handle", "father_handle"], order_by=order_by, limit=1)
    page1 = _sql_rows(db, FAMILY, page1_query)
    assert page1 == [(handles["family_no_father"], None)]
    cursor = (page1[-1][1], page1[-1][0])

    page2_query = Query(
        select=["handle", "father_handle"], order_by=order_by, limit=10, after=cursor
    )
    expected = _sql_rows(db, FAMILY, page2_query)
    actual = run_query(
        db,
        FAMILY,
        None,
        order_by=order_by,
        limit=10,
        after=cursor,
        select=["handle", "father_handle"],
    )
    assert actual == expected
    assert {row[0] for row in actual} == {
        handles["family_with_father_a"],
        handles["family_with_father_b"],
    }


def test_run_query_keyset_desc_does_not_drop_null_rows_from_later_page(paging_handles):
    """Regression guard for the second, more insidious bug `_compile_keyset`
    had: on a `desc`-sorted column, `NULL` sorts *last* -- but a plain
    `col < ?` is `UNKNOWN` (not `TRUE`) for a `NULL` row regardless of the
    cursor, so the `NULL` row used to silently vanish from every later page
    even with an ordinary, non-NULL cursor. Page 1 (size 1, desc) is
    whichever real-father family sorts first; page 2 must still include
    *both* the other real-father family and the childless (`NULL`) one.
    """
    db, handles = paging_handles
    order_by = [OrderBy("father_handle", "desc")]

    page1_query = Query(select=["handle", "father_handle"], order_by=order_by, limit=1)
    page1 = _sql_rows(db, FAMILY, page1_query)
    assert page1[0][1] is not None
    cursor = (page1[-1][1], page1[-1][0])

    page2_query = Query(
        select=["handle", "father_handle"], order_by=order_by, limit=10, after=cursor
    )
    expected = _sql_rows(db, FAMILY, page2_query)
    actual = run_query(
        db,
        FAMILY,
        None,
        order_by=order_by,
        limit=10,
        after=cursor,
        select=["handle", "father_handle"],
    )
    assert actual == expected
    remaining = {handles["family_with_father_a"], handles["family_with_father_b"]} - {page1[0][0]}
    assert {row[0] for row in actual} == remaining | {handles["family_no_father"]}


def test_run_query_no_order_by_still_deterministic_like_sql(paging_handles):
    """Even with no explicit `order_by`, the SQL path always compiles an
    `ORDER BY handle` tiebreaker (`effective_order_by`) -- `run_query` with
    no `order_by` given has to apply that same implicit tiebreaker, not
    fall back to arbitrary handle-enumeration order, for true parity.
    """
    db, _handles = paging_handles
    query = Query(select=["handle"], limit=100)
    expected = _sql_rows(db, PERSON, query)
    actual = run_query(db, PERSON, None, limit=100, select=["handle"])
    assert actual == expected


def test_run_query_default_return_shape_is_unchanged_without_select(paging_handles):
    """No `select` given still returns full objects, not tuples -- backward
    compatible with every caller from before `select` existed on this path.
    """
    db, handles = paging_handles
    matches = run_query(db, PERSON, None, order_by=[OrderBy("given_name", "asc")], limit=1)
    assert len(matches) == 1
    assert matches[0].handle == handles["Alice"]
    assert matches[0].primary_name.first_name == "Alice"
