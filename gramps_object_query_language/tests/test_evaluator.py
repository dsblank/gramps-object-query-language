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

import os

import pytest
from gramps.cli.clidbman import CLIDbManager
from gramps.gen.db import DbTxn
from gramps.gen.db.utils import make_database
from gramps.gen.dbstate import DbState
from gramps.gen.lib import (
    Attribute,
    AttributeType,
    ChildRef,
    ChildRefType,
    Citation,
    Date,
    Event,
    EventRef,
    EventType,
    Family,
    Name,
    Note,
    Person,
    PersonRef,
    Place,
    PlaceName,
    Source,
    EventRoleType,
    Surname,
    Url,
    UrlType,
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
    EVENT,
    FAMILY,
    NOTE,
    PERSON,
    PLACE,
    And,
    BacklinkClassFilter,
    CollectionCount,
    Contains,
    Eq,
    Exists,
    Gt,
    In,
    JsonArrayCount,
    JsonArrayExists,
    JsonPath,
    Length,
    Like,
    Ne,
    Not,
    Or,
    Regex,
    resolve_any_path,
    resolve_collection,
    resolve_column_path,
    resolve_length_path,
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


def test_evaluate_where_empty_in_matches_nothing(db_handles):
    """Even a missing value: an empty list is plain FALSE, like the SQL's
    `1 = 0`, so NOT of it is TRUE."""
    db, handles = db_handles
    father = db.get_person_from_handle(handles["father"])
    assert evaluate_where(db, father, In("surname", []), PERSON) is False
    assert evaluate_where(db, father, In(JsonPath(("nope",)), []), PERSON) is False
    assert evaluate_where(db, father, Not(In("surname", [])), PERSON) is True


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


@pytest.fixture(scope="module")
def like_db():
    """A separate SQLite DB whose surnames exercise `LIKE`'s edge cases --
    kept apart from `db_handles` so its people don't shift that fixture's
    counts."""
    dbman = CLIDbManager(DbState())
    dirpath, db_name = dbman.create_new_db_cli("_test_evaluator_like", dbid="sqlite")
    db = make_database("sqlite")
    db.load(dirpath)
    surnames = {
        "smith": "Smith",
        "percent": "Paid 100%",
        "hundred": "Paid 1000",
        "backslash": "Back\\slash",
        "no_backslash": "Backslash",
        "underscore": "Under_score",
        "newline": "Multi\nline",
        "accented": "Élise",
    }
    handles = {}
    with DbTxn("setup", db) as trans:
        for key, surname in surnames.items():
            person = Person()
            person.set_primary_name(_name("X", surname))
            handles[key] = db.add_person(person, trans)
    yield db, handles
    db.close()
    dbman.remove_database(db_name)


def test_sql_and_evaluator_agree_on_like_and_contains(like_db):
    """Same style of regression guard as test_sql_and_evaluator_agree_on_regex,
    for `Like`/`Contains`: case-insensitivity, `\\` escapes, wildcards across
    a newline, and literal `%`/`_`/`\\` in a substring. Leaves out a
    case-*mismatched* non-ASCII pattern (`'é%'` vs `"Élise"`) -- SQLite
    only folds ASCII, the evaluator folds everything; see `_like_op`.
    """
    from gramps_object_query_language.query import Dialect, Query, compile_query

    db, handles = like_db
    wheres = [
        Like("surname", "smi%"),
        Like("surname", "SMITH"),
        Like("surname", "paid 100\\%"),  # literal % -- not "Paid 1000"
        Like("surname", "paid 100%"),  # wildcard % -- both
        Like("surname", "back\\\\slash"),  # escaped backslash
        Like("surname", "back\\slash"),  # lone backslash escapes 's' -- no literal '\'
        Like("surname", "under\\_score"),
        Like("surname", "multi%"),  # % spans the newline
        Like("surname", "multi_line"),  # _ matches the newline
        Like("surname", "Él%"),
        Not(Like("surname", "%a%")),
        Contains("surname", "MIT"),
        Contains("surname", "100%"),
        Contains("surname", "k\\s"),
        Contains("surname", "r_s"),
    ]
    people = {key: db.get_person_from_handle(handle) for key, handle in handles.items()}
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
    # Spot-check the cases above actually discriminate, not just agree.
    assert evaluate_where(db, people["percent"], Like("surname", "paid 100\\%"), PERSON)
    assert not evaluate_where(db, people["hundred"], Like("surname", "paid 100\\%"), PERSON)
    assert evaluate_where(db, people["newline"], Like("surname", "multi%"), PERSON)
    # A lone `\` before an ordinary character escapes it rather than
    # matching a literal backslash -- a change from SQLite's old no-ESCAPE
    # rendering, where the pattern `back\slash` matched "Back\slash" instead.
    assert evaluate_where(db, people["no_backslash"], Like("surname", "back\\slash"), PERSON)
    assert not evaluate_where(db, people["backslash"], Like("surname", "back\\slash"), PERSON)


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


# --- Length / len(...) (intra-record JSON array cardinality) ------------------


def test_evaluate_where_length_counts_json_array(db_handles):
    db, handles = db_handles
    father = db.get_person_from_handle(handles["father"])
    surnames = resolve_length_path(PERSON, ("primary_name", "surname_list"))
    assert evaluate_where(db, father, Eq(surnames, 1), PERSON) is True
    assert evaluate_where(db, father, Gt(surnames, 1), PERSON) is False


def test_evaluate_where_length_zero_for_empty_list(db_handles):
    db, handles = db_handles
    mother = db.get_person_from_handle(handles["mother"])
    attrs = resolve_length_path(PERSON, ("attribute_list",))
    assert evaluate_where(db, mother, Eq(attrs, 0), PERSON) is True
    assert evaluate_where(db, mother, Gt(attrs, 0), PERSON) is False


def test_evaluate_where_length_relationship_chained(db_handles):
    db, handles = db_handles
    family = db.get_family_from_handle(handles["family"])
    father_attrs = resolve_length_path(FAMILY, ("father", "attribute_list"))
    assert evaluate_where(db, family, Eq(father_attrs, 0), FAMILY) is True


def test_proxy_excludes_private_item_from_length(db_handles, proxy):
    # secret_attr_person has one attribute, marked private -- the proxy
    # drops it from attribute_list entirely (not just masking a field on
    # it), so len() through the proxy comes back 0, matching the object the
    # proxy's caller is actually allowed to see. No separate privacy
    # handling needed here -- see the module docstring.
    db, handles = db_handles
    attrs = resolve_length_path(PERSON, ("attribute_list",))

    raw_person = db.get_person_from_handle(handles["secret_attr_person"])
    assert evaluate_where(db, raw_person, Eq(attrs, 1), PERSON) is True

    proxied_person = proxy.get_person_from_handle(handles["secret_attr_person"])
    assert evaluate_where(proxy, proxied_person, Eq(attrs, 0), PERSON) is True


def test_sql_and_evaluator_agree_on_length(db_handles):
    from gramps_object_query_language.query import Dialect, Query, compile_query

    db, handles = db_handles
    attrs = resolve_length_path(PERSON, ("attribute_list",))
    wheres = [Eq(attrs, 0), Gt(attrs, 0), In(attrs, [0, 1])]
    people = {
        key: db.get_person_from_handle(handles[key])
        for key in ("father", "mother", "no_birth", "secret_attr_person")
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


# --- any()/len() unified with exists()/count() (JsonArrayExists/JsonArrayCount) -----


def test_evaluate_where_json_array_exists_matches_element(db_handles):
    db, handles = db_handles
    father = db.get_person_from_handle(handles["father"])
    ref, element_spec = resolve_any_path(PERSON, ("primary_name", "surname_list"))
    node = JsonArrayExists(ref, element_spec, Eq(JsonPath(("surname",), base_column="je.value"), "Anderson"))
    assert evaluate_where(db, father, node, PERSON) is True
    node_no_match = JsonArrayExists(ref, element_spec, Eq(JsonPath(("surname",), base_column="je.value"), "Nobody"))
    assert evaluate_where(db, father, node_no_match, PERSON) is False


def test_evaluate_where_json_array_exists_no_condition(db_handles):
    db, handles = db_handles
    mother = db.get_person_from_handle(handles["mother"])
    ref, element_spec = resolve_any_path(PERSON, ("attribute_list",))
    node = JsonArrayExists(ref, element_spec, None)
    assert evaluate_where(db, mother, node, PERSON) is False  # mother has no attributes


def test_evaluate_where_json_array_exists_relationship_chained(db_handles):
    db, handles = db_handles
    family = db.get_family_from_handle(handles["family"])
    ref, element_spec = resolve_any_path(FAMILY, ("father", "primary_name", "surname_list"))
    node = JsonArrayExists(ref, element_spec, Eq(JsonPath(("surname",), base_column="je.value"), "Anderson"))
    assert evaluate_where(db, family, node, FAMILY) is True


def test_evaluate_where_json_array_count(db_handles):
    db, handles = db_handles
    father = db.get_person_from_handle(handles["father"])
    ref, element_spec = resolve_any_path(PERSON, ("primary_name", "surname_list"))
    count = JsonArrayCount(ref, element_spec, Eq(JsonPath(("surname",), base_column="je.value"), "Anderson"))
    assert evaluate_where(db, father, Eq(count, 1), PERSON) is True
    assert evaluate_where(db, father, Eq(count, 0), PERSON) is False


def test_proxy_excludes_private_item_from_json_array_exists(db_handles, proxy):
    # secret_attr_person has one attribute, marked private -- the proxy
    # drops it from attribute_list entirely (same reasoning as Length's own
    # equivalent test above), so any(...) through the proxy finds nothing
    # where the raw db finds the private attribute's own value.
    db, handles = db_handles
    ref, element_spec = resolve_any_path(PERSON, ("attribute_list",))
    node = JsonArrayExists(ref, element_spec, Eq(JsonPath(("value",), base_column="je.value"), "SECRET-ATTR-VALUE"))

    raw_person = db.get_person_from_handle(handles["secret_attr_person"])
    assert evaluate_where(db, raw_person, node, PERSON) is True

    proxied_person = proxy.get_person_from_handle(handles["secret_attr_person"])
    assert evaluate_where(proxy, proxied_person, node, PERSON) is False


def test_sql_and_evaluator_agree_on_json_array_exists(db_handles):
    from gramps_object_query_language.query import Dialect, Query, compile_query

    db, handles = db_handles
    ref, element_spec = resolve_any_path(PERSON, ("primary_name", "surname_list"))
    wheres = [
        JsonArrayExists(ref, element_spec, Eq(JsonPath(("surname",), base_column="je.value"), "Anderson")),
        JsonArrayExists(ref, element_spec, Eq(JsonPath(("surname",), base_column="je.value"), "Nobody")),
        JsonArrayExists(ref, element_spec, None),
    ]
    people = {
        key: db.get_person_from_handle(handles[key])
        for key in ("father", "mother", "no_birth", "secret_attr_person")
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


# --- Backlinks (reverse references, via db.find_backlink_handles) ------------
#
# Kept in its own small, dedicated fixture (mirroring assoc_db_handles above)
# rather than added onto db_handles -- the private Person here is purpose-
# built for the proxy test below, and isolating it avoids perturbing any of
# db_handles' own privacy-sensitive assertions elsewhere in this file.


@pytest.fixture(scope="module")
def backlinks_db_handles():
    dbman = CLIDbManager(DbState())
    dirpath, db_name = dbman.create_new_db_cli("_test_evaluator_backlinks", dbid="sqlite")
    db = make_database("sqlite")
    db.load(dirpath)

    handles = {}
    with DbTxn("setup", db) as trans:
        referenced_note = Note()
        referenced_note.set("Referenced by a person and a family.")
        handles["referenced_note"] = db.add_note(referenced_note, trans)

        orphan_note = Note()
        orphan_note.set("Nothing points here.")
        handles["orphan_note"] = db.add_note(orphan_note, trans)

        person = Person()
        person.set_primary_name(_name("Fred", "Blank"))
        person.add_note(handles["referenced_note"])
        handles["person"] = db.add_person(person, trans)

        family = Family()
        family.add_note(handles["referenced_note"])
        handles["family"] = db.add_family(family, trans)

        privately_referenced_note = Note()
        privately_referenced_note.set("Only a private Person points here.")
        handles["privately_referenced_note"] = db.add_note(privately_referenced_note, trans)

        private_person = Person()
        private_person.set_primary_name(_name("Secret", "Person"))
        private_person.set_privacy(True)
        private_person.add_note(handles["privately_referenced_note"])
        handles["private_person"] = db.add_person(private_person, trans)

    yield db, handles

    db.close()
    dbman.remove_database(db_name)


@pytest.fixture(scope="module")
def backlinks_proxy(backlinks_db_handles):
    db, _handles = backlinks_db_handles
    return PrivateProxyDb(db)


def test_exists_backlinks_true_for_referenced_note(backlinks_db_handles):
    db, handles = backlinks_db_handles
    note = db.get_note_from_handle(handles["referenced_note"])
    assert evaluate_where(db, note, Exists(resolve_collection(NOTE, "backlinks")), NOTE) is True


def test_exists_backlinks_false_for_orphan_note(backlinks_db_handles):
    db, handles = backlinks_db_handles
    note = db.get_note_from_handle(handles["orphan_note"])
    assert evaluate_where(db, note, Exists(resolve_collection(NOTE, "backlinks")), NOTE) is False


def test_not_exists_backlinks_for_orphan_note(backlinks_db_handles):
    db, handles = backlinks_db_handles
    note = db.get_note_from_handle(handles["orphan_note"])
    where = Not(Exists(resolve_collection(NOTE, "backlinks")))
    assert evaluate_where(db, note, where, NOTE) is True


def test_exists_backlinks_class_filter(backlinks_db_handles):
    db, handles = backlinks_db_handles
    note = db.get_note_from_handle(handles["referenced_note"])
    backlinks = resolve_collection(NOTE, "backlinks")
    assert evaluate_where(db, note, Exists(backlinks, BacklinkClassFilter("eq", "Person")), NOTE) is True
    assert (
        evaluate_where(db, note, Exists(backlinks, BacklinkClassFilter("eq", "Repository")), NOTE)
        is False
    )


def test_exists_backlinks_class_filter_ne(backlinks_db_handles):
    # referenced_note is pointed to by both a Person and a Family -- "not
    # Person" still matches via the Family backlink.
    db, handles = backlinks_db_handles
    note = db.get_note_from_handle(handles["referenced_note"])
    backlinks = resolve_collection(NOTE, "backlinks")
    assert evaluate_where(db, note, Exists(backlinks, BacklinkClassFilter("ne", "Person")), NOTE) is True


def test_exists_backlinks_class_filter_in(backlinks_db_handles):
    db, handles = backlinks_db_handles
    note = db.get_note_from_handle(handles["referenced_note"])
    backlinks = resolve_collection(NOTE, "backlinks")
    condition = BacklinkClassFilter("in", ["Person", "Family"])
    assert evaluate_where(db, note, Exists(backlinks, condition), NOTE) is True
    condition = BacklinkClassFilter("in", ["Repository", "Source"])
    assert evaluate_where(db, note, Exists(backlinks, condition), NOTE) is False


def test_count_backlinks(backlinks_db_handles):
    db, handles = backlinks_db_handles
    note = db.get_note_from_handle(handles["referenced_note"])
    backlinks = resolve_collection(NOTE, "backlinks")
    assert evaluate_where(db, note, Gt(CollectionCount(backlinks), 1), NOTE) is True
    assert evaluate_where(db, note, Gt(CollectionCount(backlinks), 2), NOTE) is False


def test_proxy_excludes_backlink_from_a_private_referrer(backlinks_db_handles, backlinks_proxy):
    """A note referenced *only* by a private Person must show no backlinks
    through the proxy, even though the raw db reports one -- PrivateProxyDb's
    own `find_backlink_handles` override (gramps/gen/proxy/private.py)
    already excludes a referrer that is itself private, and this module's
    Backlinks handling calls that method directly (no separate privacy
    guard of its own needed -- see this file's own module docstring).
    """
    db, handles = backlinks_db_handles
    raw_note = db.get_note_from_handle(handles["privately_referenced_note"])
    assert evaluate_where(db, raw_note, Exists(resolve_collection(NOTE, "backlinks")), NOTE) is True

    proxied_note = backlinks_proxy.get_note_from_handle(handles["privately_referenced_note"])
    where = Not(Exists(resolve_collection(NOTE, "backlinks")))
    assert evaluate_where(backlinks_proxy, proxied_note, where, NOTE) is True


# --- Self-linked collections (Person.child_refs, the "adopted" gap) -----------


@pytest.fixture(scope="module")
def child_refs_db_handles():
    """A person adopted into one of two parent families, a birth-only
    control, and a data-integrity edge case (a family named in someone's
    own `parent_family_list` with no matching `ChildRef` at all) -- the
    exact shape `gramps.gen.filters.rules.person.HaveAltFamilies` (the real
    rule behind gramps-connect's "adopted" filter) walks by hand.
    """
    dbman = CLIDbManager(DbState())
    dirpath, db_name = dbman.create_new_db_cli("_test_evaluator_child_refs", dbid="sqlite")
    db = make_database("sqlite")
    db.load(dirpath)

    handles = {}
    with DbTxn("setup", db) as trans:
        bio_father = Person()
        handles["bio_father"] = db.add_person(bio_father, trans)
        adoptive_father = Person()
        handles["adoptive_father"] = db.add_person(adoptive_father, trans)

        adopted_child = Person()
        handles["adopted_child"] = db.add_person(adopted_child, trans)

        bio_family = Family()
        bio_family.set_father_handle(handles["bio_father"])
        birth_ref = ChildRef()
        birth_ref.set_reference_handle(handles["adopted_child"])
        birth_ref.set_father_relation(ChildRefType.BIRTH)
        bio_family.add_child_ref(birth_ref)
        handles["bio_family"] = db.add_family(bio_family, trans)

        adoptive_family = Family()
        adoptive_family.set_father_handle(handles["adoptive_father"])
        adopted_ref = ChildRef()
        adopted_ref.set_reference_handle(handles["adopted_child"])
        adopted_ref.set_father_relation(ChildRefType.ADOPTED)
        adoptive_family.add_child_ref(adopted_ref)
        handles["adoptive_family"] = db.add_family(adoptive_family, trans)

        adopted_child.add_parent_family_handle(handles["bio_family"])
        adopted_child.add_parent_family_handle(handles["adoptive_family"])
        db.commit_person(adopted_child, trans)

        control = Person()
        handles["control"] = db.add_person(control, trans)
        control_family = Family()
        control_family.set_father_handle(handles["bio_father"])
        control_ref = ChildRef()
        control_ref.set_reference_handle(handles["control"])
        control_ref.set_father_relation(ChildRefType.BIRTH)
        control_family.add_child_ref(control_ref)
        handles["control_family"] = db.add_family(control_family, trans)
        control.add_parent_family_handle(handles["control_family"])
        db.commit_person(control, trans)

        # Data integrity: named in parent_family_list, but that family has
        # no ChildRef for them at all (see ROADMAP.md's "adopted" write-up
        # -- the real HaveAltFamilies rule's own ref[0] would crash here).
        orphan_family = Family()
        handles["orphan_family"] = db.add_family(orphan_family, trans)
        orphan_link = Person()
        handles["orphan_link"] = db.add_person(orphan_link, trans)
        orphan_link.add_parent_family_handle(handles["orphan_family"])
        db.commit_person(orphan_link, trans)

        # Privacy: adopted, but the ChildRef entry itself is marked private.
        private_father = Person()
        handles["private_father"] = db.add_person(private_father, trans)
        private_adopted = Person()
        handles["private_adopted"] = db.add_person(private_adopted, trans)
        private_family = Family()
        private_family.set_father_handle(handles["private_father"])
        private_ref = ChildRef()
        private_ref.set_reference_handle(handles["private_adopted"])
        private_ref.set_father_relation(ChildRefType.ADOPTED)
        private_ref.set_privacy(True)
        private_family.add_child_ref(private_ref)
        handles["private_family"] = db.add_family(private_family, trans)
        private_adopted.add_parent_family_handle(handles["private_family"])
        db.commit_person(private_adopted, trans)

    yield db, handles

    db.close()
    dbman.remove_database(db_name)


@pytest.fixture(scope="module")
def child_refs_proxy(child_refs_db_handles):
    db, _handles = child_refs_db_handles
    return PrivateProxyDb(db)


def _adopted_condition():
    frel = JsonPath(("frel", "value"), base_column="link.value")
    mrel = JsonPath(("mrel", "value"), base_column="link.value")
    return Or(Eq(frel, ChildRefType.ADOPTED), Eq(mrel, ChildRefType.ADOPTED))


def test_evaluate_where_child_refs_matches_adopted_child(child_refs_db_handles):
    db, handles = child_refs_db_handles
    collection = resolve_collection(PERSON, "child_refs")
    where = Exists(collection, _adopted_condition())

    adopted = db.get_person_from_handle(handles["adopted_child"])
    assert evaluate_where(db, adopted, where, PERSON) is True

    control = db.get_person_from_handle(handles["control"])
    assert evaluate_where(db, control, where, PERSON) is False


def test_evaluate_where_child_refs_no_matching_link_is_false_not_error(child_refs_db_handles):
    # The real HaveAltFamilies rule's own ref[0] would raise IndexError on
    # this exact data shape -- GOQL's version must not.
    db, handles = child_refs_db_handles
    collection = resolve_collection(PERSON, "child_refs")
    where = Exists(collection, _adopted_condition())
    orphan = db.get_person_from_handle(handles["orphan_link"])
    assert evaluate_where(db, orphan, where, PERSON) is False


def test_evaluate_where_child_refs_count(child_refs_db_handles):
    db, handles = child_refs_db_handles
    collection = resolve_collection(PERSON, "child_refs")
    count = CollectionCount(collection, _adopted_condition())
    adopted = db.get_person_from_handle(handles["adopted_child"])
    assert evaluate_where(db, adopted, Eq(count, 1), PERSON) is True
    control = db.get_person_from_handle(handles["control"])
    assert evaluate_where(db, control, Eq(count, 0), PERSON) is True


def test_proxy_excludes_private_child_ref_entry(child_refs_db_handles, child_refs_proxy):
    db, handles = child_refs_db_handles
    collection = resolve_collection(PERSON, "child_refs")
    where = Exists(collection, _adopted_condition())

    raw_person = db.get_person_from_handle(handles["private_adopted"])
    assert evaluate_where(db, raw_person, where, PERSON) is True

    proxied_person = child_refs_proxy.get_person_from_handle(handles["private_adopted"])
    assert evaluate_where(child_refs_proxy, proxied_person, where, PERSON) is False


def test_sql_and_evaluator_agree_on_child_refs(child_refs_db_handles):
    from gramps_object_query_language.query import Dialect, Query, compile_query

    db, handles = child_refs_db_handles
    collection = resolve_collection(PERSON, "child_refs")
    wheres = [Exists(collection, _adopted_condition()), Not(Exists(collection, _adopted_condition()))]
    people = {
        key: db.get_person_from_handle(handles[key])
        for key in ("adopted_child", "control", "orphan_link")
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


# --- any() nested inside any() ---------------------------------------------


@pytest.fixture(scope="module")
def nested_any_db():
    """A person per way of being sourced -- through an event's own
    citation, through the event reference's citation, or not at all --
    each with exactly one event, so a nested any() that looks at the wrong
    row (the person's instead of the event's or the reference's) gives a
    different answer from one that looks at the right one. Two of the event
    references also carry an attribute -- a cited "witness" one and an
    uncited "other" one -- for any() over a list inside a list."""
    from gramps.gen.lib import EventRef

    dbman = CLIDbManager(DbState())
    dirpath, db_name = dbman.create_new_db_cli("_test_evaluator_nested_any", dbid="sqlite")
    db = make_database("sqlite")
    db.load(dirpath)
    handles = {}
    with DbTxn("setup", db) as trans:
        handles["source"] = db.add_source(Source(), trans)
        citation = Citation()
        citation.set_reference_handle(handles["source"])
        handles["citation"] = db.add_citation(citation, trans)
        place = Place()
        place.set_title("Chicago")
        handles["place"] = db.add_place(place, trans)

        for key, event_cited, ref_cited in (
            ("event_cited", True, False),
            ("ref_cited", False, True),
            ("uncited", False, False),
        ):
            event = Event()
            event.set_type(EventType.BIRTH)
            if event_cited:
                event.add_citation(handles["citation"])
                event.set_place_handle(handles["place"])
            event_handle = db.add_event(event, trans)
            ref = EventRef()
            ref.set_reference_handle(event_handle)
            if ref_cited:
                ref.add_citation(handles["citation"])
            if key != "event_cited":
                attribute = Attribute()
                attribute.set_value("witness" if ref_cited else "other")
                if ref_cited:
                    attribute.add_citation(handles["citation"])
                ref.add_attribute(attribute)
            person = Person()
            person.set_primary_name(_name(key, "Nested"))
            person.add_event_ref(ref)
            handles[key] = db.add_person(person, trans)
    yield db, handles
    db.close()
    dbman.remove_database(db_name)


@pytest.mark.parametrize(
    "where_expr, expected",
    [
        # Collection inside a Collection: the inner unnest must read the
        # joined event (event__target), not a bare "event" that isn't in
        # scope -- used to fail with "no such column: event.json_data".
        ("any(events, any(citations))", {"event_cited"}),
        ("not any(events, any(citations))", {"ref_cited", "uncited"}),
        ("any(events, len(citations) > 0)", {"event_cited"}),
        # A relationship hop inside a Collection's condition, same cause.
        ("any(events, place.title == 'Chicago')", {"event_cited"}),
        # any(path) with no condition inside an any() over a JSON array:
        # must measure each event reference's citation_list, not the
        # person's own -- used to run, but silently match nobody.
        ("any(event_ref_list, any(citation_list))", {"ref_cited"}),
        ("any(event_ref_list, len(citation_list) > 0)", {"ref_cited"}),
        (
            "not any(citations) and not any(events, any(citations))"
            " and not any(event_ref_list, any(citation_list))",
            {"uncited"},
        ),
        # A list inside a list: each level unnests under its own alias
        # (je, je1, ...) and reads the level above's element -- used to
        # render json_each(.json_data, ...), a syntax error.
        ("any(event_ref_list, any(attribute_list, value == 'witness'))", {"ref_cited"}),
        ("any(event_ref_list, len(attribute_list, value == 'witness') > 0)", {"ref_cited"}),
        ("any(event_ref_list, any(attribute_list))", {"ref_cited", "uncited"}),
        ("any(event_ref_list, any(attribute_list, any(citation_list)))", {"ref_cited"}),
        ("any(event_ref_list, any(attribute_list, value == 'x' or any(citation_list)))", {"ref_cited"}),
    ],
)
def test_sql_and_evaluator_agree_on_nested_any(nested_any_db, pg_conn, where_expr, expected):
    """Compiles each where_expr to SQL and runs it on the fixture's real
    SQLite backend (and on PostgreSQL, if configured -- see `pg_conn`),
    checks the result against what's expected, and checks evaluate_where
    agrees person by person -- test_query_lang.py only checks how a nested
    any() *parses*, which is how both bugs above got past it."""
    db, handles = nested_any_db
    _assert_sql_and_evaluator_agree(
        db, handles, PERSON, ("event_cited", "ref_cited", "uncited"), where_expr, expected, pg_conn
    )


# --- a collection nested in a collection over the same table ----------------


@pytest.fixture(scope="module")
def self_nested_db():
    """Two same-table chains, each one link longer than any query below
    needs to see through: places B -> C -> D -> E (each enclosed by the
    next) plus an unenclosed F, and people A -> B -> {C, D} by association.
    A nested collection whose alias shadows its parent's row asks about the
    wrong row (a place enclosing itself, a person associated with
    themselves) and matches nobody."""
    from gramps.gen.lib import PlaceRef

    dbman = CLIDbManager(DbState())
    dirpath, db_name = dbman.create_new_db_cli("_test_evaluator_self_nested", dbid="sqlite")
    db = make_database("sqlite")
    db.load(dirpath)
    handles = {}
    with DbTxn("setup", db) as trans:
        enclosing = None
        for key in ("E", "D", "C", "B", "F"):
            place = Place()
            place.set_gramps_id(key)
            place.set_name(PlaceName(value=f"Place {key}"))
            if enclosing is not None and key != "F":
                ref = PlaceRef()
                ref.set_reference_handle(enclosing)
                place.add_placeref(ref)
            handles[f"place_{key}"] = enclosing = db.add_place(place, trans)

        for key, associates in (("D", ()), ("C", ()), ("B", ("C", "D")), ("A", ("B",))):
            person = Person()
            person.set_gramps_id(key)
            person.set_primary_name(_name(key, "Self"))
            for associate in associates:
                ref = PersonRef()
                ref.set_reference_handle(handles[f"person_{associate}"])
                person.add_person_ref(ref)
            handles[f"person_{key}"] = db.add_person(person, trans)
    yield db, handles
    db.close()
    dbman.remove_database(db_name)


@pytest.mark.parametrize(
    "where_expr, expected",
    [
        ("any(enclosing_places)", {"B", "C", "D"}),
        ("any(enclosing_places, any(enclosing_places))", {"B", "C"}),
        ("any(enclosing_places, not any(enclosing_places))", {"D"}),
        ("any(enclosing_places, any(enclosing_places, any(enclosing_places)))", {"B"}),
        # CollectionCount (len() of a collection) inside the same-table
        # collection's condition, same cause.
        ("any(enclosing_places, len(enclosing_places) > 0)", {"B", "C"}),
        # Flat and JSON fields of the inner row, qualified by its own alias.
        ("any(enclosing_places, any(enclosing_places, gramps_id == 'E'))", {"C"}),
        ("any(enclosing_places, any(enclosing_places, name.value == 'Place D'))", {"B"}),
        ("any(enclosing_places, gramps_id == 'C' and any(enclosing_places))", {"B"}),
    ],
)
def test_sql_and_evaluator_agree_on_nested_same_table_places(
    self_nested_db, pg_conn, where_expr, expected
):
    db, handles = self_nested_db
    _assert_sql_and_evaluator_agree(
        db,
        handles,
        PLACE,
        tuple(f"place_{key}" for key in "BCDEF"),
        where_expr,
        {f"place_{key}" for key in expected},
        pg_conn,
    )


@pytest.mark.parametrize(
    "where_expr, expected",
    [
        ("any(associations, any(associations))", {"A"}),
        ("any(associations, len(associations) > 1)", {"A"}),
        ("any(associations, len(associations) == 0)", {"B"}),
        ("any(associations, any(associations, gramps_id == 'D'))", {"A"}),
    ],
)
def test_sql_and_evaluator_agree_on_nested_same_table_people(
    self_nested_db, pg_conn, where_expr, expected
):
    db, handles = self_nested_db
    _assert_sql_and_evaluator_agree(
        db,
        handles,
        PERSON,
        tuple(f"person_{key}" for key in "ABCD"),
        where_expr,
        {f"person_{key}" for key in expected},
        pg_conn,
    )


# --- a bool flat column reached across a relationship -------------------------


@pytest.fixture(scope="module")
def related_private_db():
    """People whose birth event, or its place, is private or not. A
    related row's `private` is that table's own INTEGER column, so a bool
    literal compared to it must be coerced -- PostgreSQL rejected
    `birth.private == False` with `integer = boolean`."""
    dbman = CLIDbManager(DbState())
    dirpath, db_name = dbman.create_new_db_cli("_test_evaluator_related_private", dbid="sqlite")
    db = make_database("sqlite")
    db.load(dirpath)
    handles = {}
    with DbTxn("setup", db) as trans:
        for key, private in (("public", False), ("private", True)):
            place = Place()
            place.set_name(PlaceName(value=f"{key} place"))
            place.set_privacy(private)
            handles[f"place_{key}"] = db.add_place(place, trans)
        for key, event_private, place_key in (
            ("public_birth", False, "public"),
            ("private_birth", True, "public"),
            ("private_place", False, "private"),
        ):
            event = Event()
            event.set_type(EventType.BIRTH)
            event.set_privacy(event_private)
            event.set_place_handle(handles[f"place_{place_key}"])
            event.set_date_object(Date(1950, 1, 1))
            event_handle = db.add_event(event, trans)
            person = Person()
            person.set_primary_name(_name(key, "Related"))
            person.set_birth_ref(_event_ref(event_handle))
            handles[key] = db.add_person(person, trans)
        person = Person()
        person.set_primary_name(_name("No", "Birth"))
        handles["no_birth"] = db.add_person(person, trans)
        person = Person()
        person.set_primary_name(_name("123", "Numeric"))
        person.set_privacy(True)
        handles["numeric_name"] = db.add_person(person, trans)
    yield db, handles
    db.close()
    dbman.remove_database(db_name)


@pytest.mark.parametrize(
    "where_expr, expected",
    [
        ("birth.private == False", {"public_birth", "private_place"}),
        ("birth.private == True", {"private_birth"}),
        ("birth.private != True", {"public_birth", "private_place", "no_birth"}),
        ("birth.private in [True]", {"private_birth"}),
        ("birth.place.private == False", {"public_birth", "private_birth"}),
        ("birth.place.private == True", {"private_place"}),
    ],
)
def test_sql_and_evaluator_agree_on_related_bool_column(
    related_private_db, pg_conn, where_expr, expected
):
    db, handles = related_private_db
    _assert_sql_and_evaluator_agree(
        db,
        handles,
        PERSON,
        ("public_birth", "private_birth", "private_place", "no_birth"),
        where_expr,
        expected,
        pg_conn,
    )


@pytest.mark.parametrize("column", ["private", "birth.private"])
def test_keyset_bool_cursor_on_flat_bool_column(related_private_db, pg_conn, column):
    """A `False` cursor value seeks past every non-private row on both
    backends -- PostgreSQL used to reject it (`integer > boolean`)."""
    from gramps_object_query_language.query import Dialect, OrderBy, Query, compile_query

    db, handles = related_private_db
    # The largest handle as the tiebreak: hex strings sort alike under
    # every collation, so both backends see the same page boundary.
    last = max(handles.values())
    query = Query(select=["handle"], order_by=[OrderBy(column)], after=(False, last), limit=None)
    sql, params = compile_query(PERSON, query, dialect=Dialect.SQLITE)
    db.dbapi.execute(sql, params)
    sqlite_matches = {row[0] for row in db.dbapi.fetchall()}
    expected = {"private_birth"} if column == "birth.private" else {"numeric_name"}
    assert {key for key in handles if handles[key] in sqlite_matches} == expected
    if pg_conn is not None:
        sql, params = compile_query(PERSON, query, dialect=Dialect.POSTGRESQL)
        assert _run_on_postgresql(pg_conn, db, sql, params) == sqlite_matches


_TYPED_SELECT = [
    "handle",
    "private",
    "gender",
    "birth.private",
    "birth.place.private",
    "primary_name.private",
    "primary_name.first_name",
    "birth.date.sortval",
    "birth.date",
    "primary_name.surname_list",
]


def test_select_values_have_schema_types_on_every_path(related_private_db, pg_conn):
    """The same `select` gives equal values of equal types on SQLite, on
    PostgreSQL and on the evaluator path, once SQL rows go through
    `select_normalizer` -- a boolean is True/False everywhere, a number a
    number, an object or list parsed, and a name of "123" a string."""
    from gramps_object_query_language.proxied_query import run_query
    from gramps_object_query_language.query import Dialect, Query, compile_query, select_normalizer
    from gramps_object_query_language.query_lang import compile_expr_for_spec

    db, handles = related_private_db
    where = compile_expr_for_spec(PERSON, "gender >= 0")
    query = Query(select=_TYPED_SELECT, where=where, limit=None)
    normalize = select_normalizer(PERSON, _TYPED_SELECT)

    sql, params = compile_query(PERSON, query, dialect=Dialect.SQLITE)
    db.dbapi.execute(sql, params)
    results = {"sqlite": [normalize(row) for row in db.dbapi.fetchall()]}
    if pg_conn is not None:
        sql, params = compile_query(PERSON, query, dialect=Dialect.POSTGRESQL)
        results["postgresql"] = [normalize(row) for row in _rows_on_postgresql(pg_conn, db, sql, params)]
    results["evaluator"] = run_query(db, PERSON, where, select=_TYPED_SELECT)

    def typed(rows):
        return sorted((tuple((type(v).__name__, v) for v in row) for row in rows), key=repr)

    expected = typed(results.pop("evaluator"))
    assert len(expected) == len(handles) - 2  # every person, not the places
    for name, rows in results.items():
        assert typed(rows) == expected, name
    by_handle = {row[0]: row for row in results["sqlite"]}
    numeric = by_handle[handles["numeric_name"]]
    assert numeric[1] is True and numeric[6] == "123"
    assert by_handle[handles["private_birth"]][3] is True
    assert by_handle[handles["public_birth"]][7] > 0


# --- SQL/evaluator parity helpers --------------------------------------------


@pytest.fixture(scope="module")
def pg_conn():
    """A psycopg2 connection for also running the parity tests above on
    PostgreSQL, or `None` (PostgreSQL skipped, SQLite still runs) unless
    `GOQL_TEST_POSTGRES_DSN` is set (e.g. `host=localhost user=gramps
    password=gramps dbname=gramps`). Read-only against that database: each
    query runs against TEMP copies of the fixture's tables, which shadow
    the real ones, inside a transaction that is rolled back."""
    dsn = os.environ.get("GOQL_TEST_POSTGRES_DSN")
    if not dsn:
        yield None
        return
    psycopg2 = pytest.importorskip("psycopg2")
    conn = psycopg2.connect(dsn)
    yield conn
    conn.close()


_PRIMARY_TABLES = (
    "person", "family", "event", "place", "citation",
    "source", "repository", "media", "note", "tag",
    "reference",  # backlinks, event_refs
)


def _run_on_postgresql(conn, db, sql: str, params: list) -> set:
    """The first column of every row `_rows_on_postgresql` returns."""
    return {row[0] for row in _rows_on_postgresql(conn, db, sql, params)}


def _rows_on_postgresql(conn, db, sql: str, params: list) -> list:
    """Runs PostgreSQL-dialect `sql` against TEMP copies of `db`'s tables,
    then rolls back. INTEGER columns (`private`, `gender`, ...) stay
    INTEGER, as on a real PostgreSQL backend, so a literal of the wrong
    type fails here as it would there; every other column is copied as
    TEXT."""
    try:
        with conn.cursor() as cur:
            for table in _PRIMARY_TABLES:
                db.dbapi.execute(f"PRAGMA table_info({table})")
                info = db.dbapi.fetchall()
                quoted = [f'"{row[1]}"' for row in info]  # e.g. media's "desc"
                types = ["INTEGER" if row[2].upper() == "INTEGER" else "TEXT" for row in info]
                cur.execute(
                    f"CREATE TEMP TABLE {table} "
                    f"({', '.join(f'{c} {t}' for c, t in zip(quoted, types))})"
                )
                db.dbapi.execute(f"SELECT {', '.join(quoted)} FROM {table}")
                rows = db.dbapi.fetchall()
                if rows:
                    placeholders = ", ".join(["%s"] * len(info))
                    cur.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
            cur.execute(sql.replace("?", "%s"), params)
            return cur.fetchall()
    finally:
        conn.rollback()


def _assert_sql_and_evaluator_agree(db, handles, spec, keys, where_expr, expected, pg_conn):
    """Compiles `where_expr` against `spec`, runs it on `db`'s SQLite backend
    (and, given `pg_conn`, on PostgreSQL too), and checks that the matching
    `keys` (into `handles`) are exactly `expected` on each, and that
    `evaluate_where` agrees object by object."""
    from gramps_object_query_language.query import Dialect, Query, compile_query
    from gramps_object_query_language.query_lang import parse_expr_for_spec, where_list_to_ast

    where = where_list_to_ast(parse_expr_for_spec(spec, where_expr), spec)
    query = Query(select=["handle"], where=where)
    sql, params = compile_query(spec, query, dialect=Dialect.SQLITE)
    db.dbapi.execute(sql, params)
    sql_matches = {row[0] for row in db.dbapi.fetchall()}
    assert {key for key in keys if handles[key] in sql_matches} == set(expected), "SQLite"
    if pg_conn is not None:
        sql, params = compile_query(spec, query, dialect=Dialect.POSTGRESQL)
        pg_matches = _run_on_postgresql(pg_conn, db, sql, params)
        assert {key for key in keys if handles[key] in pg_matches} == set(expected), "PostgreSQL"
    get_object = getattr(db, f"get_{spec.table}_from_handle")
    for key in keys:
        actual = evaluate_where(db, get_object(handles[key]), where, spec)
        assert actual == (key in expected), f"{where_expr!r} on {key!r}: eval={actual}"


# --- several main-row fields: PostgreSQL parses the row's JSON once ----------


@pytest.fixture(scope="module")
def parse_once_db():
    """Events and people for queries that read several fields of the main
    row, which PostgreSQL answers through one parsed copy of the row
    (`goql_row`) instead of a `json_data::jsonb` cast per field. Mixes in
    flat columns, relationship hops, lists and missing values, the pieces
    that sit next to the parsed copy in the same query."""
    dbman = CLIDbManager(DbState())
    dirpath, db_name = dbman.create_new_db_cli("_test_evaluator_parse_once", dbid="sqlite")
    db = make_database("sqlite")
    db.load(dirpath)
    handles = {}
    with DbTxn("setup", db) as trans:
        place = Place()
        place.set_title("Chicago")
        place_handle = db.add_place(place, trans)

        def add_event(key, event_type, date, description, **extra):
            event = Event()
            event.set_type(event_type)
            if date is not None:
                event.set_date_object(date)
            event.set_description(description)
            event.set_gramps_id(key)
            if extra.get("place"):
                event.set_place_handle(place_handle)
            if extra.get("attribute"):
                attribute = Attribute()
                attribute.set_value(extra["attribute"])
                event.add_attribute(attribute)
            handles[key] = db.add_event(event, trans)

        about = Date(1950, 11, 2)
        about.set_modifier(Date.MOD_ABOUT)
        julian = Date()
        julian.set(Date.QUAL_NONE, Date.MOD_NONE, Date.CAL_JULIAN, (1, 1, 1700, False))
        yearless = Date()
        yearless.set_yr_mon_day(0, 11, 5)
        add_event("e_nov2", EventType.BIRTH, Date(1950, 11, 2), "Birth of Ann", place=True)
        add_event("e_nov2_about", EventType.BIRTH, about, "Birth of Bob")
        add_event("e_dec25", EventType.MARRIAGE, Date(1975, 12, 25), "Wedding", attribute="x")
        add_event("e_julian", EventType.DEATH, julian, "Old death")
        add_event("e_yearless", EventType.BIRTH, yearless, "Birth, year unknown")
        add_event("e_nodate", EventType.BIRTH, None, "Undated birth")

        for key, given, surname, gender, birth, other, alternate in (
            ("p_ann", "Ann", "Smith", Person.FEMALE, "e_nov2", None, False),
            ("p_bob", "Bob", "Smith", Person.MALE, "e_nov2_about", None, True),
            ("p_cat", "Cat", "Jones", Person.FEMALE, None, "e_dec25", False),
            ("p_dan", "Dan", "Brown", Person.UNKNOWN, None, None, False),
        ):
            person = Person()
            person.set_primary_name(_name(given, surname))
            person.set_gender(gender)
            if alternate:
                person.add_alternate_name(_name(given, "Alt"))
            if birth:
                ref = _event_ref(handles[birth])
                person.add_event_ref(ref)
                person.set_birth_ref(ref)
            if other:
                person.add_event_ref(_event_ref(handles[other]))
            handles[key] = db.add_person(person, trans)
    yield db, handles
    db.close()
    dbman.remove_database(db_name)


_PARSE_ONCE_EVENTS = ("e_nov2", "e_nov2_about", "e_dec25", "e_julian", "e_yearless", "e_nodate")
_PARSE_ONCE_PEOPLE = ("p_ann", "p_bob", "p_cat", "p_dan")
_EXACT_DAY_AND_MONTH = (
    "type.value in [EventType.BIRTH, EventType.DEATH, EventType.MARRIAGE]"
    " and date.modifier == Date.MOD_NONE and date.quality == Date.QUAL_NONE"
    " and date.calendar == Date.CAL_GREGORIAN"
    " and date.dateval[0] > 0 and date.dateval[1] > 0"
)


def _assert_parses_main_row_once(spec, where_expr):
    """Guards the tests below: each where_expr must actually compile to the
    parsed-once form on PostgreSQL, or it isn't testing it."""
    from gramps_object_query_language.query import Dialect, Query, compile_query
    from gramps_object_query_language.query_lang import parse_expr_for_spec, where_list_to_ast

    where = where_list_to_ast(parse_expr_for_spec(spec, where_expr), spec)
    sql, _ = compile_query(spec, Query(select=["handle"], where=where), dialect=Dialect.POSTGRESQL)
    assert "goql_row" in sql, where_expr


@pytest.mark.parametrize(
    "where_expr, expected",
    [
        (
            "type.value == EventType.BIRTH and date.modifier == Date.MOD_NONE"
            " and date.dateval[1] == 11",
            {"e_nov2", "e_yearless"},
        ),
        (_EXACT_DAY_AND_MONTH, {"e_nov2", "e_dec25", "e_yearless"}),
        (
            "(date.dateval[1] == 12 or date.dateval[0] == 2)"
            " and not (date.modifier == Date.MOD_ABOUT)",
            {"e_nov2", "e_dec25"},
        ),
        (
            "date.calendar != Date.CAL_GREGORIAN or date.dateval[2] == 0",
            {"e_julian", "e_yearless", "e_nodate"},
        ),
        (
            "date.calendar == Date.CAL_JULIAN and date.dateval[1] == 1 and date.dateval[0] == 1",
            {"e_julian"},
        ),
        # beside a relationship hop, whose own json_data is its own row's
        (
            "place.title == 'Chicago' and date.dateval[1] == 11"
            " and type.value == EventType.BIRTH",
            {"e_nov2"},
        ),
        # beside a flat column (not `description`: SharedPostgreSQL's real
        # column is desc_ription, which _rows_on_postgresql's copies lack)
        (
            "like(gramps_id, 'e_nov2%') and date.dateval[1] == 11"
            " and date.modifier == Date.MOD_ABOUT",
            {"e_nov2_about"},
        ),
        # beside len() and any() of the row's own lists
        (
            "len(citation_list) == 0 and date.dateval[2] > 1900 and date.dateval[1] > 0",
            {"e_nov2", "e_nov2_about", "e_dec25"},
        ),
        (
            "any(attribute_list, value == 'x')"
            " or (date.calendar == Date.CAL_JULIAN and date.dateval[2] == 1700)",
            {"e_dec25", "e_julian"},
        ),
    ],
)
def test_sql_and_evaluator_agree_parsing_events_once(parse_once_db, pg_conn, where_expr, expected):
    db, handles = parse_once_db
    _assert_parses_main_row_once(EVENT, where_expr)
    _assert_sql_and_evaluator_agree(
        db, handles, EVENT, _PARSE_ONCE_EVENTS, where_expr, expected, pg_conn
    )


@pytest.mark.parametrize(
    "where_expr, expected",
    [
        ("primary_name.first_name == 'Ann' and len(alternate_names) == 0", {"p_ann"}),
        (
            "primary_name.surname_list[0].surname == 'Smith' and len(alternate_names) > 0",
            {"p_bob"},
        ),
        (
            "gender == Person.FEMALE and primary_name.surname_list[0].surname == 'Jones'"
            " and primary_name.first_name != ''",
            {"p_cat"},
        ),
        # beside a hop through birth_ref_index into the event table
        (
            "birth.date.dateval[1] == 11 and primary_name.first_name in ['Ann', 'Bob']"
            " and primary_name.surname_list[0].surname == 'Smith'",
            {"p_ann", "p_bob"},
        ),
        (
            "any(event_ref_list, role.value == 1) and primary_name.first_name != 'Ann'"
            " and len(alternate_names) == 0",
            {"p_cat"},
        ),
        # a missing birth (the hop finds no row) next to main-row fields
        (
            "(birth.date.dateval[1] == 0 or primary_name.first_name == 'Dan')"
            " and len(alternate_names) == 0",
            {"p_dan"},
        ),
    ],
)
def test_sql_and_evaluator_agree_parsing_people_once(parse_once_db, pg_conn, where_expr, expected):
    db, handles = parse_once_db
    _assert_parses_main_row_once(PERSON, where_expr)
    _assert_sql_and_evaluator_agree(
        db, handles, PERSON, _PARSE_ONCE_PEOPLE, where_expr, expected, pg_conn
    )


@pytest.mark.parametrize("limit", [None, 2])
def test_order_by_and_count_agree_parsing_once(parse_once_db, pg_conn, limit):
    """ORDER BY and LIMIT over the parsed copy, and a count query (which uses
    it only from four field references up), give the same answer on SQLite
    and PostgreSQL as the expected order."""
    from gramps_object_query_language.query import (
        Dialect, OrderBy, Query, compile_count_query, compile_query,
    )
    from gramps_object_query_language.query_lang import parse_expr_for_spec, where_list_to_ast

    db, handles = parse_once_db
    where = where_list_to_ast(parse_expr_for_spec(EVENT, _EXACT_DAY_AND_MONTH), EVENT)
    order_by = [OrderBy("date.dateval[1]"), OrderBy("date.dateval[0]"), OrderBy("date.dateval[2]", "desc")]
    query = Query(select=["handle"], where=where, order_by=order_by, limit=limit)
    expected = [handles[key] for key in ("e_nov2", "e_yearless", "e_dec25")][:limit]

    sql, params = compile_query(EVENT, query, dialect=Dialect.SQLITE)
    db.dbapi.execute(sql, params)
    assert [row[0] for row in db.dbapi.fetchall()] == expected, "SQLite"
    count_sql, count_params = compile_count_query(EVENT, Query(where=where), dialect=Dialect.SQLITE)
    db.dbapi.execute(count_sql, count_params)
    assert db.dbapi.fetchall()[0][0] == 3, "SQLite count"

    sql, params = compile_query(EVENT, query, dialect=Dialect.POSTGRESQL)
    count_sql, count_params = compile_count_query(EVENT, Query(where=where), dialect=Dialect.POSTGRESQL)
    assert "goql_row" in sql and "goql_row" in count_sql
    if pg_conn is not None:
        assert [row[0] for row in _rows_on_postgresql(pg_conn, db, sql, params)] == expected, "PostgreSQL"
        assert _rows_on_postgresql(pg_conn, db, count_sql, count_params)[0][0] == 3, "PostgreSQL count"


# --- a selected relationship field named like a sort column -----------------
#
# A related field renders as a scalar subquery, `(SELECT gramps_id FROM event
# ...)`, which PostgreSQL names after its inner column. A bare `ORDER BY
# gramps_id` looks at output column names first, so it either failed as
# ambiguous (the record's own column selected too -- and `handle` always is,
# as the final tiebreak) or silently sorted by the related record's column.


def _person_rows(db, pg_conn, query):
    """`query`'s rows on SQLite and, given `pg_conn`, PostgreSQL."""
    from gramps_object_query_language.query import Dialect, compile_query

    sql, params = compile_query(PERSON, query, dialect=Dialect.SQLITE)
    db.dbapi.execute(sql, params)
    results = {"SQLite": [tuple(row) for row in db.dbapi.fetchall()]}
    if pg_conn is not None:
        sql, params = compile_query(PERSON, query, dialect=Dialect.POSTGRESQL)
        results["PostgreSQL"] = [tuple(row) for row in _rows_on_postgresql(pg_conn, db, sql, params)]
    return results


def test_select_related_handle(parse_once_db, pg_conn):
    """`birth.handle` (the default order's own `handle` tiebreak shares its
    name) runs, and returns the birth event's handle."""
    from gramps_object_query_language.query import Query
    from gramps_object_query_language.query_lang import parse_select

    db, handles = parse_once_db
    select = [ref for ref, _ in parse_select(PERSON, ["handle", "birth.handle"])]
    expected = {
        (handles["p_ann"], handles["e_nov2"]),
        (handles["p_bob"], handles["e_nov2_about"]),
        (handles["p_cat"], None),
        (handles["p_dan"], None),
    }
    for backend, rows in _person_rows(db, pg_conn, Query(select=select)).items():
        assert set(rows) == expected, backend


@pytest.mark.parametrize("own_column_selected", [True, False])
def test_order_by_column_shared_with_a_related_select(parse_once_db, pg_conn, own_column_selected):
    """Sorting by `gramps_id` while selecting `birth.gramps_id` sorts by the
    person's own Gramps ID, whether or not that column is selected too --
    not by the birth event's (which puts people with no birth first)."""
    from gramps_object_query_language.query import Query
    from gramps_object_query_language.query_lang import parse_order_by, parse_select

    db, handles = parse_once_db
    entries = ["handle", "gramps_id", "birth.gramps_id"] if own_column_selected else ["handle", "birth.gramps_id"]
    select = [ref for ref, _ in parse_select(PERSON, entries)]
    order_by = parse_order_by(PERSON, ["gramps_id"])
    get = db.get_person_from_handle
    by_own_id = sorted(("p_ann", "p_bob", "p_cat", "p_dan"), key=lambda key: get(handles[key]).gramps_id)
    expected = [handles[key] for key in by_own_id]
    for backend, rows in _person_rows(db, pg_conn, Query(select=select, order_by=order_by)).items():
        assert [row[0] for row in rows] == expected, backend


def test_keyset_page_with_a_related_select_named_like_the_sort(parse_once_db, pg_conn):
    """The next page (`after`) continues the same order: the seek condition
    and the ORDER BY both use the person's own `change`."""
    from gramps_object_query_language.query import Query
    from gramps_object_query_language.query_lang import parse_order_by, parse_select

    db, handles = parse_once_db
    select = [ref for ref, _ in parse_select(PERSON, ["handle", "birth.change"])]
    order_by = parse_order_by(PERSON, ["change"])
    whole = _person_rows(db, pg_conn, Query(select=["handle"], order_by=order_by))["SQLite"]
    order = [row[0] for row in whole]
    first = db.get_person_from_handle(order[0])
    after = (first.change, first.handle)
    query = Query(select=select, order_by=order_by, after=after)
    for backend, rows in _person_rows(db, pg_conn, query).items():
        assert [row[0] for row in rows] == order[1:], backend


# --- PostgreSQL pages: choose the rows, then read their select ---------------
#
# PostgreSQL computes plain `SELECT` expressions before the `ORDER BY` sort,
# so a page of JSON fields sorted without an index read every matching
# row's JSON. A page with a JSON `select` now picks its rows (handle and sort
# keys) in an inner query and reads the `select` for those rows only.


@pytest.mark.parametrize(
    "dialect_name, select, limit, deferred",
    [
        ("POSTGRESQL", ["handle", "primary_name.first_name"], 2, True),
        ("POSTGRESQL", ["handle", "birth.date"], 2, True),
        ("POSTGRESQL", ["handle", "primary_name.first_name"], None, False),
        ("POSTGRESQL", ["handle", "gramps_id", "surname"], 2, False),
        ("SQLITE", ["handle", "primary_name.first_name"], 2, False),
    ],
)
def test_which_pages_read_select_after_choosing_rows(dialect_name, select, limit, deferred):
    from gramps_object_query_language.query import Dialect, Query, compile_query
    from gramps_object_query_language.query_lang import parse_order_by, parse_select

    query = Query(
        select=[ref for ref, _ in parse_select(PERSON, select)],
        order_by=parse_order_by(PERSON, ["surname"]),
        limit=limit,
    )
    sql, _ = compile_query(PERSON, query, dialect=Dialect[dialect_name], treeid=7)
    assert ("goql_page" in sql) == deferred


def _page_through(run, spec, select, order_by, *, where=None, collation=None, treeid=None):
    """Every row of the deferred statement, two at a time, each page
    continuing from the last one's cursor as gramps-web-api does (the
    cursor values looked up by `compile_after_lookup`). `run(sql, params)`
    executes PostgreSQL-dialect SQL."""
    from gramps_object_query_language.query import (
        Dialect, Query, compile_after_lookup, compile_query,
    )

    rows, after = [], None
    for _ in range(20):  # a cursor that stops advancing fails, not hangs
        sql, params = compile_query(
            spec,
            Query(select=select, where=where, order_by=order_by, limit=2, after=after),
            collation=collation, dialect=Dialect.POSTGRESQL, treeid=treeid,
        )
        assert "goql_page" in sql
        page = run(sql, params)
        rows.extend(page)
        if len(page) < 2:
            return rows
        lookup_sql, lookup_params = compile_after_lookup(
            spec, order_by, page[-1][0], dialect=Dialect.POSTGRESQL, treeid=treeid
        )
        after = tuple(run(lookup_sql, lookup_params)[0])
    raise AssertionError(f"paging did not finish: {len(rows)} rows so far")


def _one_query(run, spec, select, order_by, *, where=None, collation=None, treeid=None):
    """The same rows as one unpaged statement, which never defers."""
    from gramps_object_query_language.query import Dialect, Query, compile_query

    sql, params = compile_query(
        spec,
        Query(select=select, where=where, order_by=order_by, limit=None),
        collation=collation, dialect=Dialect.POSTGRESQL, treeid=treeid,
    )
    assert "goql_page" not in sql
    return run(sql, params)


_DEFERRED_PEOPLE_SELECT = [
    "handle", "gramps_id", "primary_name.first_name", "primary_name.surname_list",
    "birth.date.sortval", "count(events)", "event_ref_list",
]


@pytest.mark.parametrize(
    "order",
    [
        ["surname", "given_name"],
        ["-surname", "-given_name"],
        ["birth.date.sortval"],  # NULL for two people: NULLs first
        ["-birth.date.sortval"],  # ... and last
        ["primary_name.first_name"],
        ["-gramps_id"],
    ],
)
def test_pages_read_after_choosing_rows_match_one_query(parse_once_db, pg_conn, order):
    """Paging two rows at a time through the deferred statement, cursor by
    cursor, gives exactly the rows and values of one unpaged query, on
    PostgreSQL -- with a JSON field, a parsed-once pair of fields, a hop and
    a count beside flat columns."""
    from gramps_object_query_language.query_lang import parse_order_by, parse_select

    if pg_conn is None:
        pytest.skip("needs GOQL_TEST_POSTGRES_DSN")
    db, _ = parse_once_db
    select = [ref for ref, _ in parse_select(PERSON, _DEFERRED_PEOPLE_SELECT)]
    order_by = parse_order_by(PERSON, order)

    def run(sql, params):
        return _rows_on_postgresql(pg_conn, db, sql, params)

    expected = _one_query(run, PERSON, select, order_by)
    assert len(expected) == 4
    assert _page_through(run, PERSON, select, order_by) == expected


def test_deferred_page_with_a_filter_and_a_parsed_row_inside(parse_once_db, pg_conn):
    """A filter (its parameters bind between the select's and the limit's),
    sort keys that read the row's JSON twice -- so the inner query gets its
    own parsed-row join -- and a select reading it only once, so the outer
    query doesn't: the inner query keeps its join, and the pages still match
    one unpaged query."""
    from gramps_object_query_language.query import Dialect, Query, compile_query
    from gramps_object_query_language.query_lang import (
        parse_expr_for_spec, parse_order_by, parse_select, where_list_to_ast,
    )

    db, handles = parse_once_db
    select = [ref for ref, _ in parse_select(PERSON, ["handle", "gramps_id", "primary_name.first_name"])]
    order_by = parse_order_by(PERSON, ["-primary_name.surname_list[0].surname", "primary_name.first_name"])
    where = where_list_to_ast(parse_expr_for_spec(PERSON, "primary_name.first_name != 'Cat'"), PERSON)

    sql, _ = compile_query(
        PERSON, Query(select=select, where=where, order_by=order_by, limit=2), dialect=Dialect.POSTGRESQL
    )
    inner = sql[sql.index("FROM (SELECT"): sql.index(") AS goql_page")]
    outer = sql.replace(inner, "")
    # The inner query's sort keys and filter read its parsed copy of the row.
    assert "CROSS JOIN LATERAL" in inner and inner.count("goql_row.json_data_jsonb") >= 2
    assert "CROSS JOIN LATERAL" not in outer and "goql_row" not in outer

    if pg_conn is None:
        pytest.skip("needs GOQL_TEST_POSTGRES_DSN")

    def run(sql, params):
        return _rows_on_postgresql(pg_conn, db, sql, params)

    expected = _one_query(run, PERSON, select, order_by, where=where)
    # Smiths first (surname descending), each surname by first name; no Cat.
    assert [row[0] for row in expected] == [handles[key] for key in ("p_ann", "p_bob", "p_dan")]
    assert _page_through(run, PERSON, select, order_by, where=where) == expected


def _rows_on_postgresql_in_two_trees(conn, db, sql: str, params: list) -> list:
    """`_rows_on_postgresql`, but laid out as SharedPostgreSQL shares its
    tables: each table gets a `treeid` column and holds `db`'s rows twice,
    as tree 1 and tree 2 -- the same handles, as in a copied tree. Tree 2's
    flat `gramps_id` is prefixed with "T2-", so a value read from the wrong
    tree shows."""
    try:
        with conn.cursor() as cur:
            for table in _PRIMARY_TABLES:
                db.dbapi.execute(f"PRAGMA table_info({table})")
                info = db.dbapi.fetchall()
                names = [row[1] for row in info]
                quoted = [f'"{name}"' for name in names]
                types = ["INTEGER" if row[2].upper() == "INTEGER" else "TEXT" for row in info]
                cur.execute(
                    f"CREATE TEMP TABLE {table} (treeid INTEGER NOT NULL, "
                    f"{', '.join(f'{c} {t}' for c, t in zip(quoted, types))})"
                )
                db.dbapi.execute(f"SELECT {', '.join(quoted)} FROM {table}")
                rows = db.dbapi.fetchall()
                placeholders = ", ".join(["%s"] * (len(info) + 1))
                for treeid in (1, 2):
                    for row in rows:
                        row = list(row)
                        if treeid == 2 and "gramps_id" in names:
                            row[names.index("gramps_id")] = f"T2-{row[names.index('gramps_id')]}"
                        cur.execute(f"INSERT INTO {table} VALUES ({placeholders})", [treeid, *row])
            cur.execute(sql.replace("?", "%s"), params)
            return cur.fetchall()
    finally:
        conn.rollback()


@pytest.mark.parametrize("treeid", [1, 2])
def test_deferred_page_reads_its_own_tree_when_trees_share_handles(parse_once_db, pg_conn, treeid):
    """In tables shared by several trees, two trees can hold the same
    handles (a copied tree). The page's rows are joined back to its own
    tree's records: each person once, with that tree's values."""
    from gramps_object_query_language.query import Dialect, Query, compile_query
    from gramps_object_query_language.query_lang import parse_order_by, parse_select

    db, handles = parse_once_db
    select = [ref for ref, _ in parse_select(PERSON, _DEFERRED_PEOPLE_SELECT)]
    order_by = parse_order_by(PERSON, ["surname", "given_name"])
    sql, params = compile_query(
        PERSON, Query(select=select, order_by=order_by, limit=2), dialect=Dialect.POSTGRESQL, treeid=treeid
    )
    assert "person.treeid = ?" in sql and params.count(treeid) >= 3  # inner filter, join, hops

    if pg_conn is None:
        pytest.skip("needs GOQL_TEST_POSTGRES_DSN")

    def run(sql, params):
        return _rows_on_postgresql_in_two_trees(pg_conn, db, sql, params)

    rows = _page_through(run, PERSON, select, order_by, treeid=treeid)
    assert sorted(row[0] for row in rows) == sorted(handles[key] for key in _PARSE_ONCE_PEOPLE)
    prefix = "T2-" if treeid == 2 else ""
    assert all(row[1].startswith(prefix) and (treeid == 2 or not row[1].startswith("T2-")) for row in rows)
    assert rows == _one_query(run, PERSON, select, order_by, treeid=treeid)


@pytest.fixture(scope="module")
def collation_db():
    """People whose surnames sort differently by code point ("C": capitals
    first, then lower case, then accented) and by a locale ("und-x-icu":
    alphabetical, ignoring case and accents)."""
    dbman = CLIDbManager(DbState())
    dirpath, db_name = dbman.create_new_db_cli("_test_evaluator_collation", dbid="sqlite")
    db = make_database("sqlite")
    db.load(dirpath)
    handles = {}
    with DbTxn("setup", db) as trans:
        for key, given, surname in (
            ("zeller", "Zoe", "Zeller"), ("devos", "Dirk", "de Vos"),
            ("adams", "Amy", "Adams"), ("emile", "Eve", "\u00c9mile"),
            ("baker", "Bob", "Baker"),
        ):
            person = Person()
            person.set_primary_name(_name(given, surname))
            handles[key] = db.add_person(person, trans)
    yield db, handles
    db.close()
    dbman.remove_database(db_name)


@pytest.mark.parametrize(
    "collation, order",
    [
        ("C", ["adams", "baker", "zeller", "devos", "emile"]),
        ("und-x-icu", ["adams", "baker", "devos", "emile", "zeller"]),
    ],
)
def test_deferred_page_sorts_by_the_requested_collation(collation_db, pg_conn, collation, order):
    """A locale collation (gramps-web-api's `locale` parameter) rides on the
    inner query's sort keys: both levels sort by it, and the pages come out
    in that collation's order, matching one unpaged query."""
    from gramps_object_query_language.query_lang import parse_order_by, parse_select

    if pg_conn is None:
        pytest.skip("needs GOQL_TEST_POSTGRES_DSN")
    db, handles = collation_db
    select = [ref for ref, _ in parse_select(PERSON, ["handle", "primary_name.first_name"])]
    order_by = parse_order_by(PERSON, ["surname"])

    def run(sql, params):
        return _rows_on_postgresql(pg_conn, db, sql, params)

    rows = _page_through(run, PERSON, select, order_by, collation=collation)
    assert [row[0] for row in rows] == [handles[key] for key in order]
    assert rows == _one_query(run, PERSON, select, order_by, collation=collation)


# --- <collection>[n].<field>: following one entry of a collection ------------


@pytest.fixture(scope="module")
def indexed_hop_db():
    """Two parents and a child whose parent_family_list names their family
    (then a second, step family), an orphan with no parent family, a source
    with a citation, and attributes on the father, for
    `parent_families[0].father...`, `families[0].children[0]...` and
    `citations[0].source...`."""
    from gramps.gen.lib import Citation, Source, SrcAttribute

    dbman = CLIDbManager(DbState())
    dirpath, db_name = dbman.create_new_db_cli("_test_evaluator_indexed_hop", dbid="sqlite")
    db = make_database("sqlite")
    db.load(dirpath)
    handles = {}
    with DbTxn("setup", db) as trans:
        source = Source()
        source.set_title("Parish Register")
        film = SrcAttribute()
        film.set_type("Film")
        film.set_value("FHL 123")
        source.add_attribute(film)
        handles["source"] = db.add_source(source, trans)
        citation = Citation()
        citation.set_reference_handle(handles["source"])
        handles["citation"] = db.add_citation(citation, trans)

        father = Person()
        father.set_primary_name(_name("Karl", "Anderson"))
        father.add_citation(handles["citation"])
        for attr_type, value in (
            (AttributeType.NICKNAME, "Kalle"),
            (AttributeType.OCCUPATION, "Farmer"),
            (AttributeType.OCCUPATION, "Miller"),
            # A custom type: stored by name, unlike the built-ins above.
            ("Hair color", "Brown"),
        ):
            attr = Attribute()
            attr.set_type(attr_type)
            attr.set_value(value)
            father.add_attribute(attr)
        handles["father"] = db.add_person(father, trans)
        mother = Person()
        mother.set_primary_name(_name("Lena", "Baker"))
        for url_type, path in (
            (UrlType.WEB_HOME, "https://lena.example"),
            (UrlType.EMAIL, "lena@example.org"),
            (UrlType.EMAIL, "baker@example.org"),
            ("Blog", "https://lena.example/blog"),
        ):
            url = Url()
            url.set_type(url_type)
            url.set_path(path)
            mother.add_url(url)
        handles["mother"] = db.add_person(mother, trans)
        child = Person()
        child.set_primary_name(_name("Steve", "Anderson"))
        handles["child"] = db.add_person(child, trans)
        orphan = Person()
        orphan.set_primary_name(_name("Olive", "Orr"))
        handles["orphan"] = db.add_person(orphan, trans)

        family = Family()
        family.set_father_handle(handles["father"])
        family.set_mother_handle(handles["mother"])
        child_ref = ChildRef()
        child_ref.set_reference_handle(handles["child"])
        family.add_child_ref(child_ref)
        handles["family"] = db.add_family(family, trans)

        # The child's second parent family: a stepfather and his wife.
        stepfather = Person()
        stepfather.set_primary_name(_name("Ed", "Jones"))
        handles["stepfather"] = db.add_person(stepfather, trans)
        stepmother = Person()
        stepmother.set_primary_name(_name("Ann", "Lee"))
        handles["stepmother"] = db.add_person(stepmother, trans)
        family2 = Family()
        family2.set_father_handle(handles["stepfather"])
        family2.set_mother_handle(handles["stepmother"])
        step_ref = ChildRef()
        step_ref.set_reference_handle(handles["child"])
        step_ref.set_father_relation(ChildRefType.STEPCHILD)
        family2.add_child_ref(step_ref)
        handles["family2"] = db.add_family(family2, trans)

        # Gramps keeps both sides of a family link; DbTxn doesn't do it for us.
        for key, method, family_key in (
            ("father", "add_family_handle", "family"),
            ("mother", "add_family_handle", "family"),
            ("child", "add_parent_family_handle", "family"),
            ("stepfather", "add_family_handle", "family2"),
            ("stepmother", "add_family_handle", "family2"),
            ("child", "add_parent_family_handle", "family2"),
        ):
            person = db.get_person_from_handle(handles[key])
            getattr(person, method)(handles[family_key])
            db.commit_person(person, trans)
    yield db, handles
    db.close()
    dbman.remove_database(db_name)


_PEOPLE = ("father", "mother", "child", "orphan")


@pytest.mark.parametrize(
    "entry, expected",
    [
        ("parent_families[0].father.surname", {"child": "Anderson"}),
        ("parent_families[0].mother.primary_name.first_name", {"child": "Lena"}),
        ("parent_families[1].father.surname", {"child": "Jones"}),
        ("parent_families[2].father.surname", {}),
        ("parent_families[0].gramps_id", {"child": "F0000"}),
        ("families[0].children[0].given_name", {"father": "Steve", "mother": "Steve"}),
        ("citations[0].source.title", {"father": "Parish Register"}),
        (
            "len([f for f in parent_families if f.father.surname == 'Anderson'])",
            {"child": 1, "father": 0, "mother": 0, "orphan": 0},
        ),
    ],
)
def test_indexed_collection_hop_select(indexed_hop_db, pg_conn, entry, expected):
    """A select of `<collection>[n].<field>` gives the linked record's value,
    and NULL where the entry isn't there -- on SQLite, PostgreSQL and the
    evaluator alike."""
    from gramps_object_query_language.query import Dialect, Query, compile_query
    from gramps_object_query_language.query_lang import parse_select

    db, handles = indexed_hop_db
    [(ref, key)] = parse_select(PERSON, [entry + " as v"])
    assert key == "v"
    want = {handles[person]: expected.get(person) for person in _PEOPLE}

    query = Query(select=["handle", ref])
    sql, params = compile_query(PERSON, query, dialect=Dialect.SQLITE)
    db.dbapi.execute(sql, params)
    got = {handle: value for handle, value in db.dbapi.fetchall() if handle in want}
    assert got == want, "SQLite"
    if pg_conn is not None:
        sql, params = compile_query(PERSON, query, dialect=Dialect.POSTGRESQL)
        rows = _rows_on_postgresql(pg_conn, db, sql, params)
        assert {h: v for h, v in rows if h in want} == want, "PostgreSQL"
    for handle, value in want.items():
        person = db.get_person_from_handle(handle)
        assert resolve_column_ref(db, person, ref, PERSON) == value, "evaluator"


@pytest.mark.parametrize(
    "where_expr, expected",
    [
        ("parent_families[0].father.surname == 'Anderson'", {"child"}),
        # `!=` is null-safe: no parent family at all also differs.
        ("parent_families[0].father.surname != 'Anderson'", {"father", "mother", "orphan"}),
        ("families[0].children[0].given_name == 'Steve'", {"father", "mother"}),
        ("len([f for f in parent_families if f.mother.surname == 'Baker']) == 1", {"child"}),
    ],
)
def test_indexed_collection_hop_where(indexed_hop_db, pg_conn, where_expr, expected):
    db, handles = indexed_hop_db
    _assert_sql_and_evaluator_agree(
        db, handles, PERSON, _PEOPLE, where_expr, expected, pg_conn
    )


def test_birth_children_column(indexed_hop_db, pg_conn):
    """The comprehension a Families column uses to count birth children,
    where an adopted child doesn't count."""
    from gramps.gen.lib import ChildRefType
    from gramps_object_query_language.query import Dialect, Query, compile_query
    from gramps_object_query_language.query_lang import parse_select

    db, handles = indexed_hop_db
    [(ref, _)] = parse_select(FAMILY, [
        "len([c for c in child_ref_list if c.frel.value == ChildRefType.BIRTH "
        "and c.mrel.value == ChildRefType.BIRTH]) as n"
    ])
    family = db.get_family_from_handle(handles["family"])
    adopted = ChildRef()
    adopted.set_reference_handle(handles["orphan"])
    adopted.set_father_relation(ChildRefType.ADOPTED)
    family.add_child_ref(adopted)
    with DbTxn("adopt", db) as trans:
        db.commit_family(family, trans)
    try:
        query = Query(select=["handle", ref])
        sql, params = compile_query(FAMILY, query, dialect=Dialect.SQLITE)
        db.dbapi.execute(sql, params)
        assert dict(db.dbapi.fetchall())[handles["family"]] == 1, "SQLite"
        if pg_conn is not None:
            sql, params = compile_query(FAMILY, query, dialect=Dialect.POSTGRESQL)
            assert dict(_rows_on_postgresql(pg_conn, db, sql, params))[handles["family"]] == 1
        assert resolve_column_ref(db, family, ref, FAMILY) == 1, "evaluator"
    finally:
        family.set_child_ref_list(family.get_child_ref_list()[:1])
        with DbTxn("undo adopt", db) as trans:
            db.commit_family(family, trans)


def test_indexed_collection_hop_sorts(indexed_hop_db):
    """`order_by` takes the same path; people with no parent family (NULL)
    sort first ascending, as a missing birth date does."""
    from gramps_object_query_language.query import Dialect, Query, compile_query
    from gramps_object_query_language.query_lang import parse_order_by

    db, handles = indexed_hop_db
    order_by = parse_order_by(PERSON, ["-families[0].children[0].given_name", "gramps_id"])
    sql, params = compile_query(
        PERSON, Query(select=["handle"], order_by=order_by), dialect=Dialect.SQLITE
    )
    db.dbapi.execute(sql, params)
    order = [row[0] for row in db.dbapi.fetchall()]
    assert set(order[:2]) == {handles["father"], handles["mother"]}


# --- [x.f for x in rel if cond][n]: one entry, chosen by a condition ---------


_OCCUPATIONS = "[a.value for a in attribute_list if a.type.value == AttributeType.OCCUPATION]"


@pytest.mark.parametrize(
    "entry, expected",
    [
        (_OCCUPATIONS + "[0]", {"father": "Farmer"}),
        (_OCCUPATIONS + "[1]", {"father": "Miller"}),
        (_OCCUPATIONS + "[2]", {}),
        (
            "[a for a in attribute_list if a.type.value == AttributeType.OCCUPATION][0].value",
            {"father": "Farmer"},
        ),
        ("[a.value for a in attribute_list][0]", {"father": "Kalle"}),
        (
            "[f for f in parent_families if f.father.surname == 'Jones'][0]"
            ".mother.primary_name.first_name",
            {"child": "Ann"},
        ),
        ("[f for f in parent_families][1].father.surname", {"child": "Jones"}),
        (
            "[f.father.surname for f in parent_families if f.mother.surname == 'Baker'][0]",
            {"child": "Anderson"},
        ),
        ("[f for f in parent_families if f.father.surname == 'Nobody'][0].gramps_id", {}),
    ],
)
def test_picked_entry_select(indexed_hop_db, pg_conn, entry, expected):
    """A select of a list comprehension taken by index gives that matching
    entry's value, in stored order, NULL past the last match -- on SQLite,
    PostgreSQL and the evaluator alike."""
    test_indexed_collection_hop_select(indexed_hop_db, pg_conn, entry, expected)


@pytest.mark.parametrize(
    "where_expr, expected",
    [
        (_OCCUPATIONS + "[0] == 'Farmer'", {"father"}),
        (_OCCUPATIONS + "[1] == 'Farmer'", set()),
        ("[f for f in parent_families if f.father.surname == 'Jones'][0].mother.surname == 'Lee'", {"child"}),
    ],
)
def test_picked_entry_where(indexed_hop_db, pg_conn, where_expr, expected):
    db, handles = indexed_hop_db
    _assert_sql_and_evaluator_agree(
        db, handles, PERSON, _PEOPLE, where_expr, expected, pg_conn
    )


# --- attribute_list['X']: the first attribute of a type ----------------------


@pytest.mark.parametrize(
    "entry, expected",
    [
        # Built-ins are stored with an empty name, so these match by number.
        ("attribute_list['Occupation'].value", {"father": "Farmer"}),
        ("attribute_list['Nickname'].value", {"father": "Kalle"}),
        # A custom type matches by its stored name.
        ("attribute_list['Hair color'].value", {"father": "Brown"}),
        ("attribute_list['Caste'].value", {}),
        ("attribute_list['hair color'].value", {}),
    ],
)
def test_attribute_lookup_select(indexed_hop_db, pg_conn, entry, expected):
    test_indexed_collection_hop_select(indexed_hop_db, pg_conn, entry, expected)


@pytest.mark.parametrize(
    "where_expr, expected",
    [
        ("attribute_list['Occupation'].value == 'Farmer'", {"father"}),
        ("attribute_list['Occupation'].value == 'Miller'", set()),
        ("attribute_list['Hair color'] != None", {"father"}),
        ("attribute_list['Hair color'] == None", {"mother", "child", "orphan"}),
        ("any(e.attribute_list['Occupation'].value == 'Farmer' for e in events)", set()),
    ],
)
def test_attribute_lookup_where(indexed_hop_db, pg_conn, where_expr, expected):
    db, handles = indexed_hop_db
    _assert_sql_and_evaluator_agree(
        db, handles, PERSON, _PEOPLE, where_expr, expected, pg_conn
    )


@pytest.mark.parametrize(
    "where_expr, expected",
    [
        # All the entries of a type, not just the first: the father has
        # two occupations, Farmer then Miller.
        ("len(attribute_list['Occupation']) == 2", {"father"}),
        ("len(attribute_list['Occupation'], value == 'Miller') == 1", {"father"}),
        ("len([a for a in attribute_list['Occupation'] if a.value == 'Miller']) == 1", {"father"}),
        ("len(attribute_list['Hair color']) == 1", {"father"}),
        ("len(attribute_list['Caste']) == 0", {"father", "mother", "child", "orphan"}),
        ("any(attribute_list['Occupation'])", {"father"}),
        ("any(attribute_list['Nickname'])", {"father"}),
        ("any(attribute_list['Caste'])", set()),
        ("any(attribute_list['Occupation'], value == 'Miller')", {"father"}),
        ("any(attribute_list['Nickname'], value == 'Miller')", set()),
        ("any(a.value == 'Miller' for a in attribute_list['Occupation'])", {"father"}),
        ("any(a for a in attribute_list['Occupation'] if a.value == 'Miller')", {"father"}),
        ("any(a.value == 'Miller' for a in attribute_list['Nickname'])", set()),
        ("[a.value for a in attribute_list['Occupation']][1] == 'Miller'", {"father"}),
        ("[a for a in attribute_list['Occupation'] if a.value != 'Farmer'][0].value == 'Miller'", {"father"}),
        ("len(distinct([a.value for a in attribute_list['Occupation']])) == 2", {"father"}),
        # urls: built-in types by number, a custom one by name.
        ("urls['Web Home'].path == 'https://lena.example'", {"mother"}),
        ("urls['E-mail'].path == 'lena@example.org'", {"mother"}),
        ("urls['Blog'].path == 'https://lena.example/blog'", {"mother"}),
        ("urls['Blog'] == None", {"father", "child", "orphan"}),
        ("len(urls['E-mail']) == 2", {"mother"}),
        ("any(u.path == 'baker@example.org' for u in urls['E-mail'])", {"mother"}),
        ("any(u.path == 'baker@example.org' for u in urls['Web Home'])", set()),
        ("any(urls['Web Search'])", set()),
    ],
)
def test_entries_of_a_type_where(indexed_hop_db, pg_conn, where_expr, expected):
    """`list['Kind']` is every entry of that kind, looped over, counted or
    tested; read straight off, it is the first."""
    db, handles = indexed_hop_db
    _assert_sql_and_evaluator_agree(
        db, handles, PERSON, _PEOPLE, where_expr, expected, pg_conn
    )


@pytest.mark.parametrize(
    "entry, expected",
    [
        ("len(attribute_list['Occupation'])", {"father": 2, "mother": 0, "child": 0, "orphan": 0}),
        ("urls['E-mail'].path", {"mother": "lena@example.org"}),
        ("[u.path for u in urls['E-mail']][1]", {"mother": "baker@example.org"}),
    ],
)
def test_entries_of_a_type_select(indexed_hop_db, pg_conn, entry, expected):
    test_indexed_collection_hop_select(indexed_hop_db, pg_conn, entry, expected)


def test_attribute_lookup_through_link(indexed_hop_db, pg_conn):
    """One link away: a Citation reads its Source's attribute by type, the
    citation-template case."""
    from gramps_object_query_language.query import CITATION, Dialect, Query, compile_query
    from gramps_object_query_language.query_lang import parse_select

    db, handles = indexed_hop_db
    [(ref, _)] = parse_select(CITATION, ["source.attribute_list['Film'].value as v"])
    query = Query(select=["handle", ref])
    want = {handles["citation"]: "FHL 123"}
    sql, params = compile_query(CITATION, query, dialect=Dialect.SQLITE)
    db.dbapi.execute(sql, params)
    assert dict(db.dbapi.fetchall()) == want, "SQLite"
    if pg_conn is not None:
        sql, params = compile_query(CITATION, query, dialect=Dialect.POSTGRESQL)
        assert dict(_rows_on_postgresql(pg_conn, db, sql, params)) == want, "PostgreSQL"
    citation = db.get_citation_from_handle(handles["citation"])
    assert resolve_column_ref(db, citation, ref, CITATION) == "FHL 123", "evaluator"


def test_picked_entry_sorts(indexed_hop_db):
    """order_by takes a picked entry too: the one person with an occupation
    sorts last ascending (NULLs first)."""
    from gramps_object_query_language.query import Dialect, Query, compile_query
    from gramps_object_query_language.query_lang import parse_order_by

    db, handles = indexed_hop_db
    order_by = parse_order_by(PERSON, [_OCCUPATIONS + "[0]", "gramps_id"])
    sql, params = compile_query(
        PERSON, Query(select=["handle"], order_by=order_by), dialect=Dialect.SQLITE
    )
    db.dbapi.execute(sql, params)
    assert [row[0] for row in db.dbapi.fetchall()][-1] == handles["father"]


# --- arithmetic ----------------------------------------------------------------


@pytest.fixture(scope="module")
def lifespan_db():
    """People with birth and death dates -- a full lifespan, one still
    living (no death), one with no birth, and a year-only pair -- for
    `death.date.dateval[2] - birth.date.dateval[2]` and friends."""
    dbman = CLIDbManager(DbState())
    dirpath, db_name = dbman.create_new_db_cli("_test_evaluator_lifespan", dbid="sqlite")
    db = make_database("sqlite")
    db.load(dirpath)
    handles = {}

    def event(trans, event_type, year, month=0, day=0, text=None):
        ev = Event()
        ev.set_type(event_type)
        date = Date()
        if text is not None:
            date.set_as_text(text)
        elif year is not None:
            date.set_yr_mon_day(year, month, day)
        ev.set_date_object(date)
        return db.add_event(ev, trans)

    with DbTxn("setup", db) as trans:
        for key, birth, death in (
            ("long", (1850, 3, 1), (1930, 2, 1)),  # 80 by year, 79 by days
            ("short", (1900, 1, 1), (1905, 6, 1)),
            ("living", (1990, 1, 1), None),
            ("unborn", None, (1950, 1, 1)),
            ("years", (1700, 0, 0), (1790, 0, 0)),
            # A birth event with no date at all, and one with only text: both
            # store sortval 0 and a zero dateval. Not in _LIVES.
            ("undated", (None,), (1950, 1, 1)),
            ("textonly", (None, 0, 0, "abt. when grandpa was young"), (1950, 1, 1)),
        ):
            person = Person()
            person.set_primary_name(_name(key.title(), "Life"))
            if birth:
                person.set_birth_ref(_event_ref(event(trans, EventType.BIRTH, *birth)))
            if death:
                ref = _event_ref(event(trans, EventType.DEATH, *death))
                person.add_event_ref(ref)
                person.set_death_ref(ref)
            handles[key] = db.add_person(person, trans)
    yield db, handles
    db.close()
    dbman.remove_database(db_name)


_LIVES = ("long", "short", "living", "unborn", "years")


@pytest.mark.parametrize(
    "entry, expected",
    [
        ("death.date.dateval[2] - birth.date.dateval[2]", {"long": 80, "short": 5, "years": 90}),
        ("(death.date.sortval - birth.date.sortval) // 365", {"long": 79, "short": 5, "years": 90}),
        ("birth.date.dateval[2] // 0", {}),
        ("-birth.date.dateval[2] // 7", {"long": -265, "short": -272, "living": -285, "years": -243}),
        ("len(event_ref_list) * 10 + 1", {"long": 21, "short": 21, "living": 11, "unborn": 11, "years": 21}),
    ],
)
def test_arithmetic_select(lifespan_db, pg_conn, entry, expected):
    """Arithmetic gives the same numbers on SQLite, PostgreSQL and the
    evaluator, whole numbers staying whole, and no value where a side has
    none or a divisor is zero."""
    from gramps_object_query_language.query import Dialect, Query, compile_query
    from gramps_object_query_language.query_lang import parse_select

    db, handles = lifespan_db
    [(ref, _)] = parse_select(PERSON, [entry + " as v"])
    want = {handles[key]: expected.get(key) for key in _LIVES}
    query = Query(select=["handle", ref])
    sql, params = compile_query(PERSON, query, dialect=Dialect.SQLITE)
    db.dbapi.execute(sql, params)
    assert {h: v for h, v in db.dbapi.fetchall() if h in want} == want, "SQLite"
    if pg_conn is not None:
        sql, params = compile_query(PERSON, query, dialect=Dialect.POSTGRESQL)
        got = {h: v for h, v in _rows_on_postgresql(pg_conn, db, sql, params) if h in want}
        assert got == want, "PostgreSQL"
        assert all(type(v) is int for v in got.values() if v is not None)
    for handle, value in want.items():
        assert resolve_column_ref(db, db.get_person_from_handle(handle), ref, PERSON) == value


def test_true_division_is_a_float(lifespan_db, pg_conn):
    from gramps_object_query_language.query import Dialect, Query, compile_query
    from gramps_object_query_language.query_lang import parse_select

    db, handles = lifespan_db
    [(ref, _)] = parse_select(PERSON, ["(death.date.dateval[2] - birth.date.dateval[2]) / 4 as v"])
    query = Query(select=["handle", ref])
    sql, params = compile_query(PERSON, query, dialect=Dialect.SQLITE)
    db.dbapi.execute(sql, params)
    assert dict(db.dbapi.fetchall())[handles["short"]] == 1.25
    if pg_conn is not None:
        sql, params = compile_query(PERSON, query, dialect=Dialect.POSTGRESQL)
        assert dict(_rows_on_postgresql(pg_conn, db, sql, params))[handles["short"]] == 1.25
    assert resolve_column_ref(db, db.get_person_from_handle(handles["short"]), ref, PERSON) == 1.25


@pytest.mark.parametrize(
    "where_expr, expected",
    [
        ("death.date.dateval[2] - birth.date.dateval[2] > 85", {"years"}),
        ("death.date.dateval[2] > birth.date.dateval[2] + 85", {"years"}),
        ("10 > death.date.dateval[2] - birth.date.dateval[2]", {"short"}),
        ("death.date.dateval[2] == birth.date.dateval[2] + 80", {"long"}),
        # No value isn't "not more than 50" either (see Not's missing-value rule).
        ("not (death.date.dateval[2] - birth.date.dateval[2] > 50)", {"short"}),
    ],
)
def test_arithmetic_where(lifespan_db, pg_conn, where_expr, expected):
    db, handles = lifespan_db
    _assert_sql_and_evaluator_agree(db, handles, PERSON, _LIVES, where_expr, expected, pg_conn)


def test_arithmetic_sort_pages_on_postgresql(lifespan_db, pg_conn):
    """Sorting by a computed value pages like any other sort key."""
    from gramps_object_query_language.query_lang import parse_order_by, parse_select

    if pg_conn is None:
        pytest.skip("needs GOQL_TEST_POSTGRES_DSN")
    db, _ = lifespan_db
    select = [ref for ref, _ in parse_select(PERSON, ["handle", "death.date.dateval[2] - birth.date.dateval[2] as age"])]
    order_by = parse_order_by(PERSON, ["-(death.date.dateval[2] - birth.date.dateval[2])"])

    def run(sql, params):
        return _rows_on_postgresql(pg_conn, db, sql, params)

    expected = _one_query(run, PERSON, select, order_by)
    assert len(expected) == 7
    assert _page_through(run, PERSON, select, order_by) == expected


@pytest.mark.parametrize(
    "entry",
    [
        "death.date.dateval[2] - birth.date.dateval[2]",
        "(death.date.sortval - birth.date.sortval) // 365",
        "birth.date.dateval[2] + 0",
        "birth.date.sortval * 1",
    ],
)
def test_arithmetic_treats_an_undated_date_as_missing(lifespan_db, pg_conn, entry):
    """A birth event with no date, or a text-only one, stores sortval 0: in
    arithmetic its numbers count as missing (no value), not as year 0."""
    from gramps_object_query_language.query import Dialect, Query, compile_query
    from gramps_object_query_language.query_lang import parse_select

    db, handles = lifespan_db
    [(ref, _)] = parse_select(PERSON, [entry + " as v"])
    query = Query(select=["handle", ref])
    sql, params = compile_query(PERSON, query, dialect=Dialect.SQLITE)
    db.dbapi.execute(sql, params)
    rows = dict(db.dbapi.fetchall())
    for key in ("undated", "textonly"):
        assert rows[handles[key]] is None, ("SQLite", key)
        person = db.get_person_from_handle(handles[key])
        assert resolve_column_ref(db, person, ref, PERSON) is None, ("evaluator", key)
    assert rows[handles["long"]] is not None
    if pg_conn is not None:
        sql, params = compile_query(PERSON, query, dialect=Dialect.POSTGRESQL)
        pg = dict(_rows_on_postgresql(pg_conn, db, sql, params))
        assert pg[handles["undated"]] is None and pg[handles["textonly"]] is None
        assert pg[handles["long"]] == rows[handles["long"]]


def test_comparisons_still_see_an_undated_date_as_zero(lifespan_db, pg_conn):
    """Only arithmetic changes: a filter can still find undated births."""
    db, handles = lifespan_db
    _assert_sql_and_evaluator_agree(
        db, handles, PERSON, ("long", "undated", "textonly", "unborn"),
        "birth.date.sortval == 0", {"undated", "textonly"}, pg_conn,
    )


@pytest.mark.parametrize(
    "spec_name, keys, where_expr, expected",
    [
        # Every child a birth child of both parents: the first family's one
        # child is; the second family's is a stepchild.
        (
            "family", ("family", "family2"),
            "len([c for c in child_ref_list if c.frel.value == ChildRefType.BIRTH"
            " and c.mrel.value == ChildRefType.BIRTH]) == len(children)",
            {"family"},
        ),
        ("family", ("family", "family2"), "0 < len(children)", {"family", "family2"}),
        ("person", _PEOPLE, "len(attribute_list) > len(citation_list)", {"father"}),
        (
            "person", _PEOPLE,
            "'Farmer' == [a.value for a in attribute_list if a.type.value == AttributeType.OCCUPATION][0]",
            {"father"},
        ),
        ("person", _PEOPLE, "len(parent_family_list) == len(family_list) + 2", {"child"}),
    ],
)
def test_computed_value_on_the_right(indexed_hop_db, pg_conn, spec_name, keys, where_expr, expected):
    """A count, a picked entry or arithmetic works on either side of a
    comparison, the same on SQLite, PostgreSQL and the evaluator."""
    db, handles = indexed_hop_db
    spec = {"family": FAMILY, "person": PERSON}[spec_name]
    _assert_sql_and_evaluator_agree(db, handles, spec, keys, where_expr, expected, pg_conn)


# --- counting a field: len([f.father for f in ...]), len(distinct([...])) ---


@pytest.fixture(scope="module")
def distinct_db():
    """People whose parent families, events and alternate names repeat
    values, for counting fields and distinct values:

    - `two_fathers`: child of (Adam, Mary) and (Bill, Mary).
    - `same_father`: child of (Adam, Mary) and (Adam, Nora) -- one father
      recorded in two families.
    - `no_father`: child of (nobody, Mary).
    - `orphan`: no parent families, events, or alternate names.
    - `traveler`: events in Chicago, Chicago, Boston and nowhere; alternate
      first names Bob, Bob, Rob and an empty one.
    """
    dbman = CLIDbManager(DbState())
    dirpath, db_name = dbman.create_new_db_cli("_test_evaluator_distinct", dbid="sqlite")
    db = make_database("sqlite")
    db.load(dirpath)
    handles = {}
    with DbTxn("setup", db) as trans:
        for key, first in (("adam", "Adam"), ("bill", "Bill"), ("mary", "Mary"), ("nora", "Nora")):
            parent = Person()
            parent.set_primary_name(_name(first, "Parent"))
            handles[key] = db.add_person(parent, trans)
        for key in ("two_fathers", "same_father", "no_father", "orphan", "traveler"):
            person = Person()
            person.set_primary_name(_name(key, "Child"))
            handles[key] = db.add_person(person, trans)

        def add_family(child_key, father_key, mother_key):
            family = Family()
            if father_key:
                family.set_father_handle(handles[father_key])
            family.set_mother_handle(handles[mother_key])
            child_ref = ChildRef()
            child_ref.set_reference_handle(handles[child_key])
            family.add_child_ref(child_ref)
            family_handle = db.add_family(family, trans)
            child = db.get_person_from_handle(handles[child_key])
            child.add_parent_family_handle(family_handle)
            db.commit_person(child, trans)

        add_family("two_fathers", "adam", "mary")
        add_family("two_fathers", "bill", "mary")
        add_family("same_father", "adam", "mary")
        add_family("same_father", "adam", "nora")
        add_family("no_father", None, "mary")

        places = {}
        for title in ("Chicago", "Boston"):
            place = Place()
            place.set_title(title)
            places[title] = db.add_place(place, trans)
        traveler = db.get_person_from_handle(handles["traveler"])
        for title in ("Chicago", "Chicago", "Boston", None):
            event = Event()
            event.set_type(EventType.RESIDENCE)
            if title:
                event.set_place_handle(places[title])
            ref = EventRef()
            ref.set_reference_handle(db.add_event(event, trans))
            traveler.add_event_ref(ref)
        for first in ("Bob", "Bob", "Rob", ""):
            traveler.add_alternate_name(_name(first, "Child"))
        db.commit_person(traveler, trans)
    yield db, handles
    db.close()
    dbman.remove_database(db_name)


_DISTINCT_PEOPLE = ("two_fathers", "same_father", "no_father", "orphan", "traveler")


@pytest.mark.parametrize(
    "expr, expected",
    [
        (
            "len(distinct([f.father for f in parent_families]))",
            {"two_fathers": 2, "same_father": 1, "no_father": 0, "orphan": 0, "traveler": 0},
        ),
        (
            "len(distinct([f.mother for f in parent_families]))",
            {"two_fathers": 1, "same_father": 2, "no_father": 1, "orphan": 0, "traveler": 0},
        ),
        # A list of fathers leaves out the families with no father.
        (
            "len([f.father for f in parent_families])",
            {"two_fathers": 2, "same_father": 2, "no_father": 0, "orphan": 0, "traveler": 0},
        ),
        (
            "len([f for f in parent_families])",
            {"two_fathers": 2, "same_father": 2, "no_father": 1, "orphan": 0, "traveler": 0},
        ),
        # How many times a father repeats across someone's parent families.
        (
            "len([f.father for f in parent_families])"
            " - len(distinct([f.father for f in parent_families]))",
            {"two_fathers": 0, "same_father": 1, "no_father": 0, "orphan": 0, "traveler": 0},
        ),
        (
            "len(distinct([f.father.primary_name.first_name for f in parent_families]))",
            {"two_fathers": 2, "same_father": 1, "no_father": 0, "orphan": 0, "traveler": 0},
        ),
        (
            "len(distinct([f.father for f in parent_families if f.mother.primary_name.first_name == 'Mary']))",
            {"two_fathers": 2, "same_father": 1, "no_father": 0, "orphan": 0, "traveler": 0},
        ),
        (
            "len(distinct([e.place for e in events]))",
            {"two_fathers": 0, "same_father": 0, "no_father": 0, "orphan": 0, "traveler": 2},
        ),
        (
            "len([e.place for e in events])",
            {"two_fathers": 0, "same_father": 0, "no_father": 0, "orphan": 0, "traveler": 3},
        ),
        (
            "len(distinct([e.place.title for e in events]))",
            {"two_fathers": 0, "same_father": 0, "no_father": 0, "orphan": 0, "traveler": 2},
        ),
        # A self-linked collection: this person's own child reference in
        # each parent family.
        (
            "len(distinct([c.frel.value for c in child_refs]))",
            {"two_fathers": 1, "same_father": 1, "no_father": 1, "orphan": 0, "traveler": 0},
        ),
        # A list inside the record; the empty first name isn't counted.
        (
            "len(distinct([n.first_name for n in alternate_names]))",
            {"two_fathers": 0, "same_father": 0, "no_father": 0, "orphan": 0, "traveler": 2},
        ),
        (
            "len([n.first_name for n in alternate_names])",
            {"two_fathers": 0, "same_father": 0, "no_father": 0, "orphan": 0, "traveler": 3},
        ),
        (
            "len(distinct([n.first_name for n in alternate_names if n.first_name != 'Rob']))",
            {"two_fathers": 0, "same_father": 0, "no_father": 0, "orphan": 0, "traveler": 1},
        ),
    ],
)
def test_counting_a_field_and_distinct_values(distinct_db, pg_conn, expr, expected):
    """A counted field and a distinct count give the same numbers on SQLite,
    PostgreSQL and the evaluator."""
    from gramps_object_query_language.query import Dialect, Query, compile_query
    from gramps_object_query_language.query_lang import parse_select

    db, handles = distinct_db
    [(ref, _key)] = parse_select(PERSON, [f"{expr} as n"])
    query = Query(select=["handle", ref])
    want = {handles[key]: value for key, value in expected.items()}
    sql, params = compile_query(PERSON, query, dialect=Dialect.SQLITE)
    db.dbapi.execute(sql, params)
    got = {handle: value for handle, value in db.dbapi.fetchall() if handle in want}
    assert got == want, "SQLite"
    if pg_conn is not None:
        sql, params = compile_query(PERSON, query, dialect=Dialect.POSTGRESQL)
        rows = _rows_on_postgresql(pg_conn, db, sql, params)
        assert {handle: value for handle, value in rows if handle in want} == want, "PostgreSQL"
    for key, value in expected.items():
        person = db.get_person_from_handle(handles[key])
        assert resolve_column_ref(db, person, ref, PERSON) == value, f"evaluator on {key}"


def test_distinct_count_as_a_filter(distinct_db, pg_conn):
    db, handles = distinct_db
    _assert_sql_and_evaluator_agree(
        db, handles, PERSON, _DISTINCT_PEOPLE,
        "len(distinct([f.father for f in parent_families])) > 1", {"two_fathers"}, pg_conn,
    )
    _assert_sql_and_evaluator_agree(
        db, handles, PERSON, _DISTINCT_PEOPLE,
        "len([f.father for f in parent_families]) > len(distinct([f.father for f in parent_families]))",
        {"same_father"}, pg_conn,
    )


# --- a Gramps type compared with its name ------------------------------------


@pytest.fixture(scope="module")
def type_names_db():
    """Events, families and people whose types are built-in or custom, for
    `type == "Census"`-style comparisons. `FamilyRelType.CUSTOM` (4) and
    `ChildRefType.CUSTOM` (7) aren't 0 like most classes' -- the custom
    cases here would miss if that number were hardcoded."""
    from gramps.gen.lib import FamilyRelType

    dbman = CLIDbManager(DbState())
    dirpath, db_name = dbman.create_new_db_cli("_test_evaluator_type_names", dbid="sqlite")
    db = make_database("sqlite")
    db.load(dirpath)
    handles = {}
    with DbTxn("setup", db) as trans:
        for key, event_type in (
            ("e_census", EventType.CENSUS),
            ("e_birth", EventType.BIRTH),
            ("e_baptism", EventType.BAPTISM),
            ("e_tax", (EventType.CUSTOM, "Tax list")),
            ("e_muster", (EventType.CUSTOM, "Muster roll")),
        ):
            event = Event()
            event.set_type(event_type)
            event.set_gramps_id(key)
            handles[key] = db.add_event(event, trans)

        child = Person()
        child.set_primary_name(_name("Kid", "Smith"))
        birth_ref = _event_ref(handles["e_birth"])
        child.add_event_ref(birth_ref)
        child.set_birth_ref(birth_ref)
        handles["p_kid"] = db.add_person(child, trans)
        other = Person()
        other.set_primary_name(_name("Other", "Smith"))
        other.add_event_ref(_event_ref(handles["e_census"]))
        handles["p_other"] = db.add_person(other, trans)

        for key, rel_type, frel in (
            ("f_married", FamilyRelType.MARRIED, ChildRefType.BIRTH),
            ("f_handfast", (FamilyRelType.CUSTOM, "Handfasting"), ChildRefType.ADOPTED),
            ("f_foster", FamilyRelType.UNKNOWN, (ChildRefType.CUSTOM, "Kinship care")),
        ):
            family = Family()
            family.set_relationship(FamilyRelType(rel_type))
            ref = ChildRef()
            ref.set_reference_handle(handles["p_kid"])
            ref.set_father_relation(ChildRefType(frel))
            family.add_child_ref(ref)
            handles[key] = db.add_family(family, trans)
    yield db, handles
    db.close()
    dbman.remove_database(db_name)


_TYPE_NAME_EVENTS = ("e_census", "e_birth", "e_baptism", "e_tax", "e_muster")


@pytest.mark.parametrize(
    "where_expr, expected",
    [
        ("type == 'Census'", {"e_census"}),
        ("type == 'census'", {"e_census"}),
        ("'Census' == type", {"e_census"}),
        ("type is 'Census'", {"e_census"}),
        ("type == 21", {"e_census"}),
        ("type != 'Census'", {"e_birth", "e_baptism", "e_tax", "e_muster"}),
        ("type is not 'Census'", {"e_birth", "e_baptism", "e_tax", "e_muster"}),
        ("type in ['Birth', 'Baptism']", {"e_birth", "e_baptism"}),
        ("type not in ['Birth', 'Baptism']", {"e_census", "e_tax", "e_muster"}),
        # custom names: value == EventType.CUSTOM and string == the name
        ("type == 'Tax list'", {"e_tax"}),
        ("type != 'Tax list'", {"e_census", "e_birth", "e_baptism", "e_muster"}),
        ("type in ['Tax list', 'Muster roll']", {"e_tax", "e_muster"}),
        ("type in ['Census', 'Tax list']", {"e_census", "e_tax"}),
        ("type not in ['Census', 'Tax list']", {"e_birth", "e_baptism", "e_muster"}),
        ("type == 'No such type'", set()),
        ("type == 'Census' or type == 'Tax list'", {"e_census", "e_tax"}),
    ],
)
def test_type_name_events(type_names_db, pg_conn, where_expr, expected):
    db, handles = type_names_db
    _assert_sql_and_evaluator_agree(
        db, handles, EVENT, _TYPE_NAME_EVENTS, where_expr, expected, pg_conn
    )


@pytest.mark.parametrize(
    "where_expr, expected",
    [
        ("type == 'Married'", {"f_married"}),
        ("type == 'Handfasting'", {"f_handfast"}),
        ("type in ['Married', 'Handfasting']", {"f_married", "f_handfast"}),
        ("any(c.frel == 'Adopted' for c in child_ref_list)", {"f_handfast"}),
        ("any(c.frel == 'Kinship care' for c in child_ref_list)", {"f_foster"}),
        ("any(c.frel in ['Birth', 'Kinship care'] for c in child_ref_list)", {"f_married", "f_foster"}),
    ],
)
def test_type_name_families(type_names_db, pg_conn, where_expr, expected):
    db, handles = type_names_db
    _assert_sql_and_evaluator_agree(
        db, handles, FAMILY, ("f_married", "f_handfast", "f_foster"), where_expr, expected, pg_conn
    )


@pytest.mark.parametrize(
    "where_expr, expected",
    [
        ("birth.type == 'Birth'", {"p_kid"}),
        ("any(r.role == 'Primary' for r in event_ref_list)", {"p_kid", "p_other"}),
        ("any(e.type == 'Census' for e in events)", {"p_other"}),
        ("primary_name.type == 'Birth Name'", {"p_kid", "p_other"}),
    ],
)
def test_type_name_people(type_names_db, pg_conn, where_expr, expected):
    db, handles = type_names_db
    _assert_sql_and_evaluator_agree(
        db, handles, PERSON, ("p_kid", "p_other"), where_expr, expected, pg_conn
    )


# --- event_refs and referrer: the people and families pointing at an event ---


@pytest.fixture(scope="module")
def event_refs_db():
    """Ann and Bob, married (family F0001) with a child Cat, and events
    linked to them in different roles:

    - death: Ann, Primary.
    - census: Ann and Bob both Primary, Cat a Witness.
    - marriage: the family, role Family; Cat a Witness.
    - double: Bob twice, Primary and Informant.
    - orphan: nobody.

    Handles are set so that byte order (`Bzz_ann` before `aaa_bob`) and
    case-insensitive order disagree: `[0]` follows byte order everywhere.
    """
    dbman = CLIDbManager(DbState())
    dirpath, _name_ = dbman.create_new_db_cli("_test_evaluator_event_refs", dbid="sqlite")
    db = make_database("sqlite")
    db.load(dirpath)
    handles = {}

    def event(key, event_type, year=None):
        e = Event()
        e.set_handle(f"ev_{key}")
        e.set_type(event_type)
        if year is not None:
            d = Date()
            d.set_yr_mon_day(year, 6, 1)
            e.set_date_object(d)
        handles[key] = db.add_event(e, trans)
        return handles[key]

    def link(obj, event_handle, role):
        ref = EventRef()
        ref.set_reference_handle(event_handle)
        ref.set_role(role)
        obj.add_event_ref(ref)

    with DbTxn("setup", db) as trans:
        ann_birth = event("ann_birth", EventType.BIRTH, 1900)
        bob_birth = event("bob_birth", EventType.BIRTH, 1890)
        death = event("death", EventType.DEATH, 1980)
        census = event("census", EventType.CENSUS, 1930)
        marriage = event("marriage", EventType.MARRIAGE, 1920)
        double = event("double", EventType.RESIDENCE, 1940)
        event("orphan", EventType.BURIAL)

        people = {}
        for key, handle, gramps_id, given, gender in (
            ("ann", "Bzz_ann", "I0001", "Ann", Person.FEMALE),
            ("bob", "aaa_bob", "I0002", "Bob", Person.MALE),
            ("cat", "ccc_cat", "I0003", "Cat", Person.FEMALE),
        ):
            p = Person()
            p.set_handle(handle)
            p.set_gramps_id(gramps_id)
            p.set_gender(gender)
            p.set_primary_name(_name(given, "Smith"))
            people[key] = p
        link(people["ann"], ann_birth, EventRoleType.PRIMARY)
        people["ann"].set_birth_ref(people["ann"].get_event_ref_list()[0])
        link(people["ann"], death, EventRoleType.PRIMARY)
        link(people["ann"], census, EventRoleType.PRIMARY)
        link(people["bob"], bob_birth, EventRoleType.PRIMARY)
        people["bob"].set_birth_ref(people["bob"].get_event_ref_list()[0])
        link(people["bob"], census, EventRoleType.PRIMARY)
        link(people["bob"], double, EventRoleType.PRIMARY)
        link(people["bob"], double, EventRoleType.INFORMANT)
        link(people["cat"], census, EventRoleType.WITNESS)
        link(people["cat"], marriage, EventRoleType.WITNESS)

        family = Family()
        family.set_handle("fam_f")
        family.set_gramps_id("F0001")
        family.set_father_handle("aaa_bob")
        family.set_mother_handle("Bzz_ann")
        child = ChildRef()
        child.set_reference_handle("ccc_cat")
        child.set_father_relation(ChildRefType.ADOPTED)
        family.add_child_ref(child)
        link(family, marriage, EventRoleType.FAMILY)
        people["ann"].add_family_handle("fam_f")
        people["bob"].add_family_handle("fam_f")
        people["cat"].add_parent_family_handle("fam_f")
        for key, p in people.items():
            handles[key] = db.add_person(p, trans)
        handles["family"] = db.add_family(family, trans)
    yield db, handles
    db.close()
    dbman.remove_database(_name_)


_EVENTS = ("death", "census", "marriage", "double", "orphan")


@pytest.mark.parametrize(
    "where_expr, expected",
    [
        ("any(event_refs)", {"death", "census", "marriage", "double"}),
        ("not any(event_refs)", {"orphan"}),
        ("len(event_refs) == 3", {"census"}),
        ("len(event_refs) == 2", {"marriage", "double"}),
        ("len([r for r in event_refs]) == 2", {"marriage", "double"}),
        ("len(event_refs['Primary']) == 2", {"census"}),
        ("len(event_refs['Primary']) == 1", {"death", "double"}),
        ("len(event_refs, role.value == EventRoleType.WITNESS) == 1", {"census", "marriage"}),
        ("any(event_refs['Family'])", {"marriage"}),
        ("any(event_refs['Informant'], referrer.gramps_id == 'I0002')", {"double"}),
        ("any(r.referrer.primary_name.first_name == 'Cat' for r in event_refs['Witness'])", {"census", "marriage"}),
        ("any(r for r in event_refs if r.referrer.gender == Person.MALE)", {"census", "double"}),
        # A path only a Family has: no value on a person's entry.
        ("any(r.referrer.father.primary_name.first_name == 'Bob' for r in event_refs)", {"marriage"}),
        ("any(r.referrer.father.gramps_id == None for r in event_refs)", {"death", "census", "marriage", "double"}),
        ("any(event_refs, referrer.gramps_id == 'F0001')", {"marriage"}),
        ("len(distinct([r.referrer.gramps_id for r in event_refs])) == 1", {"death", "double"}),
        ("len([r.referrer.gramps_id for r in event_refs]) == 2", {"marriage", "double"}),
        # [0] is the first by byte order of handle: Ann (Bzz_ann) before Bob (aaa_bob).
        ("event_refs['Primary'].referrer.primary_name.first_name == 'Ann'", {"death", "census"}),
        ("[r.referrer.primary_name.first_name for r in event_refs['Primary']][1] == 'Bob'", {"census"}),
        ("[r for r in event_refs if r.role.value == EventRoleType.WITNESS][0].referrer.gramps_id == 'I0003'", {"census", "marriage"}),
        ("event_refs['Primary'] == None", {"marriage", "orphan"}),
        ("event_refs['Primary'].role.value == EventRoleType.PRIMARY", {"death", "census", "double"}),
        ("[r.role.value for r in event_refs][1] == EventRoleType.INFORMANT", {"double"}),
        # People before families.
        ("[r.referrer.gramps_id for r in event_refs][0] == 'I0003'", {"marriage"}),
        ("(date.sortval - event_refs['Primary'].referrer.birth.date.sortval) // 365 == 80", {"death"}),
        ("event_refs['Primary'].referrer.birth.date.sortval < date.sortval", {"death", "census", "double"}),
        ("any(e for e in event_refs if e.referrer.primary_name.first_name < 'b')", {"death", "census"}),
        ("any(e for e in event_refs if e.referrer.gramps_id < 'i0002')", {"death", "census", "marriage"}),
        # One entry of the referrer's own collections.
        ("any(r.referrer.children[0].gramps_id == 'I0003' for r in event_refs)", {"marriage"}),
        ("any(r.referrer.families[0].gramps_id == 'F0001' for r in event_refs['Primary'])", {"death", "census", "double"}),
    ],
)
def test_event_refs_where(event_refs_db, pg_conn, where_expr, expected):
    db, handles = event_refs_db
    _assert_sql_and_evaluator_agree(db, handles, EVENT, _EVENTS, where_expr, expected, pg_conn)


@pytest.mark.parametrize(
    "entry, expected",
    [
        ("event_refs['Primary'].referrer.gramps_id", {"death": "I0001", "census": "I0001", "double": "I0002"}),
        ("event_refs['Family'].referrer.gramps_id", {"marriage": "F0001"}),
        ("event_refs['Primary'].referrer.primary_name.first_name", {"death": "Ann", "census": "Ann", "double": "Bob"}),
        ("len(event_refs)", {"death": 1, "census": 3, "marriage": 2, "double": 2, "orphan": 0}),
        ("len(event_refs['Primary'])", {"death": 1, "census": 2, "marriage": 0, "double": 1, "orphan": 0}),
        ("(date.sortval - event_refs['Primary'].referrer.birth.date.sortval) // 365", {"death": 80, "census": 30, "double": 50}),
        ("event_refs['Witness'].role.value", {"census": EventRoleType.WITNESS, "marriage": EventRoleType.WITNESS}),
    ],
)
def test_event_refs_select(event_refs_db, pg_conn, entry, expected):
    from gramps_object_query_language.query import Dialect, Query, compile_query, select_normalizer
    from gramps_object_query_language.query_lang import parse_select

    db, handles = event_refs_db
    [(ref, _)] = parse_select(EVENT, [entry + " as v"])
    want = {handles[key]: expected.get(key) for key in _EVENTS}
    query = Query(select=["handle", ref])
    normalize = select_normalizer(EVENT, ["handle", ref])
    sql, params = compile_query(EVENT, query, dialect=Dialect.SQLITE)
    db.dbapi.execute(sql, params)
    got = {h: v for h, v in map(normalize, db.dbapi.fetchall()) if h in want}
    assert got == want, "SQLite"
    if pg_conn is not None:
        sql, params = compile_query(EVENT, query, dialect=Dialect.POSTGRESQL)
        rows = [normalize(row) for row in _rows_on_postgresql(pg_conn, db, sql, params)]
        assert {h: v for h, v in rows if h in want} == want, "PostgreSQL"
    for handle, value in want.items():
        assert resolve_column_ref(db, db.get_event_from_handle(handle), ref, EVENT) == value, "evaluator"


@pytest.mark.parametrize(
    "where_expr, expected",
    [
        ("any(r.referrer.father.primary_name.first_name == 'Bob' for r in child_refs)", {"cat"}),
        ("any(child_refs, referrer.gramps_id == 'F0001' and frel.value == ChildRefType.ADOPTED)", {"cat"}),
        ("len([r.referrer.mother for r in child_refs]) == 1", {"cat"}),
        ("len(distinct([r.referrer.father for r in child_refs])) == 1", {"cat"}),
        ("any(r.referrer.gramps_id == 'F0002' for r in child_refs)", set()),
    ],
)
def test_child_refs_referrer(event_refs_db, pg_conn, where_expr, expected):
    """`referrer` on a child_refs entry is the family listing the child."""
    db, handles = event_refs_db
    _assert_sql_and_evaluator_agree(db, handles, PERSON, ("ann", "bob", "cat"), where_expr, expected, pg_conn)


def test_event_refs_respects_privacy(event_refs_db):
    """Through PrivateProxyDb, a private person's link to an event isn't
    seen: the evaluator reads referrers through the proxy."""
    from gramps_object_query_language.query_lang import compile_expr_for_spec

    db, handles = event_refs_db
    cat = db.get_person_from_handle(handles["cat"])
    cat.set_privacy(True)
    with DbTxn("private", db) as trans:
        db.commit_person(cat, trans)
    try:
        proxy = PrivateProxyDb(db)
        census = proxy.get_event_from_handle(handles["census"])
        where = compile_expr_for_spec(EVENT, "any(event_refs['Witness'])")
        assert evaluate_where(proxy, census, where, EVENT) is False
        assert evaluate_where(db, db.get_event_from_handle(handles["census"]), where, EVENT) is True
    finally:
        cat.set_privacy(False)
        with DbTxn("public", db) as trans:
            db.commit_person(cat, trans)


@pytest.mark.parametrize(
    "entry", ["event_refs['Primary'].referrer.primary_name", "event_refs['Primary']", "event_refs['Family'].referrer.father.primary_name"]
)
def test_event_refs_select_objects(event_refs_db, pg_conn, entry):
    """A whole object read through event_refs (a name, the link itself)
    comes back the same from SQLite, PostgreSQL and the evaluator."""
    from gramps_object_query_language.query import Dialect, Query, compile_query, select_normalizer
    from gramps_object_query_language.query_lang import parse_select

    db, handles = event_refs_db
    [(ref, _)] = parse_select(EVENT, [entry + " as v"])
    query = Query(select=["handle", ref])
    normalize = select_normalizer(EVENT, ["handle", ref])
    sql, params = compile_query(EVENT, query, dialect=Dialect.SQLITE)
    db.dbapi.execute(sql, params)
    sqlite = dict(map(normalize, db.dbapi.fetchall()))
    evaluated = {h: resolve_column_ref(db, db.get_event_from_handle(h), ref, EVENT) for h in sqlite}
    assert sqlite == evaluated
    assert any(value is not None for value in sqlite.values())
    if pg_conn is not None:
        sql, params = compile_query(EVENT, query, dialect=Dialect.POSTGRESQL)
        assert dict(normalize(row) for row in _rows_on_postgresql(pg_conn, db, sql, params)) == sqlite


# --- backlinks['Person']: the records of one kind pointing here ------------


@pytest.mark.parametrize(
    "where_expr, expected",
    [
        ("any(backlinks['Person'])", {"death", "census", "marriage", "double"}),
        ("exists(backlinks['Person'], gramps_id == 'I0003')", {"census", "marriage"}),
        ("count(backlinks['Person']) == 0", {"orphan"}),
        ("not any(backlinks['Note'])", set(_EVENTS)),
        ("len(backlinks['Person']) == 3", {"census"}),
        # Bob points at "double" twice; he is one record.
        ("len(backlinks['Person']) == 1", {"death", "marriage", "double"}),
        ("len(backlinks['Family']) == 1", {"marriage"}),
        ("len([p for p in backlinks['Person'] if p.gender == Person.FEMALE]) == 2", {"census"}),
        ("any(p.gender == Person.MALE for p in backlinks['Person'])", {"census", "double"}),
        ("any(backlinks['Family'], father.primary_name.first_name == 'Bob')", {"marriage"}),
        ("any(p.birth.date.sortval > 0 for p in backlinks['Person'])", {"death", "census", "double"}),
        ("any(any(f.gramps_id == 'F0001' for f in p.parent_families) for p in backlinks['Person'])", {"census", "marriage"}),
        ("len(distinct([p.gender for p in backlinks['Person']])) == 2", {"census"}),
        # First by handle, byte by byte: Bzz_ann, aaa_bob, ccc_cat.
        ("backlinks['Person'].gramps_id == 'I0001'", {"death", "census"}),
        ("backlinks['Person'][1].gramps_id == 'I0002'", {"census"}),
        ("backlinks['Person'][2].primary_name.first_name == 'Cat'", {"census"}),
        ("[p.gramps_id for p in backlinks['Person'] if p.gender == Person.MALE][0] == 'I0002'", {"census", "double"}),
        ("backlinks['Person'].gramps_id == None", {"orphan"}),
    ],
)
def test_typed_backlinks_where(event_refs_db, pg_conn, where_expr, expected):
    db, handles = event_refs_db
    _assert_sql_and_evaluator_agree(db, handles, EVENT, _EVENTS, where_expr, expected, pg_conn)


@pytest.mark.parametrize(
    "where_expr, expected",
    [
        ("backlinks['Family'].gramps_id == 'F0001'", {"ann", "bob", "cat"}),
        ("any(f.father.primary_name.first_name == 'Bob' for f in backlinks['Family'])", {"ann", "bob", "cat"}),
        ("not any(backlinks['Person'])", {"ann", "bob", "cat"}),
    ],
)
def test_typed_backlinks_on_people(event_refs_db, pg_conn, where_expr, expected):
    db, handles = event_refs_db
    _assert_sql_and_evaluator_agree(db, handles, PERSON, ("ann", "bob", "cat"), where_expr, expected, pg_conn)


@pytest.mark.parametrize(
    "entry, expected",
    [
        ("backlinks['Person'].primary_name.first_name", {"death": "Ann", "census": "Ann", "marriage": "Cat", "double": "Bob"}),
        ("backlinks['Person'][2].gramps_id", {"census": "I0003"}),
        ("len(backlinks['Person'])", {"death": 1, "census": 3, "marriage": 1, "double": 1, "orphan": 0}),
        ("backlinks['Family'].father.gramps_id", {"marriage": "I0002"}),
    ],
)
def test_typed_backlinks_select(event_refs_db, pg_conn, entry, expected):
    test_event_refs_select(event_refs_db, pg_conn, entry, expected)


def test_typed_backlinks_respects_privacy(event_refs_db):
    """Through PrivateProxyDb a private person isn't among the backlinks."""
    from gramps_object_query_language.query_lang import compile_expr_for_spec

    db, handles = event_refs_db
    cat = db.get_person_from_handle(handles["cat"])
    cat.set_privacy(True)
    with DbTxn("private", db) as trans:
        db.commit_person(cat, trans)
    try:
        proxy = PrivateProxyDb(db)
        where = compile_expr_for_spec(EVENT, "len(backlinks['Person']) == 3")
        assert evaluate_where(db, db.get_event_from_handle(handles["census"]), where, EVENT) is True
        assert evaluate_where(proxy, proxy.get_event_from_handle(handles["census"]), where, EVENT) is False
    finally:
        cat.set_privacy(False)
        with DbTxn("public", db) as trans:
            db.commit_person(cat, trans)
