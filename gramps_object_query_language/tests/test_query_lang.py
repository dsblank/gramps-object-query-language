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

"""Tests for the "almost Python" expression parser (`gramps_webapi.api.query_lang`)."""

import pytest

from gramps_object_query_language.query_lang import (
    QueryLangError,
    compile_expr,
    compile_expr_for_spec,
    parse_expr,
    resolve_namespace,
)
from gramps_object_query_language.query import (
    PERSON,
    FAMILY,
    And,
    Eq,
    Gte,
    JsonPath,
    Lt,
    Not,
    Or,
    RelatedObject,
)


# --- namespace resolution -----------------------------------------------------


def test_resolve_namespace_lowercase():
    assert resolve_namespace("person") is PERSON


def test_resolve_namespace_class_name_casing():
    assert resolve_namespace("Person") is PERSON
    assert resolve_namespace("Family") is FAMILY


def test_resolve_namespace_unknown_raises():
    with pytest.raises(QueryLangError):
        resolve_namespace("bogus")


def test_resolve_namespace_no_single_letter_alias():
    with pytest.raises(QueryLangError):
        resolve_namespace("P")


# --- plain-column vs JsonPath resolution --------------------------------------


def test_single_segment_matching_flat_column_becomes_plain_string():
    assert parse_expr("person", "gender == 1") == [
        {"column": "gender", "op": "eq", "value": 1}
    ]


def test_multi_segment_path_becomes_json_path():
    result = parse_expr("person", "primary_name.first_name == 'John'")
    assert result == [
        {
            "column": {"json_path": ["primary_name", "first_name"]},
            "op": "eq",
            "value": "John",
        }
    ]


def test_single_segment_not_matching_flat_column_becomes_json_path():
    # "birth_year" isn't a real flat column on PERSON -- falls back to
    # json_path even though it's a single segment.
    result = parse_expr("person", "birth_year == 1900")
    assert result == [
        {"column": {"json_path": ["birth_year"]}, "op": "eq", "value": 1900}
    ]


def test_integer_subscript_becomes_int_segment():
    result = parse_expr("person", "primary_name.surname_list[0].surname == 'Smith'")
    assert result == [
        {
            "column": {
                "json_path": ["primary_name", "surname_list", 0, "surname"]
            },
            "op": "eq",
            "value": "Smith",
        }
    ]


# --- comparison operators ------------------------------------------------------


@pytest.mark.parametrize(
    "op_src,op_json",
    [
        ("==", "eq"),
        ("!=", "ne"),
        ("<", "lt"),
        ("<=", "lte"),
        (">", "gt"),
        (">=", "gte"),
    ],
)
def test_all_comparison_operators(op_src, op_json):
    result = parse_expr("person", f"gender {op_src} 1")
    assert result == [{"column": "gender", "op": op_json, "value": 1}]


def test_in_operator():
    result = parse_expr("person", "gender in [1, 2]")
    assert result == [{"column": "gender", "op": "in", "value": [1, 2]}]


def test_in_operator_requires_nonempty_list():
    with pytest.raises(QueryLangError):
        parse_expr("person", "gender in []")


def test_in_operator_rejects_non_list_rhs():
    with pytest.raises(QueryLangError):
        parse_expr("person", "gender in (1, 2)")  # tuple, not list


def test_contains_operator():
    result = parse_expr("person", "'Jan' in given_name")
    assert result == [{"column": "given_name", "op": "contains", "value": "Jan"}]


def test_contains_operator_on_json_path():
    result = parse_expr("person", "'accident' in death.description")
    assert result == [
        {
            "column": {"json_path": ["death", "description"]},
            "op": "contains",
            "value": "accident",
        }
    ]


def test_contains_operator_rejects_non_string_literal_lhs():
    with pytest.raises(QueryLangError):
        parse_expr("person", "5 in given_name")


def test_contains_operator_rejects_field_on_both_sides():
    # Field-vs-field isn't supported for "contains" -- only a literal
    # substring on the left.
    with pytest.raises(QueryLangError):
        parse_expr("person", "given_name in surname")


def test_like_call():
    result = parse_expr("person", "like(primary_name.first_name, 'Jo%')")
    assert result == [
        {
            "column": {"json_path": ["primary_name", "first_name"]},
            "op": "like",
            "value": "Jo%",
        }
    ]


def test_like_call_wrong_arity_rejected():
    with pytest.raises(QueryLangError):
        parse_expr("person", "like(gender)")


def test_like_call_non_string_pattern_rejected():
    with pytest.raises(QueryLangError):
        parse_expr("person", "like(gender, 5)")


# --- literals --------------------------------------------------------------------


def test_string_int_float_bool_literals():
    assert parse_expr("person", "gender == True") == [
        {"column": "gender", "op": "eq", "value": True}
    ]
    assert parse_expr("person", "gender == 1.5") == [
        {"column": "gender", "op": "eq", "value": 1.5}
    ]
    assert parse_expr("person", "gender == None") == [
        {"column": "gender", "op": "eq", "value": None}
    ]


def test_negative_number_literal():
    result = parse_expr("family", "some_field == -5")
    assert result[0]["value"] == -5


def test_unary_minus_on_non_numeric_rejected():
    with pytest.raises(QueryLangError):
        parse_expr("person", "gender == -'x'")


# --- conjunction (and) -----------------------------------------------------------


def test_and_conjunction_produces_multiple_conditions():
    result = parse_expr("person", "gender == 1 and primary_name.first_name == 'John'")
    assert result == [
        {"column": "gender", "op": "eq", "value": 1},
        {
            "column": {"json_path": ["primary_name", "first_name"]},
            "op": "eq",
            "value": "John",
        },
    ]


def test_and_conjunction_of_three():
    result = parse_expr("person", "gender == 1 and gender != 2 and gender < 3")
    assert len(result) == 3


# --- disjunction (or) --------------------------------------------------------


def test_or_produces_or_node():
    result = parse_expr("person", "gender == 1 or gender == 2")
    assert result == [
        {
            "or": [
                {"column": "gender", "op": "eq", "value": 1},
                {"column": "gender", "op": "eq", "value": 2},
            ]
        }
    ]


def test_or_of_three():
    # A single `BoolOp` node holds all three values, not nested pairs --
    # mirrors how `and` already collapses `a and b and c` into one node.
    result = parse_expr("person", "gender == 1 or gender == 2 or gender == 3")
    assert result == [
        {
            "or": [
                {"column": "gender", "op": "eq", "value": 1},
                {"column": "gender", "op": "eq", "value": 2},
                {"column": "gender", "op": "eq", "value": 3},
            ]
        }
    ]


def test_and_binds_tighter_than_or():
    # Python's own precedence, resolved by ast.parse before this module ever
    # sees the tree: "a and b or c" is "(a and b) or c", not "a and (b or c)".
    result = parse_expr(
        "person", "gender == 1 and surname == 'Smith' or given_name == 'John'"
    )
    assert result == [
        {
            "or": [
                {
                    "and": [
                        {"column": "gender", "op": "eq", "value": 1},
                        {"column": "surname", "op": "eq", "value": "Smith"},
                    ]
                },
                {"column": "given_name", "op": "eq", "value": "John"},
            ]
        }
    ]


def test_parenthesized_or_inside_and_stays_flat_and_list():
    # "(a or b) and c" -- the top-level "and" is still unwrapped into a flat
    # list (implicitly AND'd, exactly like before "or" support existed),
    # with the "or" node as one of its elements rather than changing the
    # top-level shape.
    result = parse_expr(
        "person", "(gender == 1 or gender == 2) and surname == 'Smith'"
    )
    assert result == [
        {
            "or": [
                {"column": "gender", "op": "eq", "value": 1},
                {"column": "gender", "op": "eq", "value": 2},
            ]
        },
        {"column": "surname", "op": "eq", "value": "Smith"},
    ]


def test_or_wraps_like_and_contains():
    # "or" composes with the other leaf shapes (like(...), the substring
    # form of "in"), not just plain comparisons.
    result = parse_expr("person", "like(given_name, 'J%') or 'an' in given_name")
    assert result == [
        {
            "or": [
                {"column": "given_name", "op": "like", "value": "J%"},
                {"column": "given_name", "op": "contains", "value": "an"},
            ]
        }
    ]


# --- negation (not) -----------------------------------------------------------


def test_not_produces_not_node():
    result = parse_expr("person", "not (gender == 1)")
    assert result == [{"not": {"column": "gender", "op": "eq", "value": 1}}]


def test_not_binds_without_parens_same_as_with():
    # "not" is a unary operator applying to the single comparison right
    # after it, same as real Python -- the parens above are optional.
    assert parse_expr("person", "not gender == 1") == parse_expr(
        "person", "not (gender == 1)"
    )


def test_not_binds_tighter_than_and():
    # "not a and b" is "(not a) and b", not "not (a and b)" -- "not" binds
    # tighter than "and", same as real Python.
    result = parse_expr("person", "not gender == 1 and surname == 'Smith'")
    assert result == [
        {"not": {"column": "gender", "op": "eq", "value": 1}},
        {"column": "surname", "op": "eq", "value": "Smith"},
    ]


def test_not_wraps_parenthesized_and():
    result = parse_expr("person", "not (gender == 1 and surname == 'Smith')")
    assert result == [
        {
            "not": {
                "and": [
                    {"column": "gender", "op": "eq", "value": 1},
                    {"column": "surname", "op": "eq", "value": "Smith"},
                ]
            }
        }
    ]


def test_not_composes_with_or():
    result = parse_expr("person", "not gender == 1 or surname == 'Smith'")
    assert result == [
        {
            "or": [
                {"not": {"column": "gender", "op": "eq", "value": 1}},
                {"column": "surname", "op": "eq", "value": "Smith"},
            ]
        }
    ]


def test_double_negation():
    result = parse_expr("person", "not not gender == 1")
    assert result == [{"not": {"not": {"column": "gender", "op": "eq", "value": 1}}}]


def test_not_wraps_like_and_contains():
    result = parse_expr("person", "not like(given_name, 'J%')")
    assert result == [{"not": {"column": "given_name", "op": "like", "value": "J%"}}]


# --- explicitly rejected: things with no wire-format equivalent yet -------------


def test_not_in_rejected():
    with pytest.raises(QueryLangError):
        parse_expr("person", "gender not in [1, 2]")


def test_is_rejected():
    with pytest.raises(QueryLangError):
        parse_expr("person", "gender is None")


def test_chained_comparison_rejected():
    with pytest.raises(QueryLangError):
        parse_expr("person", "1 < gender < 3")


def test_bare_name_rejected():
    with pytest.raises(QueryLangError):
        parse_expr("person", "gender")


def test_syntax_error_rejected():
    with pytest.raises(QueryLangError):
        parse_expr("person", "gender == 1 +")


# --- safety: arbitrary code must never be reachable ------------------------------


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('ls')",
        "foo(gender, 1)",  # arbitrary function call, not the whitelisted `like`
        "lambda x: x",
        "[x for x in range(10)]",
        "{x: x for x in range(10)}",
        "(yield 1)",
        "gender if True else 1",
        "f'{gender}'",
    ],
)
def test_unsupported_node_shapes_rejected(expr):
    with pytest.raises(QueryLangError):
        parse_expr("person", expr)


def test_subscript_with_non_constant_index_rejected():
    with pytest.raises(QueryLangError):
        parse_expr("person", "primary_name.surname_list[i].surname == 'Smith'")


def test_subscript_with_bool_index_rejected():
    # bool is an int subclass -- explicitly excluded, matching JsonPath's own
    # segment validation in query.py.
    with pytest.raises(QueryLangError):
        parse_expr("person", "primary_name.surname_list[True].surname == 'Smith'")


# --- ClassName.CONST value constants --------------------------------------------


def test_person_gender_constants():
    from gramps.gen.lib import Person

    assert parse_expr("person", "gender == Person.MALE") == [
        {"column": "gender", "op": "eq", "value": Person.MALE}
    ]
    assert parse_expr("person", "gender == Person.FEMALE") == [
        {"column": "gender", "op": "eq", "value": Person.FEMALE}
    ]
    assert parse_expr("person", "gender == Person.UNKNOWN") == [
        {"column": "gender", "op": "eq", "value": Person.UNKNOWN}
    ]
    assert parse_expr("person", "gender == Person.OTHER") == [
        {"column": "gender", "op": "eq", "value": Person.OTHER}
    ]


def test_citation_confidence_constants():
    from gramps.gen.lib import Citation

    result = parse_expr("citation", "confidence >= Citation.CONF_HIGH")
    assert result == [{"column": "confidence", "op": "gte", "value": Citation.CONF_HIGH}]


def test_note_format_constants():
    from gramps.gen.lib import Note

    result = parse_expr("note", "format == Note.FLOWED")
    assert result == [{"column": "format", "op": "eq", "value": Note.FLOWED}]


def test_date_modifier_and_quality_constants():
    # `Date` isn't a flat-column field on anything -- `birth.date.modifier`
    # is a `json_path`, so this also proves constants work against
    # `json_path` fields, not just real SQL columns.
    from gramps.gen.lib import Date

    result = parse_expr("person", "birth.date.modifier == Date.MOD_ABOUT")
    assert result == [
        {
            "column": {"json_path": ["birth", "date", "modifier"]},
            "op": "eq",
            "value": Date.MOD_ABOUT,
        }
    ]
    result = parse_expr("person", "birth.date.quality == Date.QUAL_ESTIMATED")
    assert result == [
        {
            "column": {"json_path": ["birth", "date", "quality"]},
            "op": "eq",
            "value": Date.QUAL_ESTIMATED,
        }
    ]


def test_grampstype_constants():
    # One example each for a few of the `GrampsType` subclasses -- these
    # attach to fields that are always nested `json_data` (Event.type,
    # Family's rel_type, a name's type), never a flat column, e.g.
    # `Event.type` is stored as `{"_class": "EventType", "value": 12,
    # "string": ""}`, so the comparison is against `type.value`.
    from gramps.gen.lib import EventType, FamilyRelType, NameType

    assert parse_expr("event", "type.value == EventType.BIRTH") == [
        {"column": {"json_path": ["type", "value"]}, "op": "eq", "value": EventType.BIRTH}
    ]
    assert parse_expr("family", "type.value == FamilyRelType.MARRIED") == [
        {
            "column": {"json_path": ["type", "value"]},
            "op": "eq",
            "value": FamilyRelType.MARRIED,
        }
    ]
    assert parse_expr("person", "primary_name.type.value == NameType.BIRTH") == [
        {
            "column": {"json_path": ["primary_name", "type", "value"]},
            "op": "eq",
            "value": NameType.BIRTH,
        }
    ]


def test_constant_inside_in_list():
    from gramps.gen.lib import Person

    result = parse_expr("person", "gender in [Person.MALE, Person.OTHER]")
    assert result == [
        {"column": "gender", "op": "in", "value": [Person.MALE, Person.OTHER]}
    ]


def test_unknown_constant_namespace_treated_as_path():
    # `Foo.BAR`'s base name isn't a registered constant class
    # (`_CONSTANT_CLASSES`), so `_is_path_node` can't tell it apart from a
    # genuine (if made-up) relationship-style path like `father.surname` --
    # same deferred-validation philosophy as any other unrecognized path
    # segment (see test_bare_relationship_name_parses_here_rejected_downstream).
    # It parses to a value_column, not an error, here.
    result = parse_expr("person", "gender == Foo.BAR")
    assert result == [
        {"column": "gender", "op": "eq", "value_column": {"json_path": ["Foo", "BAR"]}}
    ]


def test_unknown_constant_namespace_still_rejected_inside_in_list():
    # `in [...]` elements always go through `_translate_value` directly
    # (`_translate_list` never consults `_is_path_node` -- a list literal
    # has no path-shaped elements to disambiguate), so strict constant
    # validation still applies there.
    with pytest.raises(QueryLangError):
        parse_expr("person", "gender in [Foo.BAR]")


def test_unknown_constant_name_rejected():
    with pytest.raises(QueryLangError):
        parse_expr("person", "gender == Person.NOT_A_REAL_CONSTANT")


def test_two_level_attribute_chain_treated_as_path():
    # `_is_path_node` only special-cases a single-level Attribute(Name, attr)
    # as a possible constant; a deeper chain like `a.Person.MALE` is
    # structurally identical to any other multi-segment path (e.g.
    # `birth.place.title`), so it's treated the same permissive way --
    # not an error at this layer.
    result = parse_expr("person", "gender == a.Person.MALE")
    assert result == [
        {
            "column": "gender",
            "op": "eq",
            "value_column": {"json_path": ["a", "Person", "MALE"]},
        }
    ]


def test_two_level_attribute_chain_still_rejected_inside_in_list():
    with pytest.raises(QueryLangError):
        parse_expr("person", "gender in [a.Person.MALE]")


# --- Date(...) call ---------------------------------------------------------------


def test_date_call_resolves_to_sortval():
    result = parse_expr("event", "date.sortval == Date('Jan 1, 1968')")
    assert result == [
        {"column": {"json_path": ["date", "sortval"]}, "op": "eq", "value": 2439857}
    ]


def test_date_call_supports_ordering_comparisons():
    gte = parse_expr("event", "date.sortval >= Date('Jan 1, 1968')")
    assert gte[0]["op"] == "gte"
    assert gte[0]["value"] == 2439857

    lt = parse_expr("event", "date.sortval < Date('Jan 1, 1968')")
    assert lt[0]["op"] == "lt"
    assert lt[0]["value"] == 2439857


def test_date_call_range_via_and():
    result = parse_expr(
        "event",
        "date.sortval >= Date('Jan 1, 1968') and date.sortval <= Date('Dec 31, 1968')",
    )
    assert len(result) == 2
    assert result[0]["value"] < result[1]["value"]


def test_date_call_in_list():
    result = parse_expr("event", "date.sortval in [Date('Jan 1, 1968')]")
    assert result == [
        {"column": {"json_path": ["date", "sortval"]}, "op": "in", "value": [2439857]}
    ]


def test_date_call_unparseable_string_rejected():
    with pytest.raises(QueryLangError):
        parse_expr("event", "date.sortval >= Date('not a real date')")


def test_date_call_wrong_arity_rejected():
    with pytest.raises(QueryLangError):
        parse_expr("event", "date.sortval >= Date()")
    with pytest.raises(QueryLangError):
        parse_expr("event", "date.sortval >= Date('a', 'b')")


def test_date_call_non_string_argument_rejected():
    with pytest.raises(QueryLangError):
        parse_expr("event", "date.sortval >= Date(5)")


def test_date_call_rejected_as_like_pattern():
    # like(...)'s second argument must stay a plain string -- Date()
    # resolves to an int, which the existing string check already rejects.
    with pytest.raises(QueryLangError):
        parse_expr("event", "like(description, Date('Jan 1, 1968'))")


# --- Relationship-crossing paths in where_expr (birth/death/father/mother/place) --
#
# query_lang.py has no relationship-specific knowledge of its own -- a
# multi-segment path always becomes {"json_path": [...]}, the same as any
# other multi-segment path; query.py's resolve_column_path (exercised via
# object_query.py's _parse_column_ref/_build_where, not here) is what
# actually recognizes "birth"/"father"/etc. as relationships. These tests
# only check what this module itself produces: the raw wire JSON.


def test_birth_date_sortval_reference():
    result = parse_expr("person", "birth.date.sortval >= Date('Jan 1, 1968')")
    assert result == [
        {"column": {"json_path": ["birth", "date", "sortval"]}, "op": "gte", "value": 2439857}
    ]


def test_death_date_sortval_reference():
    result = parse_expr("person", "death.date.sortval < Date('Jan 1, 2000')")
    assert result == [
        {"column": {"json_path": ["death", "date", "sortval"]}, "op": "lt", "value": 2451545}
    ]


def test_birth_date_sortval_range_via_and():
    result = parse_expr(
        "person",
        "birth.date.sortval >= Date('Jan 1, 1968') and birth.date.sortval < Date('Jan 1, 1969')",
    )
    assert len(result) == 2
    assert result[0]["column"] == result[1]["column"] == {
        "json_path": ["birth", "date", "sortval"]
    }
    assert result[0]["value"] < result[1]["value"]


def test_birth_date_sortval_in_list():
    result = parse_expr("person", "birth.date.sortval in [Date('Jan 1, 1968')]")
    assert result == [
        {"column": {"json_path": ["birth", "date", "sortval"]}, "op": "in", "value": [2439857]}
    ]


def test_father_surname_reference():
    result = parse_expr("family", "father.surname == 'Smith'")
    assert result == [
        {"column": {"json_path": ["father", "surname"]}, "op": "eq", "value": "Smith"}
    ]


def test_mother_surname_reference():
    result = parse_expr("family", "mother.surname == 'Jones'")
    assert result == [
        {"column": {"json_path": ["mother", "surname"]}, "op": "eq", "value": "Jones"}
    ]


def test_two_hop_chain_reference():
    result = parse_expr("person", "birth.place.title == 'Chicago'")
    assert result == [
        {
            "column": {"json_path": ["birth", "place", "title"]},
            "op": "eq",
            "value": "Chicago",
        }
    ]


def test_bare_relationship_name_parses_here_rejected_downstream():
    # query_lang.py itself doesn't know "birth" is special -- a bare
    # relationship name with nothing after it just becomes a single-segment
    # json_path here (there's nothing to compare it to at this layer's
    # level); object_query.py's resolve_column_path is what rejects it, one
    # layer down (see test_object_query_parsing.py).
    result = parse_expr("person", "birth == 5")
    assert result == [{"column": {"json_path": ["birth"]}, "op": "eq", "value": 5}]


# --- Field-vs-field comparisons (value_column) -------------------------------------


def test_field_vs_field_produces_value_column():
    result = parse_expr(
        "family", "mother.death.date.sortval < father.death.date.sortval"
    )
    assert result == [
        {
            "column": {"json_path": ["mother", "death", "date", "sortval"]},
            "op": "lt",
            "value_column": {"json_path": ["father", "death", "date", "sortval"]},
        }
    ]


def test_field_vs_field_all_comparable_operators():
    for op_src, op_json in [
        ("==", "eq"),
        ("!=", "ne"),
        ("<", "lt"),
        ("<=", "lte"),
        (">", "gt"),
        (">=", "gte"),
    ]:
        result = parse_expr("family", f"father.surname {op_src} mother.surname")
        assert result == [
            {
                "column": {"json_path": ["father", "surname"]},
                "op": op_json,
                "value_column": {"json_path": ["mother", "surname"]},
            }
        ]


def test_field_vs_field_flat_column_both_sides():
    # Single-segment paths that happen to match a real flat column name
    # (not a relationship name) stay plain strings on both sides, same as
    # the single-path case.
    result = parse_expr("family", "father_handle == mother_handle")
    assert result == [
        {"column": "father_handle", "op": "eq", "value_column": "mother_handle"}
    ]


def test_field_vs_field_subscript_rhs():
    # A `Subscript` RHS (e.g. an indexed path) is also path-shaped, not a
    # literal -- `_is_path_node` must recognize it too.
    result = parse_expr(
        "person", "primary_name.surname_list[0].surname == primary_name.surname_list[1].surname"
    )
    assert result == [
        {
            "column": {"json_path": ["primary_name", "surname_list", 0, "surname"]},
            "op": "eq",
            "value_column": {"json_path": ["primary_name", "surname_list", 1, "surname"]},
        }
    ]


def test_field_vs_field_rejected_for_in_operator():
    # 'in' always expects a list literal RHS; a bare path there is not a
    # valid list and should be rejected the same way any other non-list
    # value would be, not silently treated as a value_column.
    with pytest.raises(QueryLangError):
        parse_expr("family", "father.surname in mother.surname")


def test_field_vs_field_rhs_class_constant_still_treated_as_value():
    # The one genuinely ambiguous shape: `Person.MALE` is a single-level
    # Attribute(Name, attr) just like `father.surname` -- must resolve as a
    # constant (plain `value`), not misfire as `value_column`.
    result = parse_expr("person", "gender == Person.MALE")
    assert result == [{"column": "gender", "op": "eq", "value": 1}]


def test_field_vs_field_lhs_and_rhs_paths_combined_with_and():
    result = parse_expr(
        "family",
        "father.surname == mother.surname and father.gender == 1",
    )
    assert len(result) == 2
    assert result[0] == {
        "column": {"json_path": ["father", "surname"]},
        "op": "eq",
        "value_column": {"json_path": ["mother", "surname"]},
    }
    assert result[1] == {
        "column": {"json_path": ["father", "gender"]},
        "op": "eq",
        "value": 1,
    }


# --- compile_expr / compile_expr_for_spec (expr string -> query.py AST) ------


def test_compile_expr_plain_column():
    spec, where = compile_expr("person", "gender == Person.MALE")
    assert spec is PERSON
    assert where == Eq("gender", 1)


def test_compile_expr_for_spec_matches_compile_expr():
    spec, where = compile_expr("person", "gender == 1")
    assert compile_expr_for_spec(PERSON, "gender == 1") == where


def test_compile_expr_json_path_not_a_relationship():
    _, where = compile_expr("person", "primary_name.surname_list[0].surname == 'Smith'")
    assert where == Eq(
        JsonPath(("primary_name", "surname_list", 0, "surname")), "Smith"
    )


def test_compile_expr_relationship_path_becomes_related_object():
    _, where = compile_expr("person", "birth.date.sortval >= 2439857")
    assert isinstance(where, Gte)
    assert isinstance(where.column, RelatedObject)
    assert where.column.name == "birth"


def test_compile_expr_field_vs_field_relationship_paths():
    _, where = compile_expr(
        "family", "mother.death.date.sortval < father.death.date.sortval"
    )
    assert isinstance(where, Lt)
    assert isinstance(where.column, RelatedObject) and where.column.name == "mother"
    assert isinstance(where.value, RelatedObject) and where.value.name == "father"


def test_compile_expr_multiple_conditions_become_and():
    _, where = compile_expr("person", "gender == Person.MALE and surname == 'Smith'")
    assert isinstance(where, And)
    assert where.exprs == (Eq("gender", 1), Eq("surname", "Smith"))


def test_compile_expr_or_becomes_or():
    _, where = compile_expr("person", "given_name == 'John' or given_name == 'Jane'")
    assert isinstance(where, Or)
    assert where.exprs == (Eq("given_name", "John"), Eq("given_name", "Jane"))


def test_compile_expr_mixed_and_or_nests_correctly():
    # "(a or b) and c" -- Or nested inside And, matching Python's own
    # grouping, not flattened or reordered.
    _, where = compile_expr(
        "person", "(gender == 1 or gender == 2) and surname == 'Smith'"
    )
    assert isinstance(where, And)
    assert where.exprs[1] == Eq("surname", "Smith")
    assert isinstance(where.exprs[0], Or)
    assert where.exprs[0].exprs == (Eq("gender", 1), Eq("gender", 2))


def test_compile_expr_not_becomes_not():
    # Not (query.py) doesn't define __eq__, so compare its wrapped .expr
    # directly, the same way test_compile_expr_or_becomes_or compares
    # Or's .exprs rather than the combinator object itself.
    _, where = compile_expr("person", "not (surname == 'Smith')")
    assert isinstance(where, Not)
    assert where.expr == Eq("surname", "Smith")


def test_compile_expr_not_wraps_and():
    _, where = compile_expr("person", "not (gender == 1 and surname == 'Smith')")
    assert isinstance(where, Not)
    assert isinstance(where.expr, And)
    assert where.expr.exprs == (Eq("gender", 1), Eq("surname", "Smith"))


def test_compile_expr_end_to_end_sqlite_execution():
    import sqlite3

    from gramps_object_query_language.query import Dialect, Query, compile_query

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE person (handle TEXT, gender INTEGER, surname TEXT)")
    conn.execute("INSERT INTO person VALUES ('p1', 1, 'Smith')")
    conn.execute("INSERT INTO person VALUES ('p2', 2, 'Smith')")

    spec, where = compile_expr("person", "gender == Person.MALE")
    sql, params = compile_query(spec, Query(select=["handle"], where=where), dialect=Dialect.SQLITE)
    rows = conn.execute(sql, params).fetchall()
    assert rows == [("p1",)]
