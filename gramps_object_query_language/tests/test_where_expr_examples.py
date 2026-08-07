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
import re
import sqlite3

import pytest

from gramps_object_query_language.query import Dialect, Query, compile_query
from gramps_object_query_language.query_lang import compile_expr


def _regexp(expr, value):
    """Mirrors gramps core's `dbapi/sqlite.py` `regexp()` UDF exactly -- a
    bare stdlib `sqlite3` connection (what this fixture otherwise is) has no
    REGEXP operator of its own, so `db` below registers this the same way
    every real Gramps SQLite connection already does, to prove `regex(...)`
    examples run against the same SQL a real deployment would see.
    """
    return re.search(expr, value, re.MULTILINE) is not None


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
    conn.create_function("regexp", 2, _regexp)
    conn.execute(
        "CREATE TABLE person (handle TEXT, gender INTEGER, given_name TEXT, "
        "surname TEXT, birth_ref_index INTEGER, death_ref_index INTEGER, "
        "json_data TEXT)"
    )
    conn.execute(
        "CREATE TABLE family (handle TEXT, father_handle TEXT, mother_handle TEXT, "
        "json_data TEXT)"
    )
    conn.execute(
        "CREATE TABLE event (handle TEXT, place TEXT, description TEXT, json_data TEXT)"
    )
    conn.execute("CREATE TABLE place (handle TEXT, title TEXT)")
    conn.execute("CREATE TABLE note (handle TEXT, format INTEGER)")

    def person(handle, gender, given_name, surname, birth_ref=0, death_ref=1, note_list=()):
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
                        ],
                        "note_list": list(note_list),
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
    # in Boston (sortval 2447893) -- an accident, per the death record. Has
    # one note attached, used by the exists(notes) examples below.
    conn.execute("INSERT INTO note VALUES ('dad1-note-1', 0)")
    person("dad1", gender=1, given_name="John", surname="Smith", note_list=["dad1-note-1"])
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

    # fam1 has one recorded child (kid1, Robert) -- used by the exists(children)
    # examples below. fam2 has no json_data / recorded children at all, used
    # to prove exists(children) correctly excludes it rather than erroring.
    conn.execute(
        "INSERT INTO family VALUES ('fam1', 'dad1', 'mom1', ?)",
        (json.dumps({"child_ref_list": [{"ref": "kid1"}]}),),
    )
    conn.execute("INSERT INTO family VALUES ('fam2', 'granddad1', 'grandma1', NULL)")

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


def test_regex_operator(db):
    # Unanchored, case-sensitive regex search -- "^J" matches both "John"
    # and "Jane" the same as like(given_name, 'J%') above, but a regex can
    # express things a LIKE pattern can't, e.g. "either John or Jane".
    result = run(db, "Person", "regex(given_name, '^(John|Jane)$')")
    assert result == [("dad1",), ("mom1",)]


def test_regex_operator_readme_example(db):
    # README-query-language.md's cookbook example, spelled without the
    # anchors used above but matching the same two people.
    result = run(db, "Person", "regex(given_name, 'John|Jane')")
    assert result == [("dad1",), ("mom1",)]


def test_regex_character_class_readme_example(db):
    # README-query-language.md's cookbook example -- "[SD]" (either S or D)
    # matches every Smith (dad1, mom1, kid1, granddad1) and Doyle (grandma1),
    # not Jones (other1) -- a character class LIKE has no equivalent for.
    result = run(db, "Person", "regex(surname, '^[SD]')")
    assert result == [("dad1",), ("granddad1",), ("grandma1",), ("kid1",), ("mom1",)]


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


def test_operand_order_value_on_left(db):
    # "value OP field" -- the literal written on the left of the comparison
    # instead of the right. Compiles and executes identically to writing it
    # the more usual way around (the operator flips: "Date(...) < field"
    # becomes "field > Date(...)"). Matches everyone born after 1900:
    # dad1 (1940), mom1 (1945), kid1 (1968) -- not granddad1/grandma1 (1840s)
    # or other1 (1900, not strictly after).
    forward = run(db, "Person", "birth.date.sortval > Date('Jan 1, 1900')")
    reversed_ = run(db, "Person", "Date('Jan 1, 1900') < birth.date.sortval")
    assert reversed_ == forward
    assert forward == [("dad1",), ("kid1",), ("mom1",)]


def test_operand_order_reversed_constant(db):
    # "Person.MALE == gender" -- the constant on the left, same as
    # "gender == Person.MALE" reversed. eq/ne are symmetric, so no operator
    # flip is needed here, just the operand classification.
    assert run(db, "Person", "Person.MALE == gender") == run(
        db, "Person", "gender == Person.MALE"
    )


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


# --- negation (not) ------------------------------------------------------------


def test_not_operator(db):
    # Everyone except the Smiths -- Mary Doyle (grandma1) and Alice Jones
    # (other1).
    result = run(db, "Person", "not (surname == 'Smith')")
    assert result == [("grandma1",), ("other1",)]


def test_not_wraps_and_readme_example(db):
    # README-query-language.md's cookbook example: everyone who *isn't* a
    # male Smith -- excludes dad1, granddad1, and kid1 (the male Smiths),
    # keeps mom1 (a female Smith), grandma1 (not a Smith), and other1 (not
    # a Smith).
    result = run(
        db, "Person", "not (gender == Person.MALE and surname == 'Smith')"
    )
    assert result == [("grandma1",), ("mom1",), ("other1",)]


def test_not_and_or_combined(db):
    # "not male, or a Doyle" -- excludes every male Smith/grandfather,
    # keeps every woman plus anyone (of any gender) named Doyle. Matches:
    # mom1 (Jane, female), grandma1 (Mary, female and a Doyle), other1
    # (Alice, female).
    result = run(db, "Person", "not (gender == Person.MALE) or surname == 'Doyle'")
    assert result == [("grandma1",), ("mom1",), ("other1",)]


def test_not_of_ordering_comparison_excludes_missing_value_too(db):
    # kid1 has no recorded death (still living) -- both "died before 2100"
    # and its negation must leave kid1 out, the same way SQL's three-valued
    # logic treats a missing value as UNKNOWN under NOT, not as a match by
    # default. (See evaluate_where's matching behavior in test_evaluator.py
    # -- this is the SQL side of the same guarantee.)
    positive = run(db, "Person", "death.date.sortval < Date('Jan 1, 2100')")
    negated = run(db, "Person", "not (death.date.sortval < Date('Jan 1, 2100'))")
    assert ("kid1",) not in positive
    assert ("kid1",) not in negated


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


def test_death_description_regex_alternation_readme_example(db):
    # README-query-language.md's cookbook example -- "|" (either of these)
    # matches dad1's "Died in a car accident." and other1's "Cause
    # unknown.", something neither like(...) nor 'text' in field can express
    # in a single condition (each only ever tests for one substring).
    result = run(db, "Person", "regex(death.description, 'accident|unknown')")
    assert result == [("dad1",), ("other1",)]


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


def test_contains_field_vs_field_readme_example(db):
    # "does the mother's surname contain the father's" -- fam1's parents
    # (dad1, mom1) are both Smiths, so "Smith" is (trivially) a substring
    # of "Smith". fam2's parents (granddad1 Smith, grandma1 Doyle) aren't.
    result = run(db, "Family", "father.surname in mother.surname")
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


# --- one-to-many relationships (exists) ---------------------------------------


def test_exists_children_with_condition(db):
    # fam1's one recorded child is kid1 (Robert Smith) -- fam2 has none
    # recorded at all.
    result = run(db, "Family", "exists(children, given_name == 'Steve')")
    assert result == []
    result = run(db, "Family", "exists(children, given_name == 'Robert')")
    assert result == [("fam1",)]


def test_not_exists_children_with_condition(db):
    result = run(db, "Family", "not exists(children, given_name == 'Steve')")
    assert result == [("fam1",), ("fam2",)]


def test_exists_children_no_condition(db):
    # "any recorded child at all" -- matches fam1 (kid1), not fam2 (no
    # json_data / no children recorded at all -- not an error, just no rows
    # for json_each to iterate).
    result = run(db, "Family", "exists(children)")
    assert result == [("fam1",)]


def test_not_exists_children_no_condition(db):
    result = run(db, "Family", "not exists(children)")
    assert result == [("fam2",)]


def test_exists_notes(db):
    # dad1 has one note attached; everyone else has an empty note_list.
    result = run(db, "Person", "exists(notes)")
    assert result == [("dad1",)]


def test_not_exists_notes(db):
    result = run(db, "Person", "not exists(notes)")
    assert result == [
        ("granddad1",),
        ("grandma1",),
        ("kid1",),
        ("mom1",),
        ("other1",),
    ]


def test_count_children(db):
    # fam1 has exactly one recorded child (kid1); fam2 has none at all.
    result = run(db, "Family", "count(children) > 0")
    assert result == [("fam1",)]
    result = run(db, "Family", "count(children) == 0")
    assert result == [("fam2",)]


def test_count_children_with_condition(db):
    result = run(db, "Family", "count(children, given_name == 'Robert') == 1")
    assert result == [("fam1",)]
    result = run(db, "Family", "count(children, given_name == 'Steve') == 1")
    assert result == []


# --- comprehension sugar for exists(...)/count(...) --------------------------


def test_any_comprehension_matches_exists_children_with_condition(db):
    # Same fixture, same result as test_exists_children_with_condition --
    # any(...) is pure sugar for exists(...), so it has to answer identically.
    result = run(db, "Family", "any(c.given_name == 'Steve' for c in children)")
    assert result == []
    result = run(db, "Family", "any(c.given_name == 'Robert' for c in children)")
    assert result == [("fam1",)]


def test_len_listcomp_matches_count_children_with_condition(db):
    # Same fixture, same result as test_count_children_with_condition.
    result = run(
        db, "Family", "len([c for c in children if c.given_name == 'Robert']) == 1"
    )
    assert result == [("fam1",)]
    result = run(
        db, "Family", "len([c for c in children if c.given_name == 'Steve']) == 1"
    )
    assert result == []


def test_count_children_more_than_two_readme_example():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE family (handle TEXT, json_data TEXT)")
    conn.execute("CREATE TABLE person (handle TEXT, gender INTEGER)")
    conn.execute("INSERT INTO person VALUES ('steve', 1)")
    conn.execute("INSERT INTO person VALUES ('anna', 0)")
    conn.execute("INSERT INTO person VALUES ('bob', 1)")
    conn.execute(
        "INSERT INTO family VALUES ('big-family', ?)",
        (json.dumps({"child_ref_list": [{"ref": "steve"}, {"ref": "anna"}, {"ref": "bob"}]}),),
    )
    conn.execute(
        "INSERT INTO family VALUES ('small-family', ?)",
        (json.dumps({"child_ref_list": [{"ref": "anna"}]}),),
    )

    result = run(conn, "Family", "count(children) > 2")
    assert result == [("big-family",)]


def test_count_children_gender_condition_readme_example():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE family (handle TEXT, json_data TEXT)")
    conn.execute("CREATE TABLE person (handle TEXT, gender INTEGER)")
    conn.execute("INSERT INTO person VALUES ('steve', 1)")
    conn.execute("INSERT INTO person VALUES ('bob', 1)")
    conn.execute("INSERT INTO person VALUES ('anna', 0)")
    conn.execute(
        "INSERT INTO family VALUES ('two-sons', ?)",
        (json.dumps({"child_ref_list": [{"ref": "steve"}, {"ref": "bob"}]}),),
    )
    conn.execute(
        "INSERT INTO family VALUES ('one-son-one-daughter', ?)",
        (json.dumps({"child_ref_list": [{"ref": "steve"}, {"ref": "anna"}]}),),
    )

    result = run(conn, "Family", "count(children, gender == Person.MALE) > 1")
    assert result == [("two-sons",)]


# --- Collections registered on other types, and self-reference ---------------


def test_citation_source_relationship_doc_example():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE citation (handle TEXT, source_handle TEXT)")
    conn.execute("CREATE TABLE source (handle TEXT, title TEXT)")
    conn.execute("INSERT INTO source VALUES ('src1', 'Census Records')")
    conn.execute("INSERT INTO source VALUES ('src2', 'Parish Register')")
    conn.execute("INSERT INTO citation VALUES ('c1', 'src1')")
    conn.execute("INSERT INTO citation VALUES ('c2', 'src2')")

    result = run(conn, "Citation", "source.title == 'Census Records'")
    assert result == [("c1",)]


# --- relationship traversal (Place -> Place, self-reference) -----------------


def test_place_enclosed_by_relationship_doc_example():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE place (handle TEXT, title TEXT, enclosed_by TEXT)")
    conn.execute("INSERT INTO place VALUES ('city', 'Chicago', 'county')")
    conn.execute("INSERT INTO place VALUES ('county', 'Cook County', 'state')")
    conn.execute("INSERT INTO place VALUES ('state', 'Illinois', NULL)")

    result = run(conn, "Place", "enclosed_by.title == 'Cook County'")
    assert result == [("city",)]


def test_place_enclosed_by_chained_self_reference_doc_example():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE place (handle TEXT, title TEXT, enclosed_by TEXT)")
    conn.execute("INSERT INTO place VALUES ('city', 'Chicago', 'county')")
    conn.execute("INSERT INTO place VALUES ('county', 'Cook County', 'state')")
    conn.execute("INSERT INTO place VALUES ('state', 'Illinois', NULL)")

    result = run(conn, "Place", "enclosed_by.enclosed_by.title == 'Illinois'")
    assert result == [("city",)]


def test_exists_citations_confidence_doc_example():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE person (handle TEXT, json_data TEXT)")
    conn.execute("CREATE TABLE citation (handle TEXT, confidence INTEGER)")
    conn.execute("INSERT INTO citation VALUES ('c-high', 3)")  # CONF_HIGH
    conn.execute("INSERT INTO citation VALUES ('c-low', 1)")  # CONF_LOW
    conn.execute(
        "INSERT INTO person VALUES ('well-sourced', ?)",
        (json.dumps({"citation_list": ["c-high"]}),),
    )
    conn.execute(
        "INSERT INTO person VALUES ('poorly-sourced', ?)",
        (json.dumps({"citation_list": ["c-low"]}),),
    )

    result = run(conn, "Person", "exists(citations, confidence >= Citation.CONF_HIGH)")
    assert result == [("well-sourced",)]


def test_exists_associations_self_reference_doc_example():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE person (handle TEXT, given_name TEXT, json_data TEXT)")
    conn.execute(
        "INSERT INTO person VALUES ('alice', 'Alice', ?)",
        (json.dumps({"person_ref_list": [{"ref": "bob"}]}),),
    )
    conn.execute(
        "INSERT INTO person VALUES ('bob', 'Bob', ?)", (json.dumps({"person_ref_list": []}),)
    )

    result = run(conn, "Person", "exists(associations, given_name == 'Bob')")
    assert result == [("alice",)]


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


# --- Date modifier/quality/dateval, via Date.MOD_*/QUAL_* constants ----------


def test_date_modifier_constant():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE person (handle TEXT, birth_ref_index INTEGER, json_data TEXT)"
    )
    conn.execute("CREATE TABLE event (handle TEXT, json_data TEXT)")

    def person(handle):
        conn.execute(
            "INSERT INTO person VALUES (?, ?, ?)",
            (handle, 0, json.dumps({"event_ref_list": [{"ref": f"{handle}-birth"}]})),
        )

    def event(handle, modifier, quality=0, dateval=None):
        conn.execute(
            "INSERT INTO event VALUES (?, ?)",
            (
                handle,
                json.dumps(
                    {
                        "date": {
                            "modifier": modifier,
                            "quality": quality,
                            "dateval": dateval or [1, 1, 1968, False],
                        }
                    }
                ),
            ),
        )

    # p1's birth is recorded as "about 1968" (MOD_ABOUT); p2's is exact
    # (MOD_NONE) -- same calendar position, different modifier.
    person("p1")
    event("p1-birth", modifier=3)  # Date.MOD_ABOUT
    person("p2")
    event("p2-birth", modifier=0)  # Date.MOD_NONE

    result = run(conn, "Person", "birth.date.modifier == Date.MOD_ABOUT")
    assert result == [("p1",)]


def test_date_quality_constant():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE person (handle TEXT, birth_ref_index INTEGER, json_data TEXT)"
    )
    conn.execute("CREATE TABLE event (handle TEXT, json_data TEXT)")

    def person(handle):
        conn.execute(
            "INSERT INTO person VALUES (?, ?, ?)",
            (handle, 0, json.dumps({"event_ref_list": [{"ref": f"{handle}-birth"}]})),
        )

    def event(handle, quality):
        conn.execute(
            "INSERT INTO event VALUES (?, ?)",
            (handle, json.dumps({"date": {"quality": quality}})),
        )

    # p1's birth date is marked "estimated"; p2's has no quality flag.
    person("p1")
    event("p1-birth", quality=1)  # Date.QUAL_ESTIMATED
    person("p2")
    event("p2-birth", quality=0)  # Date.QUAL_NONE

    result = run(conn, "Person", "birth.date.quality != Date.QUAL_NONE")
    assert result == [("p1",)]


def test_date_span_dateval_index():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE person (handle TEXT, birth_ref_index INTEGER, json_data TEXT)"
    )
    conn.execute("CREATE TABLE event (handle TEXT, json_data TEXT)")

    def person(handle):
        conn.execute(
            "INSERT INTO person VALUES (?, ?, ?)",
            (handle, 0, json.dumps({"event_ref_list": [{"ref": f"{handle}-birth"}]})),
        )

    def event(handle, dateval):
        conn.execute(
            "INSERT INTO event VALUES (?, ?)",
            (handle, json.dumps({"date": {"dateval": dateval}})),
        )

    # p1's birth is a span "1968 to 1970" (8-element dateval, end year at
    # index 6); p2's is a single exact date (4-element dateval, no index 6
    # at all) -- sortval alone can't tell a span's end from an exact date's
    # start, but dateval[6] can.
    person("p1")
    event("p1-birth", dateval=[1, 6, 1968, False, 31, 12, 1970, False])  # MOD_SPAN
    person("p2")
    event("p2-birth", dateval=[1, 1, 1968, False])

    result = run(conn, "Person", "birth.date.dateval[6] == 1970")
    assert result == [("p1",)]


# --- GrampsType constants (EventType, FamilyRelType, NameType) ---------------


def test_event_type_constant():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE event (handle TEXT, json_data TEXT)")
    conn.execute(
        "INSERT INTO event VALUES ('e1', ?)",
        (json.dumps({"type": {"_class": "EventType", "value": 12, "string": ""}}),),
    )  # EventType.BIRTH
    conn.execute(
        "INSERT INTO event VALUES ('e2', ?)",
        (json.dumps({"type": {"_class": "EventType", "value": 13, "string": ""}}),),
    )  # EventType.DEATH

    result = run(conn, "Event", "type.value == EventType.BIRTH")
    assert result == [("e1",)]


def test_family_rel_type_constant():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE family (handle TEXT, father_handle TEXT, mother_handle TEXT, "
        "json_data TEXT)"
    )
    conn.execute(
        "INSERT INTO family VALUES ('fam1', 'dad1', 'mom1', ?)",
        (json.dumps({"type": {"_class": "FamilyRelType", "value": 0, "string": ""}}),),
    )  # FamilyRelType.MARRIED
    conn.execute(
        "INSERT INTO family VALUES ('fam2', 'dad2', 'mom2', ?)",
        (json.dumps({"type": {"_class": "FamilyRelType", "value": 1, "string": ""}}),),
    )  # FamilyRelType.UNMARRIED

    result = run(conn, "Family", "type.value == FamilyRelType.MARRIED")
    assert result == [("fam1",)]


def test_name_type_constant():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE person (handle TEXT, json_data TEXT)")
    conn.execute(
        "INSERT INTO person VALUES ('p1', ?)",
        (
            json.dumps(
                {"primary_name": {"type": {"_class": "NameType", "value": 2, "string": ""}}}
            ),
        ),
    )  # NameType.BIRTH
    conn.execute(
        "INSERT INTO person VALUES ('p2', ?)",
        (
            json.dumps(
                {"primary_name": {"type": {"_class": "NameType", "value": 1, "string": ""}}}
            ),
        ),
    )  # NameType.AKA

    result = run(conn, "Person", "primary_name.type.value == NameType.BIRTH")
    assert result == [("p1",)]


# --- is / is not / not in, and operand ordering (README cookbook additions) ---


def test_is_none_readme_example(db):
    # Same people as "everyone who has died" (test_not_operator's sibling),
    # complemented: no recorded death date at all -- only kid1, still living.
    assert run(db, "Person", "death.date.sortval is None") == [("kid1",)]


def test_not_in_list_readme_example(db):
    # Excludes every Smith (dad1, mom1, kid1, granddad1) and the one Jones
    # (other1), leaving only Mary Doyle (grandma1).
    result = run(db, "Person", "surname not in ['Smith', 'Jones']")
    assert result == [("grandma1",)]


def test_operand_order_date_on_left_readme_example(db):
    # Born before 1900: granddad1 (1845) and grandma1 (1848) -- everyone
    # else (dad1, mom1, kid1) was born after, and other1 was born exactly in
    # 1900, not strictly before.
    forward = run(db, "Person", "birth.date.sortval < Date('Jan 1, 1900')")
    reversed_ = run(db, "Person", "Date('Jan 1, 1900') > birth.date.sortval")
    assert reversed_ == forward
    assert forward == [("granddad1",), ("grandma1",)]


def test_chained_comparison_readme_example(db):
    # Born strictly between 1900 and 1950: dad1 (1940) and mom1 (1945) --
    # granddad1/grandma1 (1840s) are too early, kid1 (1968) too late, and
    # other1 (born exactly in 1900) doesn't satisfy the strict left side.
    chained = run(
        db, "Person", "Date('Jan 1, 1900') < birth.date.sortval < Date('Jan 1, 1950')"
    )
    anded = run(
        db,
        "Person",
        "birth.date.sortval > Date('Jan 1, 1900') and "
        "birth.date.sortval < Date('Jan 1, 1950')",
    )
    assert chained == anded == [("dad1",), ("mom1",)]
