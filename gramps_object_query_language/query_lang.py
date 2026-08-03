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

"""An "almost Python" expression language, parsed into `object_query.py`'s
JSON `where` shape -- e.g. `"primary_name.surname_list[0].surname == 'Smith'"`
becomes `[{"column": {"json_path": [...]}, "op": "eq", "value": "Smith"}]`.

Uses `ast.parse(expr, mode="eval")` as pure syntax, never `eval()` or
`compile()` -- the tree is inspected and translated node-by-node into plain
JSON, never executed. Safety comes from whitelisting node *shapes*, not
blacklisting names: any AST node this module doesn't explicitly recognize
(function calls other than the one whitelisted `like(...)` form, lambdas,
comprehensions, attribute access building toward dunder names, imports,
walrus, f-strings, ...) is rejected by `_translate_*` simply never handling
it and falling through to a `QueryLangError`.

Deliberately not wired to any HTTP endpoint yet -- see `query.py`'s
`JsonPath`, which followed the same build-it-standalone-first,
wire-it-up-later path this session.

Current scope, matching what `object_query.py`'s wire format actually
supports today:

- Top level is a boolean expression of comparisons combined with `and`/`or`/
  `not`, nested however Python's own precedence and grouping resolves it
  (`not` binds tightest, then `and`, then `or`, and parentheses group as
  usual) -- `not a == b and c > d or e == f` parses the same way real
  Python would. The wire shape stays a flat list of leaf conditions,
  implicitly AND'd, for any expression that doesn't use `or`/`not` at all
  -- byte-identical to before either was supported. An expression that
  does use them gets an `{"or": [...]}`/`{"and": [...]}`/`{"not": node}`
  node in place of a leaf wherever it's needed -- see
  `_translate_top_level`/`_translate_bool_or_leaf`.
- A comparison is `OPERAND OP OPERAND` where `OP` is one of
  `== != < <= > >=`, `is`/`is not`, or Python's `in`/`not in`
  (`path in [v1, v2, ...]`) -- these are all the same `ast.Compare` node
  shape, just different `ops`. `is`/`is not` are pure sugar for `==`/`!=`
  (no notion of object identity here, only value equality) and `not in`
  is pure sugar for wrapping `in`'s own translation in `{"not": ...}` --
  none of the three introduce a new wire shape.
- Either side of `==`/`!=`/`</`/`<=`/`>`/`>=` may be the path and the other
  the value -- `5 < gender` and `gender > 5` compile to the identical wire
  node, via `_FLIP_OP` (`lt`<->`gt`, `lte`<->`gte`, `eq`/`ne` unchanged) --
  the wire shape always renders the path as `"column"`, regardless of which
  side of the source expression it was written on. `count(...)` stays an
  exception on purpose: it's only ever recognized when it's *the* left-hand
  operand, per its own left-hand-side-only v1 scope (see
  `_translate_column_or_count`) -- `2 < count(children)` doesn't flip into
  a supported shape, unlike `2 < gender`.
- `in` has a second shape too: `'substring' in path` (a string literal on
  the left, a path on the right) is a plain substring test (`Contains`),
  disambiguated from `path in [...]` purely by the right-hand node's shape
  (`ast.List` vs. a path) -- the same `ast.Compare`/`ast.In` node either way.
- `like(path, 'pattern%')` is a whitelisted function-call form, for the one
  operator (`Like`) that isn't a Python operator.
- A path is a bare identifier optionally followed by `.attr` / `[index]`
  segments, e.g. `gender` or `primary_name.surname_list[0].surname`.
  Single-segment paths that match the target type's flat column whitelist
  resolve to a plain column reference (a real indexed SQL column); every
  other path becomes a `{"json_path": [...]}` reference.
- On the *value* side of a comparison, `ClassName.CONST` (e.g. `Person.MALE`,
  `Note.FLOWED`, `Date.MOD_ABOUT`, `EventType.BIRTH`) resolves to the real
  value read off the actual Gramps class -- see `_CONSTANTS` -- so
  `gender == Person.MALE` and `gender == 1` compile identically. Only a
  `Name.Attribute` shape one level deep is recognized (not `a.b.CONST`).
  Covers both flat-column fields (`Person.gender`, `Citation.confidence`,
  `Note.format`) and fields that only live nested in `json_data`
  (`birth.date.modifier == Date.MOD_ABOUT`, `type.value == EventType.BIRTH`)
  -- the constant class list is unrelated to where the field it's compared
  against happens to live.
- Also on the value side, `Date('Jan 1, 1968')` -- the second and last
  whitelisted call form -- parses a human date string with Gramps' own
  date parser and resolves to `.sortval`, a plain comparable integer
  (Julian day number), so `event.date.sortval >= Date('Jan 1, 1968')` and
  `birth.date.sortval >= Date('Jan 1, 1968')` both work with ordinary
  `>=`/`<=`/`<`/`>`.
- A path may cross a relationship, not just index into one column's own
  `json_data` -- `birth`/`death` (`Person` -> `Event`), `father`/`mother`
  (`Family` -> `Person`), `place` (`Event` -> `Place`) are resolved by
  `query.py`'s `resolve_column_path()`, which this module's path
  translation defers to entirely (see `_translate_column`) rather than
  duplicating any relationship knowledge here. `birth.date.sortval`,
  `father.surname`, and `birth.place.title` are all valid paths this way.
"""

from __future__ import annotations

import ast
from typing import Any, List, Tuple, Union

from gramps.gen.datehandler import parser as _date_parser
from gramps.gen.lib import (
    AttributeType,
    ChildRefType,
    Citation,
    Date,
    EventRoleType,
    EventType,
    FamilyRelType,
    MarkerType,
    NameOriginType,
    NameType,
    Note,
    NoteType,
    Person,
    PlaceType,
    RepositoryType,
    SourceMediaType,
    SrcAttributeType,
    StyledTextTagType,
    UrlType,
)

from .query import (
    CITATION,
    EVENT,
    FAMILY,
    MEDIA,
    NOTE,
    PERSON,
    PLACE,
    REPOSITORY,
    SOURCE,
    TAG,
    And,
    CollectionCount,
    ColumnRef,
    Contains,
    Eq,
    Exists,
    FlatColumnRef,
    Gt,
    Gte,
    In,
    Like,
    Lt,
    Lte,
    Ne,
    Not,
    ObjectTypeSpec,
    Or,
    QueryError,
    resolve_collection,
    resolve_column_path,
)

# Namespace -> ObjectTypeSpec. Both the lowercase form and the actual Gramps
# class-name casing (Person, Family, ...) are accepted; no single-letter
# aliases -- those aren't what was asked for, and Gramps' own gramps_id
# prefixes (P = Place, I = Person, ...) don't line up with the object names
# anyway, so a letter scheme here would just invite confusion.
_NAMES = {
    "person": PERSON,
    "family": FAMILY,
    "event": EVENT,
    "place": PLACE,
    "repository": REPOSITORY,
    "source": SOURCE,
    "citation": CITATION,
    "media": MEDIA,
    "note": NOTE,
    "tag": TAG,
}
_NAMESPACES: dict[str, ObjectTypeSpec] = {
    **_NAMES,
    **{name.capitalize(): spec for name, spec in _NAMES.items()},
}

# `ClassName.CONST` value constants, e.g. `gender == Person.MALE`,
# `type.value == EventType.BIRTH`, `birth.date.modifier == Date.MOD_ABOUT`.
# Values are read off the real Gramps classes, never hardcoded, so they
# can't drift out of sync with core if a constant's underlying value ever
# changes -- see `_int_constants`. Covers both constants that attach to a
# *flat* column (`Person.gender`, `Citation.confidence`, `Note.format`) and
# ones that only live nested in `json_data` (`Event.type` is stored as
# `{"_class": "EventType", "value": 12, "string": ""}`, so the constant is
# compared against `type.value`, not `type` itself) -- `_translate_constant`
# doesn't care which; that distinction is entirely in how the *path* side of
# the comparison resolves (`resolve_column_path`).
#
# Deliberately still not covering arbitrary user-defined custom type values
# (a `PlaceType` of "Ranch", say) -- those have no fixed constant to name in
# the first place, only ever a per-tree string paired with `.CUSTOM`.
_CONSTANT_CLASSES: dict[str, type] = {
    "Person": Person,
    "Citation": Citation,
    "Note": Note,
    "Date": Date,
    "AttributeType": AttributeType,
    "ChildRefType": ChildRefType,
    "EventRoleType": EventRoleType,
    "EventType": EventType,
    "FamilyRelType": FamilyRelType,
    "MarkerType": MarkerType,
    "NameOriginType": NameOriginType,
    "NameType": NameType,
    "NoteType": NoteType,
    "PlaceType": PlaceType,
    "RepositoryType": RepositoryType,
    "SourceMediaType": SourceMediaType,
    "SrcAttributeType": SrcAttributeType,
    "StyledTextTagType": StyledTextTagType,
    "UrlType": UrlType,
}


def _int_constants(cls: type) -> dict[str, int]:
    """Every ALL_CAPS `int` class attribute on `cls`, e.g. `{"MALE": 1,
    "FEMALE": 0, ...}` for `Person`.

    Auto-derived rather than hand-listed so a new constant added to a
    Gramps class (or the value of an existing one changing) shows up here
    automatically instead of silently drifting out of sync. `bool` is
    excluded despite being an `int` subclass -- no Gramps class defines a
    meaningful all-caps boolean constant, and including it would risk
    picking up something like a stray `True`/`False` class attribute as if
    it were a real value.
    """
    return {
        name: value
        for name in dir(cls)
        if name.isupper() and not name.startswith("_")
        for value in [getattr(cls, name)]
        if isinstance(value, int) and not isinstance(value, bool)
    }


_CONSTANTS: dict[str, dict[str, Any]] = {
    class_name: _int_constants(cls) for class_name, cls in _CONSTANT_CLASSES.items()
}


class QueryLangError(ValueError):
    """Raised when an expression doesn't parse or uses unsupported syntax."""


def resolve_namespace(namespace: str) -> ObjectTypeSpec:
    """Look up the `ObjectTypeSpec` for a namespace string (`"person"` or `"Person"`, ...)."""
    try:
        return _NAMESPACES[namespace]
    except KeyError:
        raise QueryLangError(f"unknown namespace: {namespace!r}") from None


def _translate_constant(class_name: str, const_name: str) -> Any:
    try:
        constants = _CONSTANTS[class_name]
    except KeyError:
        raise QueryLangError(
            f"unknown constant namespace: {class_name!r} "
            f"(known: {', '.join(sorted(_CONSTANTS))})"
        ) from None
    try:
        return constants[const_name]
    except KeyError:
        raise QueryLangError(
            f"unknown constant: {class_name}.{const_name} "
            f"(known: {', '.join(class_name + '.' + n for n in constants)})"
        ) from None


_FLIP_OP: dict[str, str] = {
    # For "value OP field" (the literal written on the left, e.g.
    # "Date(...) < mother.birth.sortval") -- the wire shape always puts the
    # column first, so the operator has to flip to keep the same meaning:
    # "A < B" becomes "B > A" once B (the field) is what's rendered as
    # "column". eq/ne are symmetric and flip to themselves.
    "eq": "eq",
    "ne": "ne",
    "lt": "gt",
    "lte": "gte",
    "gt": "lt",
    "gte": "lte",
}


_COMPARE_OPS: dict[type, str] = {
    ast.Eq: "eq",
    ast.NotEq: "ne",
    ast.Lt: "lt",
    ast.LtE: "lte",
    ast.Gt: "gt",
    ast.GtE: "gte",
    ast.In: "in",
    # `is`/`is not` are pure sugar for `==`/`!=` here -- this language has no
    # notion of object identity distinct from value equality, so `gender is
    # None` and `gender == None` compile to the exact same wire node. Reusing
    # "eq"/"ne" verbatim (rather than a dedicated "is"/"is not" wire op) means
    # every existing "eq"/"ne" code path -- field-vs-field, count(...), the
    # SQL/evaluator dialects -- already handles them with no new branches.
    ast.Is: "eq",
    ast.IsNot: "ne",
}


def _translate_path(node: ast.AST) -> List[Union[str, int]]:
    """Walk a `Name`/`Attribute`/`Subscript` chain into an ordered segment list.

    `a.b[0].c` is nested as `Attribute(Attribute(Subscript(Attribute(Name)))...)`
    with the outermost node being the *last* segment -- recurse to the base
    `Name` first, then build the list root-to-leaf.
    """
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return _translate_path(node.value) + [node.attr]
    if isinstance(node, ast.Subscript):
        index_node = node.slice
        if not isinstance(index_node, ast.Constant) or not isinstance(
            index_node.value, int
        ) or isinstance(index_node.value, bool):
            raise QueryLangError(
                f"subscript index must be a plain integer literal: {ast.dump(node)}"
            )
        return _translate_path(node.value) + [index_node.value]
    raise QueryLangError(f"invalid path expression: {ast.dump(node)}")


def _translate_column(node: ast.AST, spec: ObjectTypeSpec) -> Union[str, dict]:
    """Translate a path into a wire column reference: a plain string if it's
    a single segment matching a real flat column, `{"json_path": [...]}`
    otherwise.

    No relationship-specific knowledge lives here -- a multi-segment path
    like `birth.date.sortval` or `father.surname` becomes
    `{"json_path": ["birth", "date", "sortval"]}` the same way any other
    multi-segment path does; `object_query.py`'s `_parse_column_ref` is
    what actually recognizes `"birth"`/`"father"`/etc. as relationship
    roots (via `query.py`'s `resolve_column_path`) once it receives that
    wire form. A bare relationship name with nothing after it
    (`"birth"` alone) isn't a real flat column, so it falls through to
    `{"json_path": ["birth"]}` here too -- `resolve_column_path` rejects
    that with a clear error downstream, just one layer later than a
    dedicated check here would.
    """
    segments = _translate_path(node)
    if len(segments) == 1 and isinstance(segments[0], str) and segments[0] in spec.columns:
        return segments[0]
    return {"json_path": segments}


def _translate_count_call(node: ast.Call, spec: ObjectTypeSpec) -> dict:
    """Translate `count(relationship[, condition])` into
    `{"count_of": {"relationship": ..., "where": [...]}}` -- the *value*-
    producing counterpart to `exists(...)`'s leaf-producing
    `_translate_exists_call`. Appears as a comparison's column
    (`count(children) > 2`), never as a leaf on its own -- a bare
    `count(children)` with no comparison isn't a boolean, so it's rejected
    the same way a bare path (`gender`, with no `== ...`) already is.

    `relationship`/`condition` resolve exactly like `exists(...)`'s do --
    same `resolve_collection` lookup, same recursive `_translate_top_level`
    against the collection's target type for the optional condition.
    """
    if not 1 <= len(node.args) <= 2 or node.keywords:
        raise QueryLangError(
            "count(relationship[, condition]) takes 1 or 2 positional arguments"
        )
    name_node = node.args[0]
    if not isinstance(name_node, ast.Name):
        raise QueryLangError(
            f"count(...)'s first argument must be a bare relationship name: "
            f"{ast.dump(name_node)}"
        )
    try:
        collection = resolve_collection(spec, name_node.id)
    except QueryError as error:
        raise QueryLangError(str(error)) from error
    payload: dict = {"relationship": name_node.id}
    if len(node.args) == 2:
        payload["where"] = _translate_top_level(node.args[1], collection.target)
    return {"count_of": payload}


def _is_count_call(node: ast.AST) -> bool:
    """Is `node` a `count(...)` call, without translating it? Used to
    classify a comparison's operand as column-like *before* deciding how to
    translate it -- see `_translate_compare`'s left/right classification.
    """
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "count"


def _translate_column_or_count(node: ast.AST, spec: ObjectTypeSpec) -> Union[str, dict]:
    """A comparison's column-like side: an ordinary path (`_translate_column`),
    or a `count(...)` call -- the one place a "column" can be a *computed*
    value rather than a path, verbatim. `count(...)` is deliberately not
    recognized anywhere `_translate_column` itself is called directly (a
    plain field on the other side of a comparison, `'in'`'s list/substring
    branches) -- v1 scope only ever treats `count(...)` as *the* column,
    never as something compared against another field, matching `len()`'s
    own planned restriction (see ROADMAP.md).
    """
    if _is_count_call(node):
        return _translate_count_call(node, spec)
    return _translate_column(node, spec)


def _translate_date_call(node: ast.Call) -> int:
    """Translate `Date('Jan 1, 1968')` into its `.sortval` (a comparable
    Julian day number), via Gramps' own date parser -- not a custom one.
    """
    if len(node.args) != 1 or node.keywords:
        raise QueryLangError("Date(...) takes exactly 1 positional string argument")
    text = _translate_value(node.args[0])
    if not isinstance(text, str):
        raise QueryLangError("Date(...)'s argument must be a string literal")
    parsed = _date_parser.parse(text)
    if not parsed.is_valid():
        raise QueryLangError(f"could not parse {text!r} as a date")
    return parsed.sortval


def _translate_value(node: ast.AST) -> Any:
    """Translate a literal: string / int / float / bool / None, `-<number>`,
    a `ClassName.CONST` value constant (e.g. `Person.MALE`, see `_CONSTANTS`),
    or `Date('...')` (see `_translate_date_call`).
    """
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _translate_value(node.operand)
        if not isinstance(inner, (int, float)) or isinstance(inner, bool):
            raise QueryLangError(f"unary '-' only supported on numeric literals: {ast.dump(node)}")
        return -inner
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return _translate_constant(node.value.id, node.attr)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Date"
    ):
        return _translate_date_call(node)
    raise QueryLangError(f"invalid literal: {ast.dump(node)}")


def _translate_list(node: ast.AST) -> List[Any]:
    if not isinstance(node, ast.List):
        raise QueryLangError(f"expected a list literal, e.g. [1, 2, 3]: {ast.dump(node)}")
    return [_translate_value(elt) for elt in node.elts]


def _is_path_node(node: ast.AST) -> bool:
    """Is `node` a path reference (`Name`/`Attribute`/`Subscript` chain),
    rather than a literal/`Date(...)`/`ClassName.CONST`?

    The one ambiguous shape is a single-level `Attribute(Name, attr)` --
    `Person.MALE` (a constant) and `father.surname` (a path) look
    identical syntactically. Disambiguated the same way `_translate_value`
    already does: whether the base `Name` is a known constant class
    (`_CONSTANT_CLASSES`). Anything deeper (`a.b.c`, `a[0].b`) is
    unambiguously a path -- that shape is never valid for a constant
    (`_translate_value` only recognizes exactly one `Attribute` level).
    """
    if isinstance(node, (ast.Name, ast.Subscript)):
        return True
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id in _CONSTANT_CLASSES:
            return False
        return True
    return False


def _translate_compare(node: ast.Compare, spec: ObjectTypeSpec) -> dict:
    if len(node.ops) != 1 or len(node.comparators) != 1:
        # `a < b < c` -- Python allows chained comparisons, desugaring to
        # pairwise "and": `a < b and b < c` (real Python evaluates each
        # operand at most once; this translator only ever reads a path's
        # *value* at query time, never re-evaluates a Python expression, so
        # that subtlety doesn't apply here -- splitting into independent
        # legs is exactly equivalent). Each leg is an ordinary two-term
        # `ast.Compare`, translated by recursing into this same function --
        # chaining introduces no new comparison semantics of its own, so
        # every leg transparently supports whatever a plain comparison
        # already does (operand ordering, is/is not/in, field-vs-field,
        # even mixed operators like `1 < gender != 3`).
        operands = [node.left, *node.comparators]
        legs = [
            ast.Compare(left=left, ops=[op], comparators=[right])
            for left, op, right in zip(operands, node.ops, operands[1:])
        ]
        return {"and": [_translate_compare(leg, spec) for leg in legs]}
    op_type = type(node.ops[0])
    # "not in" reuses "in"'s own translation below verbatim, then wraps the
    # result in "not" at the very end -- `not (x in y)` already compiles and
    # evaluates correctly (see Done above: the Not/missing-value three-valued
    # logic fix), so there's no new semantics to add here, just sugar for a
    # shape users could already write with explicit parens.
    negate = op_type is ast.NotIn
    lookup_type = ast.In if negate else op_type
    if lookup_type not in _COMPARE_OPS:
        raise QueryLangError(
            f"unsupported comparison operator {op_type.__name__!r} "
            "(supported: == != < <= > >= is 'is not' in 'not in')"
        )
    op = _COMPARE_OPS[lookup_type]
    rhs = node.comparators[0]
    if op == "in":
        if isinstance(rhs, ast.List):
            # "field in [v1, v2, ...]" -- list membership.
            column = _translate_column_or_count(node.left, spec)
            value = _translate_list(rhs)
            if not value:
                raise QueryLangError("'in' requires a non-empty list")
            leaf = {"column": column, "op": "in", "value": value}
        elif _is_path_node(rhs):
            # "'substring' in field" / "other_field in field" -- a plain
            # substring test, mirroring what `in` already means for two real
            # Python strings. The field being searched is on the *right*
            # here (unlike every other operator), since that's what makes
            # `'Jan' in given_name` read the same as it would in real Python.
            column = _translate_column(rhs, spec)
            if _is_path_node(node.left):
                # "other_field in field" -- field-vs-field: the needle is
                # itself a path, only known at query execution time, not a
                # literal to bind now.
                value_column = _translate_column(node.left, spec)
                leaf = {"column": column, "op": "contains", "value_column": value_column}
            else:
                substring = _translate_value(node.left)
                if not isinstance(substring, str):
                    raise QueryLangError(
                        "'... in path' (substring test) requires a string literal "
                        "or a field path on the left, e.g. \"'Jan' in given_name\" "
                        f"or \"nickname in given_name\": {ast.dump(node)}"
                    )
                leaf = {"column": column, "op": "contains", "value": substring}
        else:
            raise QueryLangError(
                "'in' requires either a list literal ('field in [1, 2]') or a "
                f"field path on the right ('... in field', a substring test): {ast.dump(node)}"
            )
    else:
        left = node.left
        if _is_path_node(left) or _is_count_call(left):
            # "field OP value" / "field OP field" -- the shape this function
            # always assumed until operand-ordering was generalized. `left`
            # is the column (or `count(...)`); `rhs` is either another field
            # (`value_column`) or an ordinary value.
            column = _translate_column_or_count(left, spec)
            if _is_path_node(rhs):
                if isinstance(column, dict) and "count_of" in column:
                    # count(...) is left-hand-side-only, against a literal (v1
                    # scope, see ROADMAP.md) -- field-vs-field against a count
                    # isn't supported, so reject explicitly rather than
                    # silently building a value_column nothing downstream
                    # can render.
                    raise QueryLangError(
                        f"count(...) only supports comparison against a literal value, "
                        f"not a field: {ast.dump(node)}"
                    )
                # Field-vs-field: "families where mother.death.date.sortval <
                # father.death.date.sortval" -- the right-hand side is itself
                # a path, not a value to bind.
                value_column = _translate_column(rhs, spec)
                leaf = {"column": column, "op": op, "value_column": value_column}
            else:
                value = _translate_value(rhs)
                leaf = {"column": column, "op": op, "value": value}
        elif _is_path_node(rhs):
            # "value OP field", e.g. "Date('Jan 1, 1968') < mother.birth.sortval"
            # -- the literal happened to be written on the left. Flip the
            # operator so the column still renders on the wire's left, the
            # one shape query.py/evaluator.py know how to read -- count(...)
            # is deliberately not accepted here (see
            # `_translate_column_or_count`'s docstring): only a plain path
            # qualifies as "the column" on this side, matching count(...)'s
            # existing left-hand-side-only v1 scope untouched.
            value = _translate_value(left)
            column = _translate_column(rhs, spec)
            leaf = {"column": column, "op": _FLIP_OP[op], "value": value}
        else:
            raise QueryLangError(
                "a comparison must have a field path on at least one side "
                f"(count(...) is only supported on the left): {ast.dump(node)}"
            )
    return {"not": leaf} if negate else leaf


def _translate_like_call(node: ast.Call, spec: ObjectTypeSpec) -> dict:
    if len(node.args) != 2 or node.keywords:
        raise QueryLangError("like(path, 'pattern') takes exactly 2 positional arguments")
    column = _translate_column(node.args[0], spec)
    pattern = _translate_value(node.args[1])
    if not isinstance(pattern, str):
        raise QueryLangError("like(...)'s second argument must be a string literal")
    return {"column": column, "op": "like", "value": pattern}


def _translate_exists_call(node: ast.Call, spec: ObjectTypeSpec) -> dict:
    """Translate `exists(relationship[, condition])` into
    `{"exists": {"relationship": ..., "where": [...]}}` -- `where` omitted
    entirely when no condition is given (`exists(children)`, "at least one
    related row at all").

    `relationship`'s target type comes from `query.py`'s `_COLLECTIONS`
    registry (via `resolve_collection`), the same way a `_RELATIONSHIPS`
    name's target drives `resolve_column_path` -- `condition`, if given, is
    itself a full `where_expr` boolean expression, just parsed against that
    target type instead of `spec`, via the same `_translate_top_level` this
    module already uses for the top-level expression.
    """
    if not 1 <= len(node.args) <= 2 or node.keywords:
        raise QueryLangError(
            "exists(relationship[, condition]) takes 1 or 2 positional arguments"
        )
    name_node = node.args[0]
    if not isinstance(name_node, ast.Name):
        raise QueryLangError(
            f"exists(...)'s first argument must be a bare relationship name: "
            f"{ast.dump(name_node)}"
        )
    try:
        collection = resolve_collection(spec, name_node.id)
    except QueryError as error:
        raise QueryLangError(str(error)) from error
    payload: dict = {"relationship": name_node.id}
    if len(node.args) == 2:
        payload["where"] = _translate_top_level(node.args[1], collection.target)
    return {"exists": payload}


def _translate_comparison_like_node(node: ast.AST, spec: ObjectTypeSpec) -> dict:
    """A single leaf: a `Compare`, or a whitelisted `like(...)`/`exists(...)` call."""
    if isinstance(node, ast.Compare):
        return _translate_compare(node, spec)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "like":
            return _translate_like_call(node, spec)
        if node.func.id == "exists":
            return _translate_exists_call(node, spec)
    raise QueryLangError(
        f"expected a comparison (a == b, a in [...], like(a, 'pat'), "
        f"exists(rel, cond)), got: {ast.dump(node)}"
    )


def _translate_bool_or_leaf(node: ast.AST, spec: ObjectTypeSpec) -> dict:
    """Translate one node of a (possibly nested) boolean expression: a leaf
    comparison/`like(...)` call, or an `and`/`or`/`not` of further such nodes.

    `ast.parse` has already resolved Python's own `and`/`or`/`not`
    precedence and grouping into correctly nested `BoolOp`/`UnaryOp` nodes
    (`not` binds tighter than `and`, which binds tighter than `or`, so
    `not a and b or c` arrives as `BoolOp(Or, [BoolOp(And, [UnaryOp(Not, a),
    b]), c])`) -- this only walks whatever shape it's handed, it doesn't
    re-implement precedence itself. Any other `ast.UnaryOp` (`+a`, `~a`) has
    no case here and falls through to `_translate_comparison_like_node`,
    which rejects it with a clear error, the same as any other unrecognized
    node shape.
    """
    if isinstance(node, ast.BoolOp):
        key = "and" if isinstance(node.op, ast.And) else "or"
        return {key: [_translate_bool_or_leaf(value, spec) for value in node.values]}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return {"not": _translate_bool_or_leaf(node.operand, spec)}
    return _translate_comparison_like_node(node, spec)


def _translate_top_level(node: ast.AST, spec: ObjectTypeSpec) -> List[dict]:
    """The whole expression, translated to `parse_expr`'s public shape: a
    list of nodes, implicitly AND'd together.

    A top-level `{"and": [...]}` -- i.e. any expression that doesn't use
    `or`/`not` at all, including a single bare comparison -- is unwrapped
    back into a flat list here, so the wire shape for those expressions is
    exactly what it was before `or`/`not` support existed. An expression
    that does use them produces a list containing an `{"or": [...]}"`/
    `{"not": node}` node (alongside plain leaves too, e.g. `"(a or b) and
    c"` -> `[{"or": [a, b]}, c]`), rather than changing the top-level shape
    from a list to something else.
    """
    translated = _translate_bool_or_leaf(node, spec)
    if isinstance(translated, dict) and tuple(translated) == ("and",):
        return translated["and"]
    return [translated]


def parse_expr_for_spec(spec: ObjectTypeSpec, expr: str) -> List[dict]:
    """Parse an "almost Python" expression against an already-known `ObjectTypeSpec`.

    For callers that already know their target type and don't need (or
    want) a namespace string -- e.g. `resources/object_query.py`'s
    `where_expr` field, where each endpoint's own `self.spec` already fixes
    the type; asking the client to also name it via a namespace string would
    be redundant. `parse_expr()` below is the namespace-string-based
    equivalent, for standalone/library use where there's no such context.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as error:
        raise QueryLangError(f"invalid syntax: {error}") from error
    return _translate_top_level(tree.body, spec)


def parse_expr(namespace: str, expr: str) -> List[dict]:
    """Parse an "almost Python" expression into a `where` condition list.

    >>> parse_expr("person", "gender == 1")
    [{'column': 'gender', 'op': 'eq', 'value': 1}]

    >>> parse_expr("person", "primary_name.surname_list[0].surname == 'Smith'")
    [{'column': {'json_path': ['primary_name', 'surname_list', 0, 'surname']}, 'op': 'eq', 'value': 'Smith'}]

    The result is ready to drop directly into a `POST .../query/` request
    body's `"where"` field. Raises `QueryLangError` on anything outside the
    supported grammar -- never executes the input (`ast.parse` only, no
    `eval`/`compile`/`exec`).
    """
    spec = resolve_namespace(namespace)
    return parse_expr_for_spec(spec, expr)


# --- expr -> query.py AST ----------------------------------------------------
#
# `parse_expr`/`parse_expr_for_spec` stop at the JSON wire shape, since that's
# all `object_query.py`'s `where_expr` request field ever needs. A caller that
# wants to actually *run* a where-expression string (this module's tests, the
# docs, or any standalone/library use with a real `db` connection) needs that
# JSON turned into `query.py`'s `Eq`/`And`/`RelatedObject`-based AST instead --
# `compile_expr`/`compile_expr_for_spec` are that bridge, built entirely out of
# `query.py`'s own exported pieces (no new AST shape of its own).

_OP_CLASSES: dict[str, type] = {
    "eq": Eq,
    "ne": Ne,
    "lt": Lt,
    "lte": Lte,
    "gt": Gt,
    "gte": Gte,
}


def _json_column_to_ref(column: Union[str, dict], spec: ObjectTypeSpec) -> ColumnRef:
    """A wire-format column reference (plain string, `{"json_path": [...]}`,
    or `{"count_of": {...}}`), resolved to a `ColumnRef` -- via
    `resolve_column_path`, so a path crossing a relationship
    (`{"json_path": ["birth", "date", "sortval"]}`) becomes a `RelatedObject`
    the same way it would coming from `object_query.py`, not a literal
    `JsonPath(("birth", "date", "sortval"))` that would (harmlessly, but
    incorrectly) look for a `birth` key inside `json_data` instead.
    `{"count_of": {"relationship": ..., "where": [...]}}` resolves to a
    `CollectionCount` the same way `_node_from_json`'s `"exists"` case
    resolves to an `Exists` -- same `resolve_collection` lookup, same
    recursive `_where_list_to_ast` for the optional condition.
    """
    if isinstance(column, str):
        return column
    if "count_of" in column:
        payload = column["count_of"]
        collection = resolve_collection(spec, payload["relationship"])
        condition = (
            _where_list_to_ast(payload["where"], collection.target)
            if "where" in payload
            else None
        )
        return CollectionCount(collection, condition)
    return resolve_column_path(spec, column["json_path"])


def _condition_from_json(condition: dict, spec: ObjectTypeSpec) -> Any:
    """One `parse_expr`-shaped condition dict, translated to a `query.py`
    comparison object (`Eq`, `Lt`, `In`, `Like`, `Contains`, ...)."""
    column = _json_column_to_ref(condition["column"], spec)
    op = condition["op"]
    if op == "in":
        return In(column, condition["value"])
    if op == "like":
        return Like(column, condition["value"])
    if "value_column" in condition:
        # Field-vs-field, e.g. "mother.death.date.sortval < father.death.date.sortval",
        # or (for "contains") "other_field in field".
        value = _json_column_to_ref(condition["value_column"], spec)
        if isinstance(value, str):
            # A flat (same-table) column resolves to a bare str here --
            # identical in shape to an ordinary literal, which
            # Comparison/Contains would otherwise (silently, wrongly) treat
            # this as. Wrap it so it's unambiguously "a field", the same
            # way a JsonPath/RelatedObject already unambiguously is -- see
            # FlatColumnRef's docstring.
            value = FlatColumnRef(value)
    else:
        value = condition["value"]
    if op == "contains":
        return Contains(column, value)
    return _OP_CLASSES[op](column, value)


def _where_list_to_ast(conditions: List[dict], spec: ObjectTypeSpec) -> Any:
    """A `parse_expr`-shaped list of top-level conditions (implicitly AND'd),
    translated to a single `query.py` boolean expression -- shared by
    `compile_expr_for_spec` and `_node_from_json`'s `"exists"` case, whose
    `where` payload is exactly this same shape, just against the collection's
    target type instead of the outer spec.
    """
    asts = [_node_from_json(condition, spec) for condition in conditions]
    return asts[0] if len(asts) == 1 else And(*asts)


def _node_from_json(node: dict, spec: ObjectTypeSpec) -> Any:
    """One `parse_expr`-shaped node -- a leaf condition, or an `{"and"/"or":
    [...]}`/`{"not": node}`/`{"exists": {...}}` combinator -- translated to a
    `query.py` boolean expression (`Eq`/`Lt`/`In`/... for a leaf, `And`/`Or`/
    `Not`/`Exists` for a combinator), recursing into each child the same way.
    """
    if "and" in node:
        return And(*(_node_from_json(child, spec) for child in node["and"]))
    if "or" in node:
        return Or(*(_node_from_json(child, spec) for child in node["or"]))
    if "not" in node:
        return Not(_node_from_json(node["not"], spec))
    if "exists" in node:
        payload = node["exists"]
        collection = resolve_collection(spec, payload["relationship"])
        condition = (
            _where_list_to_ast(payload["where"], collection.target)
            if "where" in payload
            else None
        )
        return Exists(collection, condition)
    return _condition_from_json(node, spec)


def compile_expr_for_spec(spec: ObjectTypeSpec, expr: str) -> Any:
    """Parse and translate a where-expression string into a `query.py` `where`
    AST (a single comparison, or an `And`/`Or` tree of them), ready for
    `compile_query`/`compile_count_query`. For callers that already have
    `spec` -- see `parse_expr_for_spec`.
    """
    conditions = parse_expr_for_spec(spec, expr)
    return _where_list_to_ast(conditions, spec)


def compile_expr(namespace: str, expr: str) -> Tuple[ObjectTypeSpec, Any]:
    """Parse and translate a where-expression string into `(spec, where)`,
    ready to pass straight to `compile_query(spec, Query(where=where), ...)`.

    >>> spec, where = compile_expr("person", "gender == 1")
    >>> where
    Eq('gender', 1)

    Field-vs-field paths on both sides of a comparison work the same way
    `object_query.py` resolves them -- e.g. `"mother.death.date.sortval <
    father.death.date.sortval"` becomes `Lt(RelatedObject(...), RelatedObject(...))`.
    """
    spec = resolve_namespace(namespace)
    return spec, compile_expr_for_spec(spec, expr)
