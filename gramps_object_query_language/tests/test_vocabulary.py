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

"""Every name `vocabulary()` offers is one the parser accepts."""

import json

import pytest

from gramps_object_query_language.query import resolve_collection, resolve_column_path
from gramps_object_query_language.query_lang import parse_expr, resolve_namespace
from gramps_object_query_language.vocabulary import main, namespaces, vocabularies, vocabulary

NAMESPACES = namespaces()


def _field_paths(fields, prefix=()):
    """Every path through a `fields` tree, indexing each array with `[0]`."""
    for name, node in fields.items():
        path = prefix + (name,)
        yield path
        while "items" in node:
            path = path + (0,)
            node = node["items"]
        yield from _field_paths(node.get("fields", {}), path)


def test_namespaces_cover_every_type():
    assert NAMESPACES == [
        "Person", "Family", "Event", "Place", "Repository",
        "Source", "Citation", "Media", "Note", "Tag",
    ]


def test_is_plain_json():
    data = vocabularies()
    assert json.loads(json.dumps(data)) == data


@pytest.mark.parametrize("namespace", NAMESPACES)
def test_columns_and_fields_resolve(namespace):
    spec = resolve_namespace(namespace)
    vocab = vocabulary(namespace)
    assert vocab["columns"]
    for column in vocab["columns"]:
        resolve_column_path(spec, [column])
    paths = list(_field_paths(vocab["fields"]))
    assert paths
    for path in paths:
        resolve_column_path(spec, list(path))


@pytest.mark.parametrize("namespace", NAMESPACES)
def test_relationships_and_collections_resolve(namespace):
    spec = resolve_namespace(namespace)
    vocab = vocabulary(namespace)
    for name, target in vocab["relationships"].items():
        assert target in NAMESPACES
        resolve_column_path(spec, [name, "gramps_id"])
    assert "backlinks" in vocab["collections"]
    for name, target in vocab["collections"].items():
        resolve_collection(spec, name)
        assert target is None or target in NAMESPACES
        parse_expr(namespace, f"count({name}) > 0")


def test_constants_parse():
    for class_name, names in vocabulary("Person")["constants"].items():
        assert names
        for name in names:
            parse_expr("Person", f"gender == {class_name}.{name}")


def test_keywords_functions_and_operators_parse():
    vocab = vocabulary("Person")
    expr = (
        "not (gender == 1 or gender != 0) and gender is not None and private is False"
        " and gender in [1, 2] and gramps_id < 'I9' and gramps_id <= 'I9'"
        " and gramps_id > 'I0' and gramps_id >= 'I0' and private is not True"
        " and like(surname, 'S%') and regex(surname, 'S.*')"
        " and birth.date.sortval > Date('1900') and exists(events)"
        " and count(events) > 1 and any(e.date.sortval > 0 for e in events)"
        " and len([e for e in events if e.date.sortval > 0]) > 0"
    )
    parse_expr("Person", expr)
    for token in vocab["keywords"] + vocab["functions"] + vocab["operators"]:
        assert token in expr


def test_relationship_target_detail():
    assert vocabulary("Person")["relationships"] == {"birth": "Event", "death": "Event"}
    collections = vocabulary("Person")["collections"]
    assert collections["events"] == "Event"
    assert collections["backlinks"] is None
    assert collections["child_refs"] is None


def test_links_describe_entries_and_referrers():
    """A loop variable over child_refs/event_refs is an entry (a ChildRef,
    an EventRef) with `referrer`, the record holding it."""
    event_refs = vocabulary("Event")["links"]["event_refs"]
    assert event_refs["referrers"] == ["Person", "Family"]
    assert {"role", "private", "attribute_list", "ref"} <= set(event_refs["entry"]["fields"])
    assert event_refs["entry"]["fields"]["role"]["type_names"] == "EventRoleType"
    child_refs = vocabulary("Person")["links"]["child_refs"]
    assert child_refs["referrers"] == ["Family"]
    assert {"frel", "mrel"} <= set(child_refs["entry"]["fields"])
    assert vocabulary("Family")["links"] == {}


def test_main_prints_json(capsys):
    assert main(["person"]) == 0
    assert json.loads(capsys.readouterr().out)["namespace"] == "Person"
    assert main([]) == 0
    assert set(json.loads(capsys.readouterr().out)) == set(NAMESPACES)


def _constant_fields(fields, prefix=()):
    """`(path, {"class", "prefix"})` for every field annotated with constants."""
    for name, node in fields.items():
        path = prefix + (name,)
        if "constants" in node:
            yield path, node["constants"]
        while "items" in node:
            path = path + (0,)
            node = node["items"]
        yield from _constant_fields(node.get("fields", {}), path)


@pytest.mark.parametrize("namespace", NAMESPACES)
def test_value_constants_parse_against_their_field(namespace):
    vocab = vocabulary(namespace)
    for path, constants in _constant_fields(vocab["fields"]):
        names = [
            name for name in vocab["constants"][constants["class"]]
            if name.startswith(constants["prefix"])
        ]
        assert names, (path, constants)
        text = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in path).lstrip(".")
        for name in names:
            parse_expr(namespace, f"{text} == {constants['class']}.{name}")


def test_value_constants_cover_the_known_fields():
    person = vocabulary("Person")["fields"]
    assert person["gender"]["constants"] == {"class": "Person", "prefix": ""}
    assert person["primary_name"]["fields"]["date"]["fields"]["modifier"]["constants"] == {
        "class": "Date", "prefix": "MOD_",
    }
    assert vocabulary("Event")["fields"]["type"]["fields"]["value"]["constants"]["class"] == "EventType"
    assert vocabulary("Citation")["fields"]["confidence"]["constants"]["prefix"] == "CONF_"
    assert "constants" not in person["change"]


@pytest.mark.parametrize("namespace", NAMESPACES)
def test_column_types_match_the_spec(namespace):
    spec = resolve_namespace(namespace)
    types = vocabulary(namespace)["column_types"]
    assert set(types) == set(spec.columns)
    for column, column_type in types.items():
        if column in spec.text_columns:
            assert column_type == "string"
        elif column in spec.bool_columns:
            assert column_type == "boolean"
        else:
            assert column_type == "integer"


def _type_name_fields(fields, prefix=()):
    """`(path, class name)` for every field holding a Gramps type object."""
    for name, node in fields.items():
        path = prefix + (name,)
        if "type_names" in node:
            yield path, node["type_names"]
        while "items" in node:
            path = path + (0,)
            node = node["items"]
        yield from _type_name_fields(node.get("fields", {}), path)


@pytest.mark.parametrize("namespace", NAMESPACES)
def test_type_names_compile_to_their_built_in_value(namespace):
    """Every name completion offers for a type field is one GOQL reads as
    that built-in type -- `value == <its number>`, never a custom type."""
    from gramps_object_query_language.query import Eq
    from gramps_object_query_language.query_lang import _CONSTANT_CLASSES, compile_expr

    vocab = vocabulary(namespace)
    found = list(_type_name_fields(vocab["fields"]))
    assert found or namespace == "Tag"  # a tag has no typed fields
    for path, class_name in found:
        cls = _CONSTANT_CLASSES[class_name]
        text = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in path).lstrip(".")
        names = vocab["type_names"][class_name]
        assert names, class_name
        for name in names:
            _spec, where = compile_expr(namespace, f"{text} == {name!r}")
            assert isinstance(where, Eq), (path, name)
            assert where.value == cls._E2IMAP[name] != cls._CUSTOM, (path, name)


def test_type_names_mark_the_known_fields():
    event = vocabulary("Event")
    assert event["fields"]["type"]["type_names"] == "EventType"
    assert "Census" in event["type_names"]["EventType"]
    assert "Custom" not in event["type_names"]["EventType"]
    family = vocabulary("Family")["fields"]
    assert family["type"]["type_names"] == "FamilyRelType"
    assert family["child_ref_list"]["items"]["fields"]["frel"]["type_names"] == "ChildRefType"
    assert "type_names" not in family["type"]["fields"]["value"]
