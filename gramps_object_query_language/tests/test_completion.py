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

import json
from pathlib import Path

import pytest

from gramps_object_query_language.completion import complete, complete_from_vocabularies
from gramps_object_query_language.query_lang import QueryLangError
from gramps_object_query_language.vocabulary import vocabularies

CASES = json.loads((Path(__file__).parent / "completion_cases.json").read_text())["cases"]


@pytest.mark.parametrize(
    "case", CASES, ids=[f"{c['namespace']}:{c['source']}" for c in CASES]
)
def test_shared_case(case):
    source = case["source"]
    cursor = case.get("cursor", len(source))
    results = complete(case["namespace"], source, cursor, case.get("env", ()))
    names = [r["name"] for r in results]
    if "expect" in case:
        assert names == case["expect"]
    for name in case.get("contains", []):
        assert name in names
    for name in case.get("excludes", []):
        assert name not in names
    kinds = {r["name"]: r["kind"] for r in results}
    for name, kind in case.get("kind", {}).items():
        assert kinds[name] == kind
    if "start" in case:
        assert all(r["start"] == case["start"] for r in results)


def test_applying_a_completion_replaces_the_typed_prefix():
    source = "birth.date.so and gender == 1"
    cursor = len("birth.date.so")
    [result] = complete("Person", source, cursor)
    applied = source[: result["start"]] + result["name"] + source[cursor:]
    assert applied == "birth.date.sortval and gender == 1"


def test_lowercase_namespace_and_unknown_namespace():
    assert complete("person", "birth.date.so", 13)[0]["name"] == "sortval"
    with pytest.raises(QueryLangError):
        complete("nosuch", "", 0)


def test_details():
    by_name = {r["name"]: r for r in complete("Family", "", 0)}
    assert by_name["father"]["detail"] == "Person"
    assert by_name["children"]["detail"] == "Person"
    assert by_name["backlinks"]["detail"] is None
    [sortval] = complete("Person", "birth.date.sortval", 18)
    assert sortval["detail"] == "integer"


def test_pure_function_needs_only_the_json():
    vocabs = json.loads(json.dumps(vocabularies()))
    assert [r["name"] for r in complete_from_vocabularies(vocabs, "Person", "birth.date.so", 13)] == [
        "sortval"
    ]
