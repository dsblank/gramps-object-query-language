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
    Event,
    EventType,
    Family,
    Name,
    Person,
    Place,
    PlaceName,
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
    FAMILY,
    PERSON,
    PLACE,
    And,
    Eq,
    Gt,
    In,
    Like,
    Ne,
    Not,
    Or,
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

        father = Person()
        father.set_primary_name(_name("Karl", "Anderson"))
        father.set_gender(Person.MALE)
        father.set_birth_ref(_event_ref(handles["birth_father"]))
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

        family = Family()
        family.set_father_handle(handles["father"])
        family.set_mother_handle(handles["mother"])
        handles["family"] = db.add_family(family, trans)

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
