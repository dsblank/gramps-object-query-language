#
# Gramps Web API - A RESTful API for the Gramps genealogy program
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
from gramps_object_query_language.query import FAMILY, PERSON, TAG, Eq, resolve_column_path


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
