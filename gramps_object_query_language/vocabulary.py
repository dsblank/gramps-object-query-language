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

"""Every name a `where_expr` can use, as plain JSON-compatible data.

This is the public way to enumerate the language's vocabulary -- the flat
columns, JSON fields, relationships, collections, keywords, functions and
`ClassName.CONST` constants for each namespace -- so editors can offer
completion, highlighting or help without reaching into `query.py`'s and
`query_lang.py`'s private registries. `completion.complete()` is built on
it, and a client with no Python at all (a browser app) can load the JSON
this module prints and run the same matching itself:

    python -m gramps_object_query_language.vocabulary > goql-vocabulary.json

Everything here is derived from the same registries the parser and
compiler use, never a hand-kept copy, so a name offered here is a name the
parser accepts (`tests/test_vocabulary.py` checks that), and a name added to
the language shows up here without anyone remembering to list it.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List

from .query import (
    Collection,
    ObjectTypeSpec,
    _COLLECTIONS,
    _RELATIONSHIPS,
    _schema_properties,
)
from gramps.gen.lib.grampstype import GrampsType

from .query_lang import ENV_NAME, _CONSTANT_CLASSES, _CONSTANTS, _NAMES, resolve_namespace, type_names


def _value_constants(owner: Any, field: str) -> Any:
    """`{"class", "prefix"}` naming the constants `owner.field` takes, or
    `None`."""
    if owner in _CONSTANTS and owner not in _NON_TYPE_CLASSES and field == "value":
        return {"class": owner, "prefix": ""}
    found = _FIELD_CONSTANTS.get((owner, field))
    return None if found is None else {"class": found[0], "prefix": found[1]}

#: Python keywords and literals a `where_expr` accepts: boolean operators,
#: `in`/`not in`, `is`/`is not`, the literal values, and the comprehension
#: words `for`/`if` (`any(... for e in events if ...)`).
KEYWORDS = ("and", "or", "not", "in", "is", "None", "True", "False", "for", "if")

#: The whitelisted function-call forms -- see `query_lang.py`'s docstring.
#: `any(...)`/`len(...)` also take the comprehension forms
#: (`any(e.date.sortval > 0 for e in events)`).
FUNCTIONS = ("any", "count", "Date", "exists", "len", "like", "regex")

#: Comparison operators. `in`/`is` are in `KEYWORDS`.
OPERATORS = ("==", "!=", "<", "<=", ">", ">=")

#: Bumped whenever the shape of `vocabulary()`'s result changes, so a client
#: holding a saved copy can tell it needs regenerating.
FORMAT_VERSION = 4

#: Fields whose integer value is one of a class's `ClassName.CONST`
#: constants, keyed by `(owning class, field)` -- the owning class being the
#: `_class` its schema object names -- to `(constant class, name prefix)`.
#: Every Gramps type object's `value` (`type.value` -> `EventType`) is
#: covered generically in `_value_constants`; these are the rest.
_FIELD_CONSTANTS = {
    ("Person", "gender"): ("Person", ""),
    ("Citation", "confidence"): ("Citation", "CONF_"),
    ("Note", "format"): ("Note", ""),
    ("Date", "calendar"): ("Date", "CAL_"),
    ("Date", "modifier"): ("Date", "MOD_"),
    ("Date", "quality"): ("Date", "QUAL_"),
    ("Date", "newyear"): ("Date", "NEWYEAR_"),
}
# Constant classes that are records or Date rather than Gramps type objects
# -- their constants belong to specific fields above, not to a `value`.
_NON_TYPE_CLASSES = {"Person", "Citation", "Note", "Date"}


def namespaces() -> List[str]:
    """The namespace names, in their class-name casing (`"Person"`, ...)."""
    return [name.capitalize() for name in _NAMES]


def _namespace_name(spec: ObjectTypeSpec) -> str:
    return spec.cls.__name__


def _field_tree(schema: Dict[str, Any]) -> Dict[str, Any]:
    """`schema`'s fields, recursively, as `{name: node}` where a node is
    `{"type": ...}` plus `"fields"` for an object, `"items"` for an
    array, and `"constants": {"class", "prefix"}` for an integer that holds
    one of a class's `ClassName.CONST` values (`type.value` ->
    `EventType`, `date.modifier` -> `Date.MOD_*`). Follows only what `walk_schema` follows (`properties`, `items`,
    plus `_SCHEMA_GAPS`), so every path through the tree is one the
    resolver accepts. `_`-prefixed internals (`_class`) are left out, as
    they are from `walk_schema`'s "known fields" message.
    """
    properties = _schema_properties(schema)
    owner = (properties.get("_class", {}).get("enum") or [None])[0]
    fields: Dict[str, Any] = {}
    for name, sub in sorted(properties.items()):
        if name.startswith("_"):
            continue
        fields[name] = _field_node(sub)
        constants = _value_constants(owner, name)
        if constants is not None:
            fields[name]["constants"] = constants
    return fields


#: The Gramps type classes (`EventType`, `FamilyRelType`, ...): an object
#: field holding one can be compared with a name, `type == 'Census'`.
_TYPE_CLASSES = {
    name: cls
    for name, cls in _CONSTANT_CLASSES.items()
    if isinstance(cls, type) and issubclass(cls, GrampsType)
}


def _field_node(schema: Dict[str, Any]) -> Dict[str, Any]:
    node: Dict[str, Any] = {"type": schema.get("type")}
    if schema.get("properties"):
        node["fields"] = _field_tree(schema)
        owner = (_schema_properties(schema).get("_class", {}).get("enum") or [None])[0]
        if owner in _TYPE_CLASSES:
            # `type == 'Census'`: the names are in `vocabulary()["type_names"]`.
            node["type_names"] = owner
    items = schema.get("items")
    if isinstance(items, dict):
        node["items"] = _field_node(items)
    return node


def _collection_target(collection: Any) -> Any:
    """The namespace a collection's elements are, or `None` for
    `backlinks` (any type) and self-linked collections (`child_refs`,
    whose condition is about the linking entry, not the joined row).
    """
    if not isinstance(collection, Collection) or collection.self_link_field:
        return None
    return _namespace_name(collection.target)


def vocabulary(namespace: str) -> Dict[str, Any]:
    """Everything a `where_expr` on `namespace` can name, as JSON-ready data.

    Keys:

    - `namespace`: the class-name form (`"Person"`).
    - `columns`: flat, indexed secondary columns (`surname`, `gender`, ...).
    - `column_types`: each column's JSON type -- `"string"`, `"boolean"`
      or `"integer"` -- since a column that isn't also a JSON field
      (`surname`) has no entry in `fields` to say so.
    - `fields`: the record's JSON fields, recursively (see `_field_tree`).
      Overlaps `columns` for fields stored both ways (`gramps_id`).
    - `relationships`: `{name: target namespace}` for one-to-one hops
      usable as a path segment (`birth.date.sortval`).
    - `collections`: `{name: target namespace or None}` for one-to-many
      lists usable in `any`/`exists`/`count`/`len`.
    - `keywords`, `functions`, `operators`: the fixed grammar tokens.
    - `constants`: `{ClassName: [CONST, ...]}` for `ClassName.CONST` values.
    - `type_names`: `{ClassName: [name, ...]}`, each Gramps type class's
      built-in type names (English), which a field whose node carries
      `"type_names": ClassName` can be compared with (`type == 'Census'`).
    - `env`: the name callers' `Env.NAME` values are written under.

    Raises `QueryLangError` for an unknown namespace.
    """
    spec = resolve_namespace(namespace)
    return {
        "format_version": FORMAT_VERSION,
        "namespace": _namespace_name(spec),
        "columns": sorted(spec.columns),
        "column_types": {
            column: (
                "string" if column in spec.text_columns
                else "boolean" if column in spec.bool_columns
                else "integer"
            )
            for column in sorted(spec.columns)
        },
        "fields": _field_tree(spec.cls.get_schema()),
        "relationships": {
            name: _namespace_name(target)
            for name, (target, _ref) in sorted(_RELATIONSHIPS.get(spec.table, {}).items())
        },
        "collections": {
            name: _collection_target(collection)
            for name, collection in sorted(_COLLECTIONS.get(spec.table, {}).items())
        },
        "keywords": list(KEYWORDS),
        "functions": list(FUNCTIONS),
        "operators": list(OPERATORS),
        "constants": {name: sorted(values) for name, values in sorted(_CONSTANTS.items())},
        "type_names": {name: type_names(cls) for name, cls in sorted(_TYPE_CLASSES.items())},
        "env": ENV_NAME,
    }


def vocabularies() -> Dict[str, Dict[str, Any]]:
    """`vocabulary()` for every namespace, keyed by namespace name."""
    return {name: vocabulary(name) for name in namespaces()}


def main(argv: List[str]) -> int:
    """Print `vocabularies()` (or one namespace's, given as an argument) as JSON."""
    if len(argv) > 1:
        print(f"usage: python -m {__package__}.vocabulary [NAMESPACE]", file=sys.stderr)
        return 2
    data = vocabulary(argv[0]) if argv else vocabularies()
    json.dump(data, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
