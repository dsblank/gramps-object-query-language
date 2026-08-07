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

"""Tests for `evaluator.py` against real (unproxied and proxied) objects.

Deliberately independent of the Flask app / test client, following the same
pattern as `tests/test_private_proxy.py`: a real temporary SQLite-backed
Gramps database, exercised directly. `PrivateProxyDb` is Gramps core's own
proxy, not a Web API construct -- using it here (rather than mocking one)
is the point: the evaluator's correctness under a proxy comes from actually
going through one, not from reimplementing what it does.
"""

import pytest
from gramps.cli.clidbman import CLIDbManager
from gramps.gen.db import DbTxn
from gramps.gen.db.utils import make_database
from gramps.gen.dbstate import DbState
from gramps.gen.lib import (
    Attribute,
    ChildRef,
    Citation,
    Event,
    EventType,
    Family,
    Name,
    Note,
    Person,
    PersonRef,
    Place,
    PlaceName,
    Source,
    Surname,
)
from gramps.gen.proxy import PrivateProxyDb

from gramps_object_query_language.evaluator import (
    evaluate_where,
    get_flat_column,
    get_json_path,
    resolve_column_ref,
)
from gramps_object_query_language.query import (
    CITATION,
    FAMILY,
    PERSON,
    PLACE,
    And,
    CollectionCount,
    Contains,
    Eq,
    Exists,
    Gt,
    In,
    Like,
    Ne,
    Not,
    Or,
    Regex,
    resolve_collection,
    resolve_column_path,
)


def _name(given: str, surname: str) -> Name:
    name = Name()
    name.set_first_name(given)
    surn = Surname()
    surn.set_surname(surname)
    name.set_surname_list([surn])
    return name


def _event_ref(handle):
    from gramps.gen.lib import EventRef

    ref = EventRef()
    ref.set_reference_handle(handle)
    return ref


@pytest.fixture(scope="module")
def db_handles():
    """A temporary SQLite DB with people, a family, events, and a place."""
    dbman = CLIDbManager(DbState())
    dirpath, db_name = dbman.create_new_db_cli("_test_evaluator", dbid="sqlite")
    db = make_database("sqlite")
    db.load(dirpath)

    handles = {}

    with DbTxn("setup", db) as trans:
        place = Place()
        place.set_name(PlaceName(value="Test City"))
        handles["place"] = db.add_place(place, trans)

        birth_father = Event()
        birth_father.set_type(EventType.BIRTH)
        birth_father.set_place_handle(handles["place"])
        handles["birth_father"] = db.add_event(birth_father, trans)

        birth_private = Event()
        birth_private.set_type(EventType.BIRTH)
        birth_private.set_privacy(True)
        handles["birth_private"] = db.add_event(birth_private, trans)

        note = Note()
        note.set("A note about Karl.")
        handles["note"] = db.add_note(note, trans)

        father = Person()
        father.set_primary_name(_name("Karl", "Anderson"))
        father.set_gender(Person.MALE)
        father.set_birth_ref(_event_ref(handles["birth_father"]))
        father.add_note(handles["note"])
        handles["father"] = db.add_person(father, trans)

        mother = Person()
        mother.set_primary_name(_name("Lena", "Baker"))
        mother.set_gender(Person.FEMALE)
        handles["mother"] = db.add_person(mother, trans)

        no_birth = Person()
        no_birth.set_primary_name(_name("Noah", "Case"))
        no_birth.set_gender(Person.MALE)
        handles["no_birth"] = db.add_person(no_birth, trans)

        private_birth_person = Person()
        private_birth_person.set_primary_name(_name("Priva", "Cy"))
        private_birth_person.set_birth_ref(_event_ref(handles["birth_private"]))
        handles["private_birth_person"] = db.add_person(private_birth_person, trans)

        secret_attr = Person()
        secret_attr.set_primary_name(_name("Public", "Person"))
        attr = Attribute()
        attr.set_value("SECRET-ATTR-VALUE")
        attr.set_privacy(True)
        secret_attr.add_attribute(attr)
        handles["secret_attr_person"] = db.add_person(secret_attr, trans)

        child_steve = Person()
        child_steve.set_primary_name(_name("Steve", "Anderson"))
        child_steve.set_gender(Person.MALE)
        handles["child_steve"] = db.add_person(child_steve, trans)

        child_anna = Person()
        child_anna.set_primary_name(_name("Anna", "Anderson"))
        child_anna.set_gender(Person.FEMALE)
        handles["child_anna"] = db.add_person(child_anna, trans)

        family = Family()
        family.set_father_handle(handles["father"])
        family.set_mother_handle(handles["mother"])
        for child_handle in (handles["child_steve"], handles["child_anna"]):
            child_ref = ChildRef()
            child_ref.set_reference_handle(child_handle)
            family.add_child_ref(child_ref)
        handles["family"] = db.add_family(family, trans)

        handles["childless_family"] = db.add_family(Family(), trans)

    yield db, handles

    db.close()
    dbman.remove_database(db_name)


@pytest.fixture(scope="module")
def proxy(db_handles):
    db, _handles = db_handles
    return PrivateProxyDb(db)


# --- flat columns -------------------------------------------------------


def test_get_flat_column_direct_attribute(db_handles):
    db, handles = db_handles
    father = db.get_person_from_handle(handles["father"])
    assert get_flat_column(father, "gender", PERSON) == Person.MALE
    assert get_flat_column(father, "private", PERSON) is False


def test_get_flat_column_derived_given_name_and_surname(db_handles):
    db, handles = db_handles
    father = db.get_person_from_handle(handles["father"])
    assert get_flat_column(father, "given_name", PERSON) == "Karl"
    assert get_flat_column(father, "surname", PERSON) == "Anderson"


def test_get_flat_column_derived_enclosed_by_defaults_empty(db_handles):
    db, handles = db_handles
    place = db.get_place_from_handle(handles["place"])
    assert get_flat_column(place, "enclosed_by", PLACE) == ""


# --- JsonPath -------------------------------------------------------------


def test_get_json_path_into_primary_name(db_handles):
    db, handles = db_handles
    father = db.get_person_from_handle(handles["father"])
    ref = resolve_column_path(PERSON, ["primary_name", "surname_list", 0, "surname"])
    assert get_json_path(father, ref) == "Anderson"


def test_get_json_path_missing_index_returns_none(db_handles):
    db, handles = db_handles
    mother = db.get_person_from_handle(handles["mother"])
    ref = resolve_column_path(PERSON, ["primary_name", "surname_list", 5, "surname"])
    assert get_json_path(mother, ref) is None


# --- RelatedObject ----------------------------------------------------------


def test_resolve_related_object_father_and_mother(db_handles):
    db, handles = db_handles
    family = db.get_family_from_handle(handles["family"])
    father_surname = resolve_column_path(FAMILY, ["father", "surname"])
    mother_surname = resolve_column_path(FAMILY, ["mother", "surname"])
    assert resolve_column_ref(db, family, father_surname, FAMILY) == "Anderson"
    assert resolve_column_ref(db, family, mother_surname, FAMILY) == "Baker"


def test_resolve_related_object_chained_birth_place(db_handles):
    db, handles = db_handles
    father = db.get_person_from_handle(handles["father"])
    ref = resolve_column_path(PERSON, ["birth", "place", "title"])
    # Place.title isn't set explicitly; the place's name feeds `title` via
    # secondary-column population, but what matters here is that the chain
    # resolves to *some* value rather than None -- confirms both hops
    # (person -> event via birth_ref_index, event -> place) were followed.
    assert resolve_column_ref(db, father, ref, PERSON) is not None


def test_resolve_related_object_no_birth_ref_returns_none(db_handles):
    db, handles = db_handles
    person = db.get_person_from_handle(handles["no_birth"])
    ref = resolve_column_path(PERSON, ["birth", "gramps_id"])
    assert resolve_column_ref(db, person, ref, PERSON) is None


# --- evaluate_where ----------------------------------------------------------


def test_evaluate_where_eq_and_ne(db_handles):
    db, handles = db_handles
    father = db.get_person_from_handle(handles["father"])
    assert evaluate_where(db, father, Eq("surname", "Anderson"), PERSON) is True
    assert evaluate_where(db, father, Ne("surname", "Anderson"), PERSON) is False


def test_evaluate_where_gt_excludes_none(db_handles):
    db, handles = db_handles
    person = db.get_person_from_handle(handles["no_birth"])
    ref = resolve_column_path(PERSON, ["birth", "gramps_id"])
    # Comparison base class is generic; Gt(ref, "E0000") against a None left
    # side must not raise, and must not match.
    assert evaluate_where(db, person, Gt(ref, "E0000"), PERSON) is False


def test_evaluate_where_like(db_handles):
    db, handles = db_handles
    father = db.get_person_from_handle(handles["father"])
    assert evaluate_where(db, father, Like("surname", "And%"), PERSON) is True
    assert evaluate_where(db, father, Like("surname", "Zzz%"), PERSON) is False


def test_evaluate_where_regex(db_handles):
    db, handles = db_handles
    father = db.get_person_from_handle(handles["father"])
    assert evaluate_where(db, father, Regex("surname", "^And.*son$"), PERSON) is True
    assert evaluate_where(db, father, Regex("surname", "^Zzz"), PERSON) is False
    # Unanchored -- a bare "nder" (no ^/$) still matches as a substring
    # search, same as re.search, not re.fullmatch.
    assert evaluate_where(db, father, Regex("surname", "nder"), PERSON) is True
    # Case-sensitive, unlike Like/Contains just above -- mirrors gramps
    # core's `dbapi/sqlite.py` `regexp()` UDF, a plain `re.search` with no
    # IGNORECASE flag, so this evaluator path stays exactly in step with a
    # real SQLite backend (see test_sql_and_evaluator_agree_on_regex below).
    assert evaluate_where(db, father, Regex("surname", "^and"), PERSON) is False


def test_evaluate_where_contains(db_handles):
    db, handles = db_handles
    father = db.get_person_from_handle(handles["father"])
    # Plain substring test, not a LIKE pattern -- no wildcard characters
    # involved, and matches case-insensitively (mirroring SQLite's default
    # LIKE behavior, same as `Like` above).
    assert evaluate_where(db, father, Contains("surname", "nder"), PERSON) is True
    assert evaluate_where(db, father, Contains("surname", "NDER"), PERSON) is True
    assert evaluate_where(db, father, Contains("surname", "zzz"), PERSON) is False


def test_evaluate_where_in(db_handles):
    db, handles = db_handles
    father = db.get_person_from_handle(handles["father"])
    assert evaluate_where(db, father, In("surname", ["Anderson", "Baker"]), PERSON) is True
    assert evaluate_where(db, father, In("surname", ["Baker"]), PERSON) is False


def test_evaluate_where_and_or_not(db_handles):
    db, handles = db_handles
    father = db.get_person_from_handle(handles["father"])
    assert (
        evaluate_where(
            db, father, And(Eq("gender", Person.MALE), Eq("surname", "Anderson")), PERSON
        )
        is True
    )
    assert (
        evaluate_where(db, father, Or(Eq("surname", "Baker"), Eq("surname", "Anderson")), PERSON)
        is True
    )
    assert evaluate_where(db, father, Not(Eq("surname", "Anderson")), PERSON) is False


# --- three-valued logic (UNKNOWN propagation through Not/And/Or) -------------
#
# SQL's `NOT`/`AND`/`OR` follow three-valued logic: a comparison against a
# missing value is UNKNOWN, not False, and `NOT UNKNOWN` is still UNKNOWN,
# not True. These tests lock in `_evaluate_tri`'s handling of that -- each
# one has a real SQL-side counterpart it must agree with (see the
# SQL-vs-evaluator agreement tests further down), but is checked here in
# isolation too since these are the exact shapes that diverged before
# `_evaluate_tri` existed (a plain `bool`-returning recursion collapsed
# UNKNOWN to False at each leaf, so `Not` wrapping one flipped it to True
# -- wrong).


def test_evaluate_where_not_of_ordering_comparison_against_missing_value(db_handles):
    # Before the fix: Not(Gt(...)) against a missing value incorrectly
    # returned True (a plain False, negated). SQL's NOT UNKNOWN stays
    # UNKNOWN -- excluded either way, matching test_evaluate_where_gt_excludes_none
    # above for the un-negated form.
    db, handles = db_handles
    person = db.get_person_from_handle(handles["no_birth"])
    ref = resolve_column_path(PERSON, ["birth", "gramps_id"])
    assert evaluate_where(db, person, Gt(ref, "E0000"), PERSON) is False
    assert evaluate_where(db, person, Not(Gt(ref, "E0000")), PERSON) is False


def test_evaluate_where_not_of_in_against_missing_value(db_handles):
    db, handles = db_handles
    person = db.get_person_from_handle(handles["no_birth"])
    ref = resolve_column_path(PERSON, ["birth", "gramps_id"])
    assert evaluate_where(db, person, In(ref, ["E0000"]), PERSON) is False
    assert evaluate_where(db, person, Not(In(ref, ["E0000"])), PERSON) is False


def test_evaluate_where_not_of_like_against_missing_value(db_handles):
    db, handles = db_handles
    person = db.get_person_from_handle(handles["no_birth"])
    ref = resolve_column_path(PERSON, ["birth", "gramps_id"])
    assert evaluate_where(db, person, Like(ref, "E%"), PERSON) is False
    assert evaluate_where(db, person, Not(Like(ref, "E%")), PERSON) is False


def test_evaluate_where_not_of_regex_against_missing_value(db_handles):
    db, handles = db_handles
    person = db.get_person_from_handle(handles["no_birth"])
    ref = resolve_column_path(PERSON, ["birth", "gramps_id"])
    assert evaluate_where(db, person, Regex(ref, "^E"), PERSON) is False
    assert evaluate_where(db, person, Not(Regex(ref, "^E")), PERSON) is False


def test_evaluate_where_and_false_dominates_unknown_sibling(db_handles):
    # And(False, UNKNOWN) must be a definite False, not UNKNOWN -- checking
    # for a False sibling has to happen *before* checking for an UNKNOWN
    # one, the same precedence SQL's AND uses. Getting this order backwards
    # would make Not(And(...)) wrongly stay excluded here instead of
    # matching.
    db, handles = db_handles
    father = db.get_person_from_handle(handles["father"])  # surname "Anderson", no death ref
    death_gramps_id = resolve_column_path(PERSON, ["death", "gramps_id"])
    condition = And(Eq("surname", "Baker"), Gt(death_gramps_id, "E0000"))
    assert evaluate_where(db, father, condition, PERSON) is False
    assert evaluate_where(db, father, Not(condition), PERSON) is True


def test_evaluate_where_or_true_dominates_unknown_sibling(db_handles):
    # Or(True, UNKNOWN) must be a definite True -- same dominance rule,
    # mirrored for OR.
    db, handles = db_handles
    father = db.get_person_from_handle(handles["father"])  # surname "Anderson", no death ref
    death_gramps_id = resolve_column_path(PERSON, ["death", "gramps_id"])
    condition = Or(Eq("surname", "Anderson"), Gt(death_gramps_id, "E0000"))
    assert evaluate_where(db, father, condition, PERSON) is True
    assert evaluate_where(db, father, Not(condition), PERSON) is False


def test_evaluate_where_and_of_true_and_unknown_excludes_both_ways(db_handles):
    # And(True, UNKNOWN) is UNKNOWN, not False or True -- so neither the
    # condition nor its negation matches. The "obviously wrong" bug this
    # guards against: treating And(True, UNKNOWN) as True (so Not incorrectly
    # excludes) or as False (so Not incorrectly includes) -- it's neither.
    db, handles = db_handles
    father = db.get_person_from_handle(handles["father"])  # surname "Anderson", no death ref
    death_gramps_id = resolve_column_path(PERSON, ["death", "gramps_id"])
    condition = And(Eq("surname", "Anderson"), Gt(death_gramps_id, "E0000"))
    assert evaluate_where(db, father, condition, PERSON) is False
    assert evaluate_where(db, father, Not(condition), PERSON) is False


# --- Exists (one-to-many membership) -----------------------------------------


def test_evaluate_where_exists_matches_when_condition_satisfied(db_handles):
    db, handles = db_handles
    family = db.get_family_from_handle(handles["family"])
    children = resolve_collection(FAMILY, "children")
    condition = Eq("given_name", "Steve")
    assert evaluate_where(db, family, Exists(children, condition), FAMILY) is True


def test_evaluate_where_exists_excludes_when_no_child_matches(db_handles):
    db, handles = db_handles
    family = db.get_family_from_handle(handles["family"])
    children = resolve_collection(FAMILY, "children")
    condition = Eq("given_name", "Zelda")
    assert evaluate_where(db, family, Exists(children, condition), FAMILY) is False


def test_evaluate_where_exists_no_condition_is_any_child_at_all(db_handles):
    db, handles = db_handles
    family = db.get_family_from_handle(handles["family"])
    childless_family = db.get_family_from_handle(handles["childless_family"])
    children = resolve_collection(FAMILY, "children")
    assert evaluate_where(db, family, Exists(children), FAMILY) is True
    assert evaluate_where(db, childless_family, Exists(children), FAMILY) is False


def test_evaluate_where_not_exists(db_handles):
    # Exists always resolves to a definite True/False -- not exists(...) is
    # ordinary boolean negation, no three-valued-logic wrinkle (unlike Not
    # wrapping an ordinary comparison against a missing value, see above).
    db, handles = db_handles
    family = db.get_family_from_handle(handles["family"])
    childless_family = db.get_family_from_handle(handles["childless_family"])
    children = resolve_collection(FAMILY, "children")
    assert evaluate_where(db, family, Not(Exists(children)), FAMILY) is False
    assert evaluate_where(db, childless_family, Not(Exists(children)), FAMILY) is True


def test_evaluate_where_exists_flat_handle_list(db_handles):
    # notes: a flat handle list (no ref_field extraction), unlike children's
    # ref-object list -- proves both Collection shapes work in the evaluator.
    db, handles = db_handles
    father = db.get_person_from_handle(handles["father"])
    mother = db.get_person_from_handle(handles["mother"])
    notes = resolve_collection(PERSON, "notes")
    assert evaluate_where(db, father, Exists(notes), PERSON) is True
    assert evaluate_where(db, mother, Exists(notes), PERSON) is False


def test_evaluate_where_exists_condition_can_chain_relationships(db_handles):
    # The condition compiles against the collection's target type (Person),
    # so it can cross Person's own relationships too.
    db, handles = db_handles
    family = db.get_family_from_handle(handles["family"])
    children = resolve_collection(FAMILY, "children")
    ref = resolve_column_path(PERSON, ["birth", "gramps_id"])
    condition = Eq(ref, "does-not-exist")
    assert evaluate_where(db, family, Exists(children, condition), FAMILY) is False


def test_sql_and_evaluator_agree_on_exists(db_handles):
    """Runs the same Exists AST through the real compiled SQLite query
    (against the fixture's own underlying Gramps SQLite backend, which uses
    the same table/json_data shape query.py targets) and through
    evaluate_where, and checks they agree -- the same style of regression
    guard as test_sql_and_evaluator_agree_on_not_with_missing_values below,
    for the new Exists node instead of Not/And/Or.
    """
    from gramps_object_query_language.query import Dialect, Query, compile_query

    db, handles = db_handles
    children = resolve_collection(FAMILY, "children")
    wheres = [
        Exists(children, Eq("given_name", "Steve")),
        Exists(children, Eq("given_name", "Zelda")),
        Not(Exists(children, Eq("given_name", "Steve"))),
        Exists(children),
    ]
    families = {
        "family": db.get_family_from_handle(handles["family"]),
        "childless_family": db.get_family_from_handle(handles["childless_family"]),
    }
    for where in wheres:
        sql, params = compile_query(
            FAMILY, Query(select=["handle"], where=where), dialect=Dialect.SQLITE
        )
        db.dbapi.execute(sql, params)
        sql_matches = {row[0] for row in db.dbapi.fetchall()}
        for key, family in families.items():
            expected = handles[key] in sql_matches
            actual = evaluate_where(db, family, where, FAMILY)
            assert actual == expected, f"{where!r} on {key!r}: SQL={expected} eval={actual}"


def test_sql_and_evaluator_agree_on_regex(db_handles):
    """Same style of regression guard as test_sql_and_evaluator_agree_on_exists
    above, but for Regex specifically: runs the same AST through the
    fixture's real underlying Gramps SQLite backend (`db.dbapi.execute`) --
    which already has gramps core's `regexp` UDF registered on it, the same
    way any real deployment does, no manual registration needed here -- and
    through `evaluate_where`, and checks they agree. Includes a
    lowercase-anchored pattern specifically to catch a case-sensitivity
    mismatch (this evaluator path is deliberately case-sensitive, unlike
    Like/Contains -- see test_evaluate_where_regex), and a missing-value
    case to catch a three-valued-logic mismatch.
    """
    from gramps_object_query_language.query import Dialect, Query, compile_query

    db, handles = db_handles
    birth_gramps_id = resolve_column_path(PERSON, ["birth", "gramps_id"])
    wheres = [
        Regex("surname", "^And.*son$"),
        Regex("surname", "^and"),  # lowercase -- must not match case-sensitively
        Regex("surname", "ake"),  # unanchored substring, matches "Baker"
        Not(Regex("surname", "^And.*son$")),
        Regex(birth_gramps_id, "^E"),  # missing for "no_birth" -- UNKNOWN
        Not(Regex(birth_gramps_id, "^E")),
    ]
    people = {
        "father": db.get_person_from_handle(handles["father"]),
        "mother": db.get_person_from_handle(handles["mother"]),
        "no_birth": db.get_person_from_handle(handles["no_birth"]),
    }
    for where in wheres:
        sql, params = compile_query(
            PERSON, Query(select=["handle"], where=where), dialect=Dialect.SQLITE
        )
        db.dbapi.execute(sql, params)
        sql_matches = {row[0] for row in db.dbapi.fetchall()}
        for key, person in people.items():
            expected = handles[key] in sql_matches
            actual = evaluate_where(db, person, where, PERSON)
            assert actual == expected, f"{where!r} on {key!r}: SQL={expected} eval={actual}"


# --- CollectionCount / count(...) (Collection cardinality) --------------------


def test_evaluate_where_count_matches_number_of_children(db_handles):
    db, handles = db_handles
    family = db.get_family_from_handle(handles["family"])
    children = resolve_collection(FAMILY, "children")
    assert evaluate_where(db, family, Gt(CollectionCount(children), 1), FAMILY) is True
    assert evaluate_where(db, family, Gt(CollectionCount(children), 2), FAMILY) is False


def test_evaluate_where_count_zero_for_childless_family(db_handles):
    db, handles = db_handles
    childless_family = db.get_family_from_handle(handles["childless_family"])
    children = resolve_collection(FAMILY, "children")
    assert evaluate_where(db, childless_family, Gt(CollectionCount(children), 0), FAMILY) is False
    assert evaluate_where(db, childless_family, Eq(CollectionCount(children), 0), FAMILY) is True


def test_evaluate_where_count_with_condition(db_handles):
    # family has one male child (Steve) and one female child (Anna).
    db, handles = db_handles
    family = db.get_family_from_handle(handles["family"])
    children = resolve_collection(FAMILY, "children")
    male_children = CollectionCount(children, Eq("gender", Person.MALE))
    assert evaluate_where(db, family, Eq(male_children, 1), FAMILY) is True
    assert evaluate_where(db, family, Eq(male_children, 2), FAMILY) is False


def test_evaluate_where_count_flat_handle_list(db_handles):
    db, handles = db_handles
    father = db.get_person_from_handle(handles["father"])
    mother = db.get_person_from_handle(handles["mother"])
    notes = resolve_collection(PERSON, "notes")
    assert evaluate_where(db, father, Eq(CollectionCount(notes), 1), PERSON) is True
    assert evaluate_where(db, mother, Eq(CollectionCount(notes), 0), PERSON) is True


def test_sql_and_evaluator_agree_on_count(db_handles):
    """Same style of regression guard as the Exists/Not agreement tests --
    runs the same CollectionCount-based `where` AST through the fixture's
    real underlying Gramps SQLite backend and through `evaluate_where`, and
    checks they agree.
    """
    from gramps_object_query_language.query import Dialect, Query, compile_query

    db, handles = db_handles
    children = resolve_collection(FAMILY, "children")
    wheres = [
        Gt(CollectionCount(children), 1),
        Gt(CollectionCount(children), 2),
        Eq(CollectionCount(children, Eq("gender", Person.MALE)), 1),
        In(CollectionCount(children), [0, 1]),
    ]
    families = {
        "family": db.get_family_from_handle(handles["family"]),
        "childless_family": db.get_family_from_handle(handles["childless_family"]),
    }
    for where in wheres:
        sql, params = compile_query(
            FAMILY, Query(select=["handle"], where=where), dialect=Dialect.SQLITE
        )
        db.dbapi.execute(sql, params)
        sql_matches = {row[0] for row in db.dbapi.fetchall()}
        for key, family in families.items():
            expected = handles[key] in sql_matches
            actual = evaluate_where(db, family, where, FAMILY)
            assert actual == expected, f"{where!r} on {key!r}: SQL={expected} eval={actual}"


# --- SQL vs evaluator agreement (the regression guard for the above) ---------


def test_sql_and_evaluator_agree_on_not_with_missing_values():
    """Runs the same `where` AST through the real SQLite compiler and
    through `evaluate_where` against equivalent data, for every shape that
    diverged before `_evaluate_tri` -- this is the test that would have
    caught that bug, and is meant to catch any future one shaped like it.
    Uses a plain flat column (`gramps_id`), not a `JsonPath`, so the
    evaluator side can use a simple stand-in object instead of a real
    Gramps object (`get_flat_column` is a plain `getattr`; `JsonPath`
    resolution needs `json_utils.object_to_dict`, which does not).
    """
    import sqlite3

    from gramps_object_query_language.query import Dialect, Query, compile_query

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE person (handle TEXT, gramps_id TEXT)")
    conn.execute("INSERT INTO person VALUES ('has_id', 'E0001')")
    conn.execute("INSERT INTO person VALUES ('no_id', NULL)")

    class Row:
        def __init__(self, gramps_id):
            self.gramps_id = gramps_id

    rows = {"has_id": Row("E0001"), "no_id": Row(None)}

    wheres = [
        Gt("gramps_id", "E0002"),
        Not(Gt("gramps_id", "E0002")),
        In("gramps_id", ["E0009"]),
        Not(In("gramps_id", ["E0009"])),
        Like("gramps_id", "Z%"),
        Not(Like("gramps_id", "Z%")),
        And(Eq("gramps_id", "E0001"), Not(Gt("gramps_id", "E0002"))),
        Or(Eq("gramps_id", "nope"), Not(Gt("gramps_id", "E0002"))),
        Not(And(Eq("gramps_id", "nope"), Gt("gramps_id", "E0002"))),
        Not(Or(Eq("gramps_id", "E0001"), Gt("gramps_id", "E0002"))),
    ]

    for where in wheres:
        sql, params = compile_query(
            PERSON, Query(select=["handle"], where=where), dialect=Dialect.SQLITE
        )
        sql_matches = {row[0] for row in conn.execute(sql, params).fetchall()}
        for handle, row in rows.items():
            expected = handle in sql_matches
            actual = evaluate_where(None, row, where, PERSON)
            assert actual == expected, f"{where!r} on {handle!r}: SQL={expected} eval={actual}"


# --- Proxy correctness -------------------------------------------------------


def test_proxy_masks_private_birth_relationship(db_handles, proxy):
    """A private birth event must resolve to None through the proxy, but a
    real value through the raw db -- the same object, two different views.
    """
    db, handles = db_handles
    ref = resolve_column_path(PERSON, ["birth", "gramps_id"])

    raw_person = db.get_person_from_handle(handles["private_birth_person"])
    assert resolve_column_ref(db, raw_person, ref, PERSON) is not None

    proxied_person = proxy.get_person_from_handle(handles["private_birth_person"])
    assert resolve_column_ref(proxy, proxied_person, ref, PERSON) is None


def test_proxy_strips_private_attribute_from_json_path(db_handles, proxy):
    """Reproduces the #911 review's §4 leak scenario and confirms the
    evaluator doesn't have it: a JsonPath into a private sub-object
    (`attribute_list`) must come back empty through the proxy, even though
    the containing Person is public. No SQL involved, so nothing to guard --
    correctness follows entirely from `sanitize_person` already having
    dropped the attribute before this code ever sees the object.
    """
    db, handles = db_handles
    ref = resolve_column_path(PERSON, ["attribute_list", 0, "value"])

    raw_person = db.get_person_from_handle(handles["secret_attr_person"])
    assert get_json_path(raw_person, ref) == "SECRET-ATTR-VALUE"

    proxied_person = proxy.get_person_from_handle(handles["secret_attr_person"])
    assert get_json_path(proxied_person, ref) is None


def test_proxy_where_oracle_cannot_confirm_masked_value(db_handles, proxy):
    """Even as a `where` filter (not just `select`), the masked attribute
    must not distinguish the real value from a wrong guess -- both `Eq`
    against the true value and a probe must evaluate identically (False).
    """
    db, handles = db_handles
    ref = resolve_column_path(PERSON, ["attribute_list", 0, "value"])
    proxied_person = proxy.get_person_from_handle(handles["secret_attr_person"])

    assert evaluate_where(proxy, proxied_person, Eq(ref, "SECRET-ATTR-VALUE"), PERSON) is False
    assert evaluate_where(proxy, proxied_person, Eq(ref, "totally-wrong-guess"), PERSON) is False


# --- Expanded Collection registry / Citation.source -----------------------
#
# The full registry is verified exhaustively (against resolve_collection
# directly) in test_query.py -- these confirm the evaluator side, which
# needed no per-collection code of its own (resolve_column_ref/_evaluate_tri
# are already fully generic), still resolves a newly-registered collection
# correctly, and in particular that the self-referencing one
# (Person.associations -> Person) works without the SQL-side table-name
# aliasing concern -- pure Python object fetches have no such collision.


@pytest.fixture(scope="module")
def assoc_db_handles():
    dbman = CLIDbManager(DbState())
    dirpath, db_name = dbman.create_new_db_cli("_test_evaluator_assoc", dbid="sqlite")
    db = make_database("sqlite")
    db.load(dirpath)

    handles = {}
    with DbTxn("setup", db) as trans:
        bob = Person()
        bob.set_primary_name(_name("Bob", "Jones"))
        handles["bob"] = db.add_person(bob, trans)

        alice = Person()
        alice.set_primary_name(_name("Alice", "Smith"))
        ref = PersonRef()
        ref.set_reference_handle(handles["bob"])
        alice.add_person_ref(ref)
        handles["alice"] = db.add_person(alice, trans)

        source = Source()
        source.set_title("Census Records")
        handles["source"] = db.add_source(source, trans)

        citation = Citation()
        citation.set_reference_handle(handles["source"])
        handles["citation"] = db.add_citation(citation, trans)

    yield db, handles

    db.close()
    dbman.remove_database(db_name)


def test_evaluate_where_associations_self_reference(assoc_db_handles):
    db, handles = assoc_db_handles
    alice = db.get_person_from_handle(handles["alice"])
    bob = db.get_person_from_handle(handles["bob"])
    associations = resolve_collection(PERSON, "associations")
    condition = Eq("given_name", "Bob")
    assert evaluate_where(db, alice, Exists(associations, condition), PERSON) is True
    assert evaluate_where(db, bob, Exists(associations, condition), PERSON) is False


def test_evaluate_where_citation_source_relationship(assoc_db_handles):
    db, handles = assoc_db_handles
    citation = db.get_citation_from_handle(handles["citation"])
    ref = resolve_column_path(CITATION, ["source", "title"])
    assert resolve_column_ref(db, citation, ref, CITATION) == "Census Records"
    assert evaluate_where(db, citation, Eq(ref, "Census Records"), CITATION) is True
