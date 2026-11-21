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

"""Completion for a `where_expr` being typed: what could go at the cursor.

`complete("Person", "birth.date.so", 13)` returns `sortval` (a field), and
`complete("Person", "gender == Person.", 17)` returns `FEMALE`, `MALE`,
`UNKNOWN`. This never parses the whole expression -- a half-typed one
rarely parses -- it only looks at the dotted path ending at the cursor:

- No dot: what fits the grammar position, judged from the token before
  the word. At the start of a condition (or after `(`, `,`, `and`, `or`,
  `not`, `if`): the namespace's names (columns, fields, relationships,
  collections, comprehension variables), functions, `not`, `Env`. After
  a complete operand: comparisons (on an empty word), `and`/`or`/`in`/
  `is`/`not`, and `for`/`if` inside parentheses. After a comparison or
  `in`/`is`: a value -- names, `None`/`True`/`False`, `Date`, constant
  classes, `Env` (and `not` after `is`). After `for`: nothing. When
  the field compared with holds a class's constants (`type.value ==`,
  `gender in [`), only that class (`EventType.`), and after its dot only
  the constants that fit (`date.modifier == Date.` -> `MOD_*`). A
  true/false field gets `True`/`False`; a text field gets `"` (names
  once a letter is typed).
- `Env` only when the caller passes `env` names.
- A half-typed comparison (`gender <`): the operators it can become.
- `ClassName.`: that class's constants.
- `Env.`: the `env` names the caller passes in.
- Any other path: walked one segment at a time through relationships
  (`birth.` -> the Event's names) and JSON fields
  (`primary_name.surname_list[0].` -> a Surname's fields). A comprehension
  variable starts from what it iterates over (`e.` in `any(... for e in
  events)` -> an Event's names).
- Inside a string literal, or after anything the walk can't follow:
  nothing.

All the logic runs over `vocabulary.vocabularies()`'s plain JSON data
(`complete_from_vocabularies`), so a client without Python can load that
JSON and port this module's few functions as they are;
`tests/completion_cases.json` holds cases both implementations can run.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .query_lang import resolve_namespace
from .vocabulary import vocabularies

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_SEGMENT = rf"{_NAME}(?:\[[0-9]+\])*"
_CHAIN = rf"{_SEGMENT}(?:\.{_SEGMENT})*"
# The dotted path ending at the cursor: `chain.` then a (possibly empty)
# word, or just a word. The lookbehind keeps a chain from starting partway
# through a name, number or `x[0]`/`f()` -- `1.5` or `f().x` don't
# complete.
_AT_CURSOR_RE = re.compile(rf"(?<![A-Za-z0-9_.\])])(?:({_CHAIN})\.)?({_NAME})?$")
_SEGMENT_RE = re.compile(rf"({_NAME})((?:\[[0-9]+\])*)")
_INDEX_RE = re.compile(r"\[([0-9]+)\]")
_BINDING_RE = re.compile(rf"\bfor\s+({_NAME})\s+in\s+({_CHAIN})")
_PARTIAL_OPERATOR_RE = re.compile(r"[=!<>]+$")
# The path a value is being written for: `path ==` (any comparison) or
# `path in [..., ` (a list of values, `not in` too).
_COMPARED_RE = re.compile(rf"(?<![A-Za-z0-9_.\])])({_CHAIN})\s*(?:==|!=|<=|>=|<|>)\s*$")
_LISTED_RE = re.compile(rf"(?<![A-Za-z0-9_.\])])({_CHAIN})\s+(?:not\s+)?in\s*\[[^\[\]]*$")
_INDEXED_RE = re.compile(rf"(?<![A-Za-z0-9_.\])])({_CHAIN})\[$")
_TOKEN_RE = re.compile(
    r"""\s+"""
    r"""|(?P<string>'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")"""
    r"""|(?P<number>[0-9]+(?:\.[0-9]+)?)"""
    rf"|(?P<name>{_NAME})"
    r"|(?P<operator>==|!=|<=|>=|<|>)"
    r"|(?P<other>.)",
    re.S,
)

# What the grammar expects at the cursor, from the tokens before the word
# being typed (see `_expected`):
# - "operand": the start of a condition -- a name, a function, `not`.
# - "operator": what follows a complete operand -- a comparison, `in`/
#   `is`/`not in`, `and`/`or`, and `for`/`if` inside `any(...)`/`[...]`.
# - "value": the right-hand side of a comparison -- a name, a literal,
#   `Date(...)`, `ClassName.CONST`, `Env.NAME`.
# - "is_value": after `is`, a value or `not`.
# - "not_in": after `operand not`, only `in`.
# - "in_list": after `path in` -- a `[...]` list or an `Env` list.
# - "path": a field path only (`like(`'s first argument, after `'text' in`).
# - "string": a quoted string only (`Date(`, `like(path, `).
# - "collection": a collection name only (`exists(`, `count(`).
# - "iterable": something to iterate over (`for e in`, `len(`) --
#   collections, relationships, list fields, variables; `[` for `len(`.
# - "literal": an element of a `path in [...]` list.
# - "index": inside `path[` -- an integer index, offered as `0` when
#   `path` is a list field.
# - "nothing": after `for` (a new variable's name), where only `,`/`)`
#   can come next, or anything unexpected.
_OPERAND_AFTER = {"and", "or", "not", "if"}
_COMPARISONS = {"==", "!=", "<", "<=", ">", ">="}
_LITERALS = ("None", "True", "False")
_CONTINUATIONS = ("and", "or", "in", "is", "not")
_COMPREHENSION_WORDS = ("for", "if")
_VALUE_FUNCTIONS = ("Date",)
_NON_OPERAND_WORDS = {"and", "or", "not", "in", "is", "for", "if"}
# What each argument of a whitelisted function call is (see
# `query_lang.py`): `Date('Jan 1, 1968')`, `like(path, 'pattern')`,
# `exists(collection, condition)`, `len([... for x in collection])` or
# `len(path)`; `any(...)` takes a condition or comprehension. A function
# not listed here takes conditions.
_ARGUMENT_ROLES = {
    "Date": ("string",),
    "like": ("path", "string"),
    "regex": ("path", "string"),
    "exists": ("collection", "operand"),
    "count": ("collection", "operand"),
    "len": ("iterable",),
    "any": ("operand", "operand"),
}
# Roles whose argument is complete after one operand: only `,`/`)` follows.
_SINGLE_TOKEN_ROLES = {"string", "path", "collection", "iterable"}


def _tokens(text: str) -> List[Tuple[str, str]]:
    """`(kind, text)` for each token in `text`, whitespace dropped."""
    return [
        (match.lastgroup, match.group())
        for match in _TOKEN_RE.finditer(text)
        if match.lastgroup is not None
    ]


def _ends_operand(token: Optional[Tuple[str, str]]) -> bool:
    if token is None:
        return False
    kind, text = token
    if kind in ("string", "number"):
        return True
    if kind == "name":
        return text not in _NON_OPERAND_WORDS
    return text in (")", "]")


def _is_word(token: Optional[Tuple[str, str]], word: str) -> bool:
    return token is not None and token == ("name", word)


class _Frame:
    """One open `(` or `[`: which function it calls (if any), which
    argument the cursor is on, and whether it's a `path in [...]` list."""

    def __init__(
        self, bracket: str, function: Optional[str], values: bool, subscript: bool = False
    ):
        self.bracket = bracket
        self.function = function
        self.values = values
        self.subscript = subscript
        self.argument = 0

    def role(self) -> Optional[str]:
        if self.function is None:
            return None
        roles = _ARGUMENT_ROLES.get(self.function, ())
        return roles[self.argument] if self.argument < len(roles) else "nothing"


def _open_frames(tokens: List[Tuple[str, str]]) -> List[_Frame]:
    """The brackets still open at the end of `tokens`, outermost first."""
    frames: List[_Frame] = []
    for index, (kind, text) in enumerate(tokens):
        if kind != "other":
            continue
        previous = tokens[index - 1] if index else None
        if text == "(":
            function = previous[1] if previous and previous[0] == "name" and previous[1] not in _NON_OPERAND_WORDS else None
            frames.append(_Frame("(", function, False))
        elif text == "[":
            # `path[` indexes a list; `in [` starts a list of values; any
            # other `[` starts a comprehension (`len([... for ...])`).
            frames.append(
                _Frame("[", None, _is_word(previous, "in"), subscript=_ends_operand(previous))
            )
        elif text in ")]" and frames:
            frames.pop()
        elif text == "," and frames:
            frames[-1].argument += 1
    return frames


def _expected(text: str) -> Tuple[str, bool]:
    """What the grammar expects after `text` (see the table above), and
    whether a comprehension's `for`/`if` could come there."""
    tokens = _tokens(text)
    frames = _open_frames(tokens)
    comprehension = any(
        frame.function == "any"
        or (frame.bracket == "[" and not frame.values and not frame.subscript)
        for frame in frames
    )
    top = frames[-1] if frames else None
    if not tokens:
        return "operand", comprehension
    last = tokens[-1]
    previous = tokens[-2] if len(tokens) > 1 else None
    before_previous = tokens[-3] if len(tokens) > 2 else None
    if last[0] == "other" and last[1] in "([,":
        if top is None:
            return "nothing", comprehension
        if top.subscript:
            return ("index" if last[1] == "[" else "nothing"), comprehension
        if top.values:
            return "literal", comprehension
        if top.bracket == "(" and top.function is not None:
            return top.role() or "operand", comprehension
        return ("operand" if top.argument == 0 else "nothing"), comprehension
    if _is_word(last, "not"):
        if _ends_operand(previous):
            return "not_in", comprehension
        if _is_word(previous, "is"):
            return "value", comprehension
        return "operand", comprehension
    if _is_word(last, "is"):
        return "is_value", comprehension
    if _is_word(last, "for"):
        return "nothing", comprehension
    if _is_word(last, "in"):
        if _is_word(before_previous, "for"):
            return "iterable", comprehension
        if previous is not None and previous[0] == "string":
            return "path", comprehension
        return "in_list", comprehension
    if last[1] in _OPERAND_AFTER and last[0] == "name":
        return "operand", comprehension
    if last[0] == "operator":
        return "value", comprehension
    if _ends_operand(last):
        if top is not None and (
            top.values or top.subscript or top.role() in _SINGLE_TOKEN_ROLES
        ):
            return "nothing", comprehension
        return "operator", comprehension
    return "nothing", comprehension


def _in_string(text: str) -> bool:
    """Whether `text` ends inside an unterminated `'...'`/`"..."` literal."""
    quote = None
    escaped = False
    for char in text:
        if quote is None:
            if char in "'\"":
                quote = char
        elif escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == quote:
            quote = None
    return quote is not None


def _segments(chain: str) -> List[Tuple[str, int]]:
    """`"a.b[0][1].c"` -> `[("a", 0), ("b", 2), ("c", 0)]`: each name and
    how many `[n]` indexes follow it."""
    return [
        (match.group(1), len(_INDEX_RE.findall(match.group(2))))
        for match in _SEGMENT_RE.finditer(chain)
    ]


# A walk's position: ("namespace", "Person") at a record (top level, or
# across a relationship), or ("field", node) inside its JSON fields.
_Context = Tuple[str, Any]


def _step(vocabs: Dict[str, Any], context: _Context, name: str) -> Optional[_Context]:
    kind, value = context
    if kind == "namespace":
        vocab = vocabs[value]
        if name in vocab["relationships"]:
            return ("namespace", vocab["relationships"][name])
        if name in vocab["fields"]:
            return ("field", vocab["fields"][name])
        if name in vocab["columns"]:
            return ("field", {"type": vocab["column_types"].get(name)})
        return None
    node = value.get("fields", {}).get(name)
    return None if node is None else ("field", node)


def _index(context: _Context, count: int) -> Optional[_Context]:
    for _ in range(count):
        kind, node = context
        if kind != "field" or "items" not in node:
            return None
        context = ("field", node["items"])
    return context


def _step_indexed(
    vocabs: Dict[str, Any], context: _Context, name: str, indexes: int
) -> Optional[_Context]:
    """`_step` then `_index`, except that an indexed collection on a record
    (`parent_families[0]`) steps to the record that entry links to, the
    way a relationship does."""
    if context[0] == "namespace" and indexes:
        target = vocabs[context[1]]["collections"].get(name)
        if target is not None:
            return _index(("namespace", target), indexes - 1)
    context = _step(vocabs, context, name)
    return context and _index(context, indexes)


def _indexable_collections(vocabs: Dict[str, Any], context: _Context) -> List[Tuple[str, str, Any]]:
    """The collections an index can follow from a record (`families`,
    `citations`, ...; not `backlinks` or `child_refs`)."""
    if context[0] != "namespace":
        return []
    return [
        (name, "collection", target)
        for name, target in vocabs[context[1]]["collections"].items()
        if target is not None
    ]


def _walk(
    vocabs: Dict[str, Any],
    root: str,
    segments: List[Tuple[str, int]],
    bindings: Dict[str, Optional[_Context]],
    iterating: bool = False,
) -> Optional[_Context]:
    """The context `segments` lead to from namespace `root`, or `None`.

    A first segment naming a comprehension variable starts from what that
    variable iterates over. With `iterating`, the path is a comprehension's
    `in ...` source, so it may be a bare collection (`events`), which
    yields an element of the collection's target, or end at a JSON array
    (`father.attribute_list`), which yields one of its items. A collection
    can't be reached across a relationship (`mother.events`) -- GOQL
    rejects that. An indexed one can (`mother.families[0]`): like a
    relationship, it leads to the record that entry links to.
    """
    if not segments:
        return None
    (first, first_indexes), rest = segments[0], segments[1:]
    if first in bindings:
        context = _index(bindings[first], first_indexes) if bindings[first] else None
    else:
        context = None
        if iterating and not rest and not first_indexes:
            target = vocabs[root]["collections"].get(first, False)
            if target is not False:
                return None if target is None else ("namespace", target)
        context = _step_indexed(vocabs, ("namespace", root), first, first_indexes)
    for name, indexes in rest:
        if context is None:
            return None
        context = _step_indexed(vocabs, context, name, indexes)
    if iterating and context and context[0] == "field":
        return _index(context, 1)
    return context


def _bindings(vocabs: Dict[str, Any], root: str, source: str) -> Dict[str, Optional[_Context]]:
    """Comprehension variables in `source` (`for e in events`) and what each
    iterates over, resolved in source order so a later one can iterate
    over an earlier one's list field (`for s in n.surname_list`). `None` for a variable
    whose source can't be followed (`backlinks`, a typo) -- still a
    variable, but with nothing to offer after its dot."""
    bindings: Dict[str, Optional[_Context]] = {}
    for match in _BINDING_RE.finditer(source):
        bindings[match.group(1)] = _walk(
            vocabs, root, _segments(match.group(2)), bindings, iterating=True
        )
    return bindings


def _names_at(vocabs: Dict[str, Any], context: _Context) -> List[Tuple[str, str, Any]]:
    """`(name, kind, detail)` for everything that can follow `context.`."""
    kind, value = context
    if kind == "field":
        return [
            (name, "field", node.get("type"))
            for name, node in value.get("fields", {}).items()
        ]
    vocab = vocabs[value]
    # Relationships first: a name that's both (an Event's `place`, a
    # Place's `enclosed_by`) is reported as the relationship, since that's
    # what a following `.` walks into.
    return (
        [(name, "relationship", target) for name, target in vocab["relationships"].items()]
        + [(name, "column", None) for name in vocab["columns"]]
        + [(name, "field", node.get("type")) for name, node in vocab["fields"].items()]
    )


def _is_array(field_type: Any) -> bool:
    return _has_type(field_type, "array")


def _compared_field(
    vocabs: Dict[str, Any],
    namespace: str,
    text: str,
    bindings: Dict[str, Optional[_Context]],
) -> Optional[Dict[str, Any]]:
    """The field node a value being written at the end of `text` is
    compared with (`surname ==`, `type.value in [...`), or `None`."""
    match = _COMPARED_RE.search(text) or _LISTED_RE.search(text)
    if match is None:
        return None
    context = _walk(vocabs, namespace, _segments(match.group(1)), bindings)
    if context is None or context[0] != "field":
        return None
    return context[1]


def _value_constants(
    vocabs: Dict[str, Any],
    namespace: str,
    text: str,
    bindings: Dict[str, Optional[_Context]],
) -> Optional[Dict[str, str]]:
    """`{"class", "prefix"}` for the constants the value being written at
    the end of `text` takes -- from the field it's compared with
    (`type.value ==` -> `EventType`) -- or `None`."""
    field = _compared_field(vocabs, namespace, text, bindings)
    return None if field is None else field.get("constants")


def _has_type(field_type: Any, wanted: str) -> bool:
    return field_type == wanted or (isinstance(field_type, list) and wanted in field_type)


def _continuation(
    name: str, kind: str, detail: Any, binding: Optional[_Context], env: List[str]
) -> str:
    """What can directly follow a complete name: `.` before a record's or
    an object's fields (or a class's constants, or `Env`'s names), `[`
    before a list's index, `(` after a function, a space after a keyword
    or operator, or nothing (a literal, a plain value)."""
    if kind in ("relationship", "constant_class"):
        return "."
    if kind == "function":
        return "("
    if kind == "operator" or (kind == "keyword" and name not in _LITERALS):
        return " "
    if kind == "env":
        return "." if env else ""
    if kind == "field":
        if _has_type(detail, "object"):
            return "."
        return "[" if _has_type(detail, "array") else ""
    if kind == "variable" and binding is not None:
        if binding[0] == "namespace" or binding[1].get("fields"):
            return "."
        return "[" if "items" in binding[1] else ""
    return ""


def complete_from_vocabularies(
    vocabs: Dict[str, Any],
    namespace: str,
    source: str,
    cursor: int,
    env: Iterable[str] = (),
) -> List[Dict[str, Any]]:
    """`complete()`, over already-loaded `vocabularies()` data.

    `namespace` is in class-name form (`"Person"`). Returns a list of
    `{"name", "kind", "detail", "start"}` dicts, sorted by name: replacing
    `source[start:cursor]` with `name` applies one. `kind` is one of
    `column`, `field`, `relationship`, `collection`, `keyword`,
    `function`, `operator`, `constant_class`, `constant`, `env`,
    `variable`, `punctuation` (`"` to start a string, `[` to start a
    list), `index` (`0` inside a list field's `[`); `detail` is a field's JSON type, a relationship's or
    collection's target namespace, `"text"`/`"list"` for punctuation,
    `"first item"` for an index, or `None`.

    A name typed out in full that can go on comes back with what
    continues it -- `primary_name.date` gives `date.`, `dateval` gives
    `dateval[`, `birth` gives `birth.`, `any` gives `any(`, `and` gives
    `and `, `==` gives `== ` -- so a client can tell there's more without
    the user guessing at the next character.
    """
    before = source[:cursor]
    if _in_string(before):
        return []
    vocab = vocabs[namespace]
    env = list(env)
    # `Env` is offered only where the caller will supply values for it.
    env_names = [(vocab["env"], "env", None)] if env else []
    partial = _PARTIAL_OPERATOR_RE.search(before)
    if partial:
        # A half-typed comparison (`gender <`): the operators it can become.
        typed = partial.group()
        start = cursor - len(typed)
        if _expected(before[:start])[0] != "operator":
            return []
        return [
            {
                "name": name + (" " if name == typed else ""),
                "kind": "operator",
                "detail": None,
                "start": start,
            }
            for name in sorted(vocab["operators"])
            if name.startswith(typed)
        ]
    match = _AT_CURSOR_RE.search(before)
    if match is None:
        return []
    chain, prefix = match.group(1), match.group(2) or ""
    start = cursor - len(prefix)
    bindings = _bindings(vocabs, namespace, source)

    candidates: List[Tuple[str, str, Any]]
    if chain is None:
        expected, comprehension = _expected(before[:start])
        paths = _names_at(vocabs, ("namespace", namespace)) + [
            (name, "variable", None) for name in bindings
        ]
        collections = [
            (name, "collection", target) for name, target in vocab["collections"].items()
        ]
        names = paths + collections
        compared = (
            _compared_field(vocabs, namespace, before[:start], bindings)
            if expected in ("value", "literal")
            else None
        )
        compared_type = None if compared is None else compared.get("type")
        if compared is not None and compared.get("constants"):
            # The field compared with holds one class's constants
            # (`type.value ==` -> `EventType.`): offer just that class.
            candidates = [(compared["constants"]["class"] + ".", "constant_class", None)] + env_names
        elif _has_type(compared_type, "boolean") and not _has_type(compared_type, "string"):
            candidates = [("True", "keyword", None), ("False", "keyword", None)] + env_names
        elif _has_type(compared_type, "string") and not prefix:
            # Text is nearly always compared with a quoted string; a letter
            # typed first still gets names (`father.surname == mother.su`).
            candidates = [('"', "punctuation", "text")] + env_names
        elif expected == "operand":
            candidates = (
                names
                + [(name, "function", None) for name in vocab["functions"]]
                + [("not", "keyword", None)]
                + env_names
            )
        elif expected in ("value", "is_value"):
            candidates = (
                ([("not", "keyword", None)] if expected == "is_value" else [])
                + [(name, "keyword", None) for name in _LITERALS]
                + [(name, "function", None) for name in _VALUE_FUNCTIONS]
                + [(name, "constant_class", None) for name in vocab["constants"]]
                + env_names
                + names
            )
        elif expected == "operator":
            words = _CONTINUATIONS + (_COMPREHENSION_WORDS if comprehension else ())
            candidates = [(name, "keyword", None) for name in words]
            if not prefix:
                candidates += [(name, "operator", None) for name in vocab["operators"]]
        elif expected == "not_in":
            candidates = [("in", "keyword", None)]
        elif expected == "in_list":
            candidates = [("[", "punctuation", "list")] + env_names
        elif expected == "path":
            candidates = paths
        elif expected == "string":
            candidates = [('"', "punctuation", "text")]
        elif expected == "collection":
            candidates = collections
        elif expected == "iterable":
            iterables = [
                (name, kind, detail)
                for name, kind, detail in paths
                if kind in ("relationship", "variable") or _is_array(detail)
            ]
            candidates = iterables + collections
            if before[:start].rstrip().endswith("len("):
                candidates.append(("[", "punctuation", "list"))
        elif expected == "index":
            candidates = []
            indexed = _INDEXED_RE.search(before[:start])
            if indexed:
                listed = _segments(indexed.group(1))
                context = _walk(vocabs, namespace, listed, bindings)
                if context is not None and context[0] == "field" and "items" in context[1]:
                    candidates = [("0", "index", "first item")]
                elif not listed[-1][1]:
                    # `parent_families[`: a collection's first entry.
                    owner = (
                        _walk(vocabs, namespace, listed[:-1], bindings)
                        if len(listed) > 1
                        else ("namespace", namespace)
                    )
                    if owner is not None and any(
                        name == listed[-1][0] for name, _kind, _target in _indexable_collections(vocabs, owner)
                    ):
                        candidates = [("0", "index", "first item")]
        elif expected == "literal":
            candidates = (
                [('"', "punctuation", "text")]
                + [(name, "keyword", None) for name in _LITERALS]
                + [(name, "constant_class", None) for name in vocab["constants"]]
                + env_names
            )
        else:
            candidates = []
    else:
        segments = _segments(chain)
        first, first_indexes = segments[0]
        single = len(segments) == 1 and not first_indexes
        if single and first not in bindings and first in vocab["constants"]:
            names = vocab["constants"][first]
            # After `date.modifier == Date.`, only the `MOD_` constants.
            chain_start = start - len(chain) - 1
            wanted = _value_constants(vocabs, namespace, before[:chain_start], bindings)
            if wanted is not None and wanted["class"] == first:
                names = [name for name in names if name.startswith(wanted["prefix"])]
            candidates = [(name, "constant", first) for name in names]
        elif single and first not in bindings and first == vocab["env"]:
            candidates = [(name, "env", None) for name in env]
        else:
            # What the grammar expects where the dotted path began decides
            # which of the walked-to names fit: anything for a condition or
            # value, only steps toward a list for an iterable, nothing where
            # a string, a bare collection or a list literal belongs.
            chain_start = start - len(chain) - 1
            role = _expected(before[:chain_start])[0]
            context = _walk(vocabs, namespace, segments, bindings)
            candidates = [] if context is None else _names_at(vocabs, context)
            if context is not None and role in ("operand", "value", "is_value"):
                # `father.families[0]...`: one entry of a collection, across a hop.
                candidates += _indexable_collections(vocabs, context)
            if role == "iterable":
                candidates = [
                    (name, kind, detail)
                    for name, kind, detail in candidates
                    if kind == "relationship" or _is_array(detail)
                ]
            elif role not in ("operand", "value", "is_value", "path"):
                candidates = []

    seen = set()
    results = []
    for name, kind, detail in candidates:
        if not name.startswith(prefix):
            continue
        if name == prefix:
            # Already typed in full: offer what continues it instead, so
            # Tab shows there's more (`primary_name.date` -> `date.`,
            # `any` -> `any(`). Deduplicated after this, so a name that's
            # two things keeps both continuations (`Date(` and `Date.`).
            name += _continuation(name, kind, detail, bindings.get(name), env)
        if name in seen:
            continue
        seen.add(name)
        results.append({"name": name, "kind": kind, "detail": detail, "start": start})
    results.sort(key=lambda item: (item["name"].lower(), item["name"]))
    return results


@lru_cache(maxsize=1)
def _cached_vocabularies() -> Dict[str, Any]:
    return vocabularies()


def complete(
    namespace: str, source: str, cursor: int, env: Iterable[str] = ()
) -> List[Dict[str, Any]]:
    """What could go at character offset `cursor` in the `where_expr`
    `source`, written against `namespace` (`"person"` or `"Person"`, ...).

    `env` lists the `Env.NAME` names the caller will supply, if any. See
    `complete_from_vocabularies` for the result's shape. Raises
    `QueryLangError` for an unknown namespace.
    """
    spec = resolve_namespace(namespace)
    return complete_from_vocabularies(
        _cached_vocabularies(), spec.cls.__name__, source, cursor, env
    )
