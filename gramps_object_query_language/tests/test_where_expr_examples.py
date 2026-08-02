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

"""Executes every `where_expr` example shown in `docs/where_expr.md` against a
real (in-memory) SQLite database, so the documentation can't silently drift
from what the parser/compiler actually do.

Each test's docstring-free name mirrors the doc section it demonstrates. If
you change an example here, change it in `docs/where_expr.md` too (and vice
versa) -- the two are meant to read as one document, split only because a
markdown file can't assert anything on its own.
"""

import json
import sqlite3

import pytest

from gramps_object_query_language.query import Dialect, Query, compile_query
from gramps_object_query_language.query_lang import compile_expr


@pytest.fixture
def db():
    """An in-memory SQLite database with the same flat/JSON column shape as a
    real Gramps SQLite family tree, populated with two generations of one
    family:

    - `fam2`, the grandparents (William Smith and Mary Doyle), both born in
      Philadelphia in the 1840s and both dying in Chicago.
    - `fam1`, their son and daughter-in-law (John Smith and Jane Smith), both
      born in Chicago; John dies in Boston, Jane dies in Chicago; John's
      death record notes an accident.
    - `kid1`, John and Jane's child, born in Chicago and still living.
    - `other1`, an unrelated person (Alice Jones), used to prove filters
      actually exclude non-matches.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE person (handle TEXT, gender INTEGER, given_name TEXT, "
        "surname TEXT, birth_ref_index INTEGER, death_ref_index INTEGER, "
        "json_data TEXT)"
    )
    conn.execute("CREATE TABLE family (handle TEXT, father_handle TEXT, mother_handle TEXT)")
    conn.execute(
        "CREATE TABLE event (handle TEXT, place TEXT, description TEXT, json_data TEXT)"
    )
    conn.execute("CREATE TABLE place (handle TEXT, title TEXT)")

    def person(handle, gender, given_name, surname, birth_ref=0, death_ref=1):
        conn.execute(
            "INSERT INTO person VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                handle,
                gender,
                given_name,
                surname,
                birth_ref,
                death_ref,
                json.dumps(
                    {
                        "event_ref_list": [
                            {"ref": f"{handle}-birth"},
                            {"ref": f"{handle}-death"},
                        ]
                    }
                ),
            ),
        )

    def event(handle, sortval, place=None, description=None):
        conn.execute(
            "INSERT INTO event VALUES (?, ?, ?, ?)",
            (handle, place, description, json.dumps({"date": {"sortval": sortval}})),
        )

    # Father: John Smith, born 1940 in Chicago (sortval 2429661), died 1990
    # in Boston (sortval 2447893) -- an accident, per the death record.
    person("dad1", gender=1, given_name="John", surname="Smith")
    event("dad1-birth", 2429661, place="chicago")
    event("dad1-death", 2447893, place="boston", description="Died in a car accident.")

    # Mother: Jane Smith, born 1945 in Chicago (sortval 2431477), died 1970
    # in Chicago too (sortval 2440588) -- same place she was born.
    person("mom1", gender=0, given_name="Jane", surname="Smith")
    event("mom1-birth", 2431477, place="chicago")
    event("mom1-death", 2440588, place="chicago", description="Died peacefully at home.")

    # Child, born in Chicago in 1968 (sortval 2439857), still living
    # (no death event -- death_ref_index -1, "no such entry").
    person("kid1", gender=1, given_name="Robert", surname="Smith", death_ref=-1)
    event("kid1-birth", 2439857, place="chicago")

    # An unrelated person, used to prove filters actually exclude non-matches.
    person("other1", gender=0, given_name="Alice", surname="Jones")
    event("other1-birth", 2415021, place="new-york")
    event("other1-death", 2444239, description="Cause unknown.")

    # Grandfather: William Smith, born 1845 in Philadelphia (sortval
    # 2394933), died 1900 in Chicago (sortval 2415021).
    person("granddad1", gender=1, given_name="William", surname="Smith")
    event("granddad1-birth", 2394933, place="philadelphia")
    event("granddad1-death", 2415021, place="chicago")

    # Grandmother: Mary Doyle, born 1848 in Philadelphia (sortval 2396028),
    # died 1900 in Chicago too (sortval 2415021) -- same place, same date as
    # her husband, for this fixture's purposes.
    person("grandma1", gender=0, given_name="Mary", surname="Doyle")
    event("grandma1-birth", 2396028, place="philadelphia")
    event("grandma1-death", 2415021, place="chicago")

    conn.execute("INSERT INTO family VALUES ('fam1', 'dad1', 'mom1')")
    conn.execute("INSERT INTO family VALUES ('fam2', 'granddad1', 'grandma1')")

    conn.execute("INSERT INTO place VALUES ('chicago', 'Chicago, Cook, Illinois, USA')")
    conn.execute("INSERT INTO place VALUES ('new-york', 'New York, New York, USA')")
    conn.execute("INSERT INTO place VALUES ('boston', 'Boston, Suffolk, Massachusetts, USA')")
    conn.execute(
        "INSERT INTO place VALUES ('philadelphia', 'Philadelphia, Philadelphia, Pennsylvania, USA')"
    )

    return conn


def run(db, namespace, expr, select=("handle",)):
    spec, where = compile_expr(namespace, expr)
    sql, params = compile_query(
        spec, Query(select=list(select), where=where), dialect=Dialect.SQLITE
    )
    return db.execute(sql, params).fetchall()


# --- basic comparisons -------------------------------------------------------


def test_flat_column_equality(db):
    # Default ordering is by handle, ascending. Both fam1's Smiths (dad1,
    # kid1, mom1) and fam2's grandfather (granddad1, also a Smith) match --
    # grandma1 (Mary Doyle) doesn't.
    assert run(db, "Person", "surname == 'Smith'") == [
        ("dad1",),
        ("granddad1",),
        ("kid1",),
        ("mom1",),
    ]


def test_class_constant(db):
    # gender == Person.MALE reads the real value off Person.MALE (1) --
    # gender == 1 compiles identically. Matches every male across both
    # generations: dad1, granddad1, kid1.
    assert run(db, "Person", "gender == Person.MALE") == [
        ("dad1",),
        ("granddad1",),
        ("kid1",),
    ]


def test_and_conjunction(db):
    assert run(db, "Person", "gender == Person.MALE and surname == 'Smith'") == [
        ("dad1",),
        ("granddad1",),
        ("kid1",),
    ]


def test_in_operator(db):
    result = run(db, "Person", "given_name in ['John', 'Jane']")
    assert result == [("dad1",), ("mom1",)]


def test_like_operator(db):
    result = run(db, "Person", "like(given_name, 'J%')")
    assert result == [("dad1",), ("mom1",)]


def test_contains_operator(db):
    # "'sub' in field" is a plain substring test -- 'Jan' matches "Jane"
    # (mom1) with no wildcard characters written out, unlike `like()`.
    result = run(db, "Person", "'Jan' in given_name")
    assert result == [("mom1",)]


def test_contains_operator_readme_example(db):
    # README-query-language.md's cookbook example, spelled with a different
    # substring than the doc above but matching the same person.
    result = run(db, "Person", "'an' in given_name")
    assert result == [("mom1",)]


# --- disjunction (or) ---------------------------------------------------------


def test_or_operator(db):
    # John (dad1) matches on given_name; Mary Doyle (grandma1) matches on
    # surname instead -- neither would match if this were "and".
    result = run(db, "Person", "given_name == 'John' or surname == 'Doyle'")
    assert result == [("dad1",), ("grandma1",)]


def test_and_binds_tighter_than_or_end_to_end(db):
    # "(male Smiths) or (anyone named Mary)" -- without the parentheses this
    # would read as "male, and (a Smith or named Mary)", a different (and
    # here empty for the male-only half) set of people. Matches: every male
    # Smith (dad1, granddad1, kid1) plus Mary Doyle (grandma1), who isn't a
    # Smith at all but is named Mary.
    result = run(
        db,
        "Person",
        "(gender == Person.MALE and surname == 'Smith') or given_name == 'Mary'",
    )
    assert result == [("dad1",), ("granddad1",), ("grandma1",), ("kid1",)]


# --- relationship traversal (Person -> Event) --------------------------------


def test_birth_date_sortval(db):
    # 1968 (sortval 2439857) or later.
    result = run(db, "Person", "birth.date.sortval >= 2439857")
    assert result == [("kid1",)]


def test_date_literal_helper(db):
    # Date('...') parses a human date string via Gramps' own date parser and
    # resolves to the same comparable .sortval integer.
    result = run(db, "Person", "birth.date.sortval >= Date('Jan 1, 1968')")
    assert result == [("kid1",)]


def test_still_living_excluded_from_ordering(db):
    # kid1 has no death event recorded (death_ref_index == -1) -- an ordering
    # comparison against a NULL never matches, so kid1 is correctly absent,
    # not wrongly included via some default value.
    result = run(db, "Person", "death.date.sortval < Date('Jan 1, 2100')")
    assert result == [
        ("dad1",),
        ("granddad1",),
        ("grandma1",),
        ("mom1",),
        ("other1",),
    ]


def test_death_description_like(db):
    # "death" reaches the whole death event, not just its date -- any of
    # the event's own fields are reachable the same way, e.g. a free-text
    # description of how someone died.
    result = run(db, "Person", "like(death.description, '%accident%')")
    assert result == [("dad1",)]


def test_death_description_contains(db):
    # The same query as test_death_description_like, spelled as a plain
    # substring test instead of a hand-written LIKE pattern.
    result = run(db, "Person", "'accident' in death.description")
    assert result == [("dad1",)]


# --- relationship traversal (Person -> Event -> Place) -----------------------


def test_two_hop_relationship_chain(db):
    result = run(db, "Person", "birth.place.title == 'Chicago, Cook, Illinois, USA'")
    assert result == [("dad1",), ("kid1",), ("mom1",)]


def test_born_and_died_in_same_place(db):
    # Combines both of Person's own relationships (birth and death) with
    # Event's one relationship (place), on both sides of a field-vs-field
    # comparison -- three relationship hops in a single expression. Jane
    # (mom1) was born and died in Chicago; everyone else either has a
    # different birth/death place or no recorded death at all.
    result = run(db, "Person", "birth.place.title == death.place.title")
    assert result == [("mom1",)]


# --- relationship traversal (Event -> Place) ---------------------------------


def test_event_place_directly(db):
    # "place" also works starting directly from an Event query, not just
    # reached via a Person's birth/death -- every event (of any person)
    # recorded as happening in Chicago.
    result = run(db, "Event", "place.title == 'Chicago, Cook, Illinois, USA'")
    assert result == [
        ("dad1-birth",),
        ("granddad1-death",),
        ("grandma1-death",),
        ("kid1-birth",),
        ("mom1-birth",),
        ("mom1-death",),
    ]


# --- relationship traversal (Family -> Person) -------------------------------


def test_father_surname(db):
    # Both fam1's father (John Smith) and fam2's father (William Smith,
    # the grandfather) match.
    result = run(db, "Family", "father.surname == 'Smith'")
    assert result == [("fam1",), ("fam2",)]


def test_mother_given_name(db):
    result = run(db, "Family", "mother.given_name == 'Mary'")
    assert result == [("fam2",)]


def test_field_vs_field_same_hop(db):
    # Both sides cross the same relationship root (father/mother), each to
    # its own Person row -- "same surname" (both Smiths, since it's the
    # same family in this fixture).
    result = run(db, "Family", "father.surname == mother.surname")
    assert result == [("fam1",)]


# --- relationship traversal (Family -> Person -> Event) ----------------------


def test_father_birth_before_1850(db):
    # A three-level path, chaining two relationships end to end: Family ->
    # father (-> Person) -> birth (-> Event) -> date.sortval.
    result = run(db, "Family", "father.birth.date.sortval < Date('Jan 1, 1850')")
    assert result == [("fam2",)]


# --- field-vs-field across a deeper relationship chain -----------------------


def test_mother_died_before_father(db):
    # The motivating example: mother (mom1) died 1970 (sortval 2440588),
    # father (dad1) died 1990 (sortval 2447893) -- mother's death is earlier.
    result = run(db, "Family", "mother.death.date.sortval < father.death.date.sortval")
    assert result == [("fam1",)]


def test_mother_died_before_father_reversed_is_empty(db):
    # Sanity check on the other direction: swapping the comparison must
    # exclude the same family, not match it too.
    result = run(db, "Family", "father.death.date.sortval < mother.death.date.sortval")
    assert result == []


def test_parents_died_in_same_place(db):
    # Family -> father/mother -> death -> place, on both sides at once: all
    # five relationship types this package knows about (birth, death,
    # father, mother, place) appear somewhere across this module's tests --
    # this one alone chains four of them (father, mother, death, place) in
    # a single field-vs-field comparison.
    result = run(db, "Family", "father.death.place.title == mother.death.place.title")
    assert result == [("fam2",)]


# --- constants on other types (Citation.CONF_HIGH) ---------------------------


def test_citation_confidence_constant():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE citation (handle TEXT, confidence INTEGER)")
    conn.execute("INSERT INTO citation VALUES ('c1', 3)")  # CONF_HIGH
    conn.execute("INSERT INTO citation VALUES ('c2', 1)")  # CONF_LOW

    result = run(conn, "Citation", "confidence >= Citation.CONF_HIGH")
    assert result == [("c1",)]


# --- indexing into a JSON list (multiple surnames) ---------------------------


def test_multiple_surnames():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE person (handle TEXT, given_name TEXT, json_data TEXT)"
    )

    def person(handle, given_name, surnames):
        conn.execute(
            "INSERT INTO person VALUES (?, ?, ?)",
            (
                handle,
                given_name,
                json.dumps(
                    {"primary_name": {"surname_list": [{"surname": s} for s in surnames]}}
                ),
            ),
        )

    # Maria has both her maiden name and her married name recorded; John has
    # just the one surname -- surname_list[1] only exists for Maria.
    person("maria1", "Maria", ["Garcia", "Lopez"])
    person("john1", "John", ["Smith"])

    result = run(conn, "Person", "primary_name.surname_list[1].surname != None")
    assert result == [("maria1",)]
