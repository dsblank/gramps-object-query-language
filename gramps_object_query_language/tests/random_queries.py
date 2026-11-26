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

"""Random `where_expr`s and value expressions, for `test_random_parity`.

`QueryGenerator` writes expressions the way a user would, as text, from
the fields `vocabulary()` lists: comparisons of every operator against
values taken from the tree itself, `is None`, `in`/`not in`, substring
`in`, `like`, `regex`, type names, class constants, `Date(...)`,
field-vs-field comparisons, arithmetic, `len(...)` and `distinct(...)`,
indexed and picked entries, `attribute_list['...']`, `any(...)` over
collections and lists inside the record (nested once), all combined with
`and`/`or`/`not`. Some of what it writes is rejected by the parser (a
type mismatch, say); the caller skips those. What matters is that every
expression the parser accepts means the same on every backend.

Run it directly to hunt for disagreements in bulk:

    python -m gramps_object_query_language.tests.random_queries --count 5000
"""

import json
import random
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

from gramps_object_query_language.vocabulary import vocabulary

# A leaf is (expression text, kind, node) with kind one of "string",
# "integer", "boolean", "type" (a Gramps type object, compared by name).
Leaf = Tuple[str, str, Dict[str, Any]]

COMPARE = ["==", "!=", "<", "<=", ">", ">="]

#: Disagreements already known and not yet fixed, which the generator
#: steers around so that new ones stand out. Remove an entry once its fix
#: lands, and the generator writes those expressions again.
KNOWN_ISSUES = {
    # PostgreSQL only, from here down.
    # A number field that can hold JSON `null` (`date.format`): "cannot
    # cast jsonb null to type numeric".
    "pg-json-null-number",
    # An integer or boolean column compared with a JSON field
    # (`death_ref_index != birth.date.modifier`, `private !=
    # citations[0].private`): "operator does not exist: integer = text".
    "pg-column-vs-json",
    # Arithmetic on an INTEGER column stays 32-bit (`change * 2`,
    # `change + change`): "integer out of range".
    "pg-integer-overflow",
    # `dateval[n]` selected comes back as a string ('0'), not a number.
    "pg-dateval-select-type",
}
ARITH = ["+", "-", "*", "/", "//"]
DATES = ["Jan 1, 1900", "1850", "today", "about 1900", "Dec 31, 1999"]
# Fields Gramps keeps but the random tree never fills in.
SKIPPED_FIELDS = {"lds_ord_list", "thumb", "alt_loc", "rect"}
# Regex pieces both Python's `re` and PostgreSQL read the same way.
REGEX_META = ["^", "$", ".", ".*", "[a-z]", "\\d", "(a|o)", "+", "?"]


def _kind(node: Dict[str, Any]) -> Optional[str]:
    if node.get("type_names"):
        return "type"
    kind = node.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), None)
    return kind if kind in ("string", "integer", "boolean", "number") else None


def _nullable_number(node: Dict[str, Any]) -> bool:
    kind = node.get("type")
    return isinstance(kind, list) and "null" in kind and _kind(node) in ("integer", "number")


def _leaves(fields: Dict[str, Any], prefix: str, depth: int) -> Iterator[Leaf]:
    """Every scalar reachable from `fields`, through objects and the first
    two entries of an array, `depth` levels deep."""
    for name, node in fields.items():
        if name in SKIPPED_FIELDS:
            continue
        path = f"{prefix}{name}"
        kind = _kind(node)
        if kind:
            yield path, kind, node
        if depth <= 0:
            continue
        if node.get("fields"):
            yield from _leaves(node["fields"], f"{path}.", depth - 1)
        items = node.get("items")
        if items:
            for index in (0, 1):
                item_kind = _kind(items)
                if item_kind:
                    yield f"{path}[{index}]", item_kind, items
                if items.get("fields"):
                    yield from _leaves(items["fields"], f"{path}[{index}].", depth - 1)


def _struct_arrays(fields: Dict[str, Any], prefix: str) -> List[Tuple[str, Dict[str, Any]]]:
    """Lists of structs directly on `fields` (`alternate_names`,
    `attribute_list`, ...) and one object level down
    (`primary_name.surname_list`): what a comprehension can walk."""
    out = []
    for name, node in fields.items():
        items = node.get("items") or {}
        if items.get("fields"):
            out.append((f"{prefix}{name}", items["fields"]))
        if node.get("fields"):
            for sub, subnode in node["fields"].items():
                subitems = subnode.get("items") or {}
                if subitems.get("fields"):
                    out.append((f"{prefix}{name}.{sub}", subitems["fields"]))
    return out


def _value_arrays(fields: Dict[str, Any], prefix: str) -> List[str]:
    """Every array directly on `fields`, for `len(path)`."""
    return [f"{prefix}{name}" for name, node in fields.items() if node.get("type") == "array"]


class Pools:
    """Literal values worth comparing against: every string and number in
    the tree, plus edge cases, so comparisons both match and miss."""

    def __init__(self, strings: List[str], integers: List[int], by_field: Dict[str, List[Any]]):
        self.strings = sorted(set(strings) | {"", "%", "_", "Smith", "smith", "zzz"})
        self.integers = sorted(set(integers) | {0, 1, 2, -1, 3})
        #: `{field name: [value, ...]}`, what each field actually holds.
        self.by_field = {name: sorted(set(values), key=repr) for name, values in by_field.items()}

    @classmethod
    def from_db(cls, db) -> "Pools":
        strings: List[str] = []
        integers: List[int] = []
        by_field: Dict[str, List[Any]] = {}

        def walk(value: Any, name: str) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    walk(item, key)
            elif isinstance(value, list):
                for item in value:
                    walk(item, name)
            elif isinstance(value, bool) or value is None:
                return
            elif isinstance(value, int):
                integers.append(value)
                by_field.setdefault(name, []).append(value)
            elif isinstance(value, str) and len(value) < 60:
                strings.append(value)
                by_field.setdefault(name, []).append(value)

        for table in ("person", "family", "event", "place", "source", "citation",
                      "media", "note", "repository", "tag"):
            db.dbapi.execute(f"SELECT json_data FROM {table}")
            for (data,) in db.dbapi.fetchall():
                walk(json.loads(data), table)
        for table, columns in (("person", ("given_name", "surname")), ("place", ("enclosed_by",))):
            db.dbapi.execute(f"SELECT {', '.join(columns)} FROM {table}")
            for row in db.dbapi.fetchall():
                for column, value in zip(columns, row):
                    if value is not None:
                        by_field.setdefault(column, []).append(value)
        return cls(strings, integers, by_field)


class QueryGenerator:
    """Random expressions for one namespace. `generate()` gives a
    `where_expr`; `value()` gives something to `select`."""

    def __init__(self, namespace: str, pools: Pools, rng: random.Random, avoid=KNOWN_ISSUES):
        self.avoid = set(avoid)
        self._selecting = False
        self.namespace = namespace
        self.pools = pools
        self.rng = rng
        self.vocab = vocabulary(namespace)
        self._vocabs = {namespace: self.vocab}
        self._vars = iter(f"v{i}" for i in range(10**6))

    # --- helpers -----------------------------------------------------------

    def _vocabulary(self, namespace: str) -> Dict[str, Any]:
        if namespace not in self._vocabs:
            self._vocabs[namespace] = vocabulary(namespace)
        return self._vocabs[namespace]

    def chance(self, p: float) -> bool:
        return self.rng.random() < p

    def pick(self, values: List[Any]) -> Any:
        return values[self.rng.randrange(len(values))]

    def _field_value(self, path: Optional[str], kind: type) -> Optional[Any]:
        """A value the field at `path` really holds, most of the time."""
        if path is None or self.chance(0.3):
            return None
        name = re.sub(r"\[.*?\]", "", path).rsplit(".", 1)[-1]
        values = [v for v in self.pools.by_field.get(name, []) if type(v) is kind]
        return self.pick(values) if values else None

    def _string(self, path: Optional[str] = None) -> str:
        value = self._field_value(path, str)
        if value is None:
            value = self.pick(self.pools.strings)
        if self.chance(0.25):  # `==` is exact, `<` ignores case: test both
            value = self.pick([value.upper(), value.lower(), value.swapcase()])
        return repr(value)

    def _integer(self, path: Optional[str] = None) -> str:
        value = self._field_value(path, int)
        return str(self.pick(self.pools.integers) if value is None else value)

    def pick_leaf(self, leaves: List[Leaf]) -> Leaf:
        """Short paths more often than long ones: each `.` or `[` halves
        the chance, so most comparisons read something that is there."""
        weights = [0.5 ** (path.count(".") + path.count("[")) for path, _k, _n in leaves]
        return self.rng.choices(leaves, weights)[0]

    # --- scopes: what can be named from here --------------------------------

    def _record_leaves(self, namespace: str, prefix: str, hops: int, indexes: bool = True) -> List[Leaf]:
        """Scalars of a record: its columns, its JSON fields, and the same
        again across a relationship (`birth.date.sortval`), an indexed
        collection (`parent_families[0].father.surname`) or an attribute by
        type (`attribute_list['Occupation'].value`)."""
        vocab = self._vocabulary(namespace)
        leaves: List[Leaf] = [
            (f"{prefix}{column}", kind, {"type": kind})
            for column, kind in vocab["column_types"].items()
        ]
        leaves += list(_leaves(vocab["fields"], prefix, 2))
        if hops > 0:
            for name, target in vocab["relationships"].items():
                leaves += self._record_leaves(target, f"{prefix}{name}.", hops - 1, indexes)
            if indexes:
                for name, target in vocab["collections"].items():
                    if target is not None:
                        index = self.pick([0, 0, 1])
                        leaves += self._record_leaves(target, f"{prefix}{name}[{index}].", hops - 1, False)
            if "attribute_list" in vocab["fields"]:
                type_name = self.pick(self.vocab["type_names"]["AttributeType"][:4] + ["Film", "Hair color"])
                leaves.append((f"{prefix}attribute_list[{type_name!r}].value", "string", {}))
        return leaves

    def _scope(self, depth: int):
        """The root record's scope."""
        return {
            "namespace": self.namespace,
            "prefix": "",
            "fields": self.vocab["fields"],
            "hops": 2,
            "depth": depth,
        }

    def _leaves_in(self, scope) -> List[Leaf]:
        if scope.get("namespace"):
            leaves = self._record_leaves(scope["namespace"], scope["prefix"], scope["hops"])
        else:
            leaves = list(_leaves(scope["fields"], scope["prefix"], 2))
        if "pg-json-null-number" in self.avoid:
            leaves = [l for l in leaves if not _nullable_number(l[2])]
        if self._selecting and "pg-dateval-select-type" in self.avoid:
            leaves = [l for l in leaves if "dateval[" not in l[0]]
        return leaves

    def _lists_in(self, scope) -> List[Tuple[str, Any]]:
        """`(list expression, scope of one entry)` for everything a
        comprehension can walk from `scope`."""
        out: List[Tuple[str, Any]] = []
        prefix = scope["prefix"]
        nested = bool(prefix)  # inside a comprehension: one attribute off the variable
        if scope.get("namespace"):
            vocab = self._vocabulary(scope["namespace"])
            for name, target in vocab["collections"].items():
                out.append((f"{prefix}{name}", ("collection", name, target)))
            if not nested:
                for name, target in vocab["relationships"].items():
                    for path, fields in _struct_arrays(self._vocabulary(target)["fields"], f"{name}."):
                        if "." not in path[len(name) + 1:]:
                            out.append((path, ("array", fields)))
        for path, fields in _struct_arrays(scope["fields"], prefix):
            if not nested or "." not in path[len(prefix):]:
                out.append((path, ("array", fields)))
        return out

    def _entry_scope(self, kind, var: str, depth: int):
        if kind[0] == "array":
            return {"prefix": f"{var}.", "fields": kind[1], "depth": depth}
        _, name, target = kind
        if target is None:
            return {"special": name, "var": var, "depth": depth}
        return {
            "namespace": target,
            "prefix": f"{var}.",
            "fields": self._vocabulary(target)["fields"],
            "hops": 1,
            "depth": depth,
        }

    # --- literals ---------------------------------------------------------

    def _literal_for(self, leaf: Leaf) -> str:
        path, kind, node = leaf
        if kind == "string":
            return self._string(path)
        if kind == "boolean":
            return self.pick(["True", "False"])
        if kind == "type":
            names = self.vocab["type_names"].get(node["type_names"], [])
            return repr(self.pick(names[:8] + ["Tax list", "Handfasting", "Census", "nonsense"]))
        constants = node.get("constants")
        if constants and self.chance(0.4):
            names = [
                c for c in self.vocab["constants"].get(constants["class"], [])
                if c.startswith(constants["prefix"])
            ]
            if names:
                return f"{constants['class']}.{self.pick(names)}"
        if path.endswith("sortval") and self.chance(0.4):
            return f"Date({self.pick(DATES)!r})"
        return self._integer(path)

    def _like_pattern(self, path: str) -> str:
        text = self._field_value(path, str)
        if text is None:
            text = self.pick(self.pools.strings)
        if text and self.chance(0.7):
            start = self.rng.randrange(len(text))
            text = text[start:start + self.rng.randrange(1, 5)]
        if self.chance(0.3):  # `like` and substring `in` ignore case
            text = self.pick([text.upper(), text.lower(), text.swapcase()])
        text = text.replace("\\", "\\\\")
        if self.chance(0.3):
            text = text.replace("%", "\\%").replace("_", "\\_")
        return repr(self.pick(["", "%"]) + text + self.pick(["", "%", "_%"]))

    def _regex_pattern(self, path: str) -> str:
        own = self._field_value(path, str)
        words = [w for w in ([own] if own else self.pools.strings) if re.fullmatch(r"[A-Za-z0-9 ]+", w)]
        parts = []
        for _ in range(self.rng.randrange(1, 4)):
            if self.chance(0.5) and words:
                word = self.pick(words)
                start = self.rng.randrange(len(word))
                parts.append(word[start:start + self.rng.randrange(1, 4)])
            else:
                meta = self.pick(REGEX_META)
                if meta in ("+", "?") and parts and parts[-1][-1:] in ("*", "+", "?"):
                    continue  # `.*+` is possessive in Python, an error in PostgreSQL
                parts.append(meta)
        return repr("".join(parts))

    # --- numbers ----------------------------------------------------------

    def number(self, scope, depth: int) -> str:
        """A numeric value: a field, a count, a picked entry or arithmetic."""
        choice = self.rng.randrange(10)
        if choice < 4 or depth <= 0:
            leaves = [l for l in self._leaves_in(scope) if l[1] in ("integer", "number")]
            if "pg-integer-overflow" in self.avoid:
                leaves = [l for l in leaves if not l[0].endswith("change")]
            if leaves:
                return self.pick_leaf(leaves)[0]
            if depth <= 0:
                return (not scope.get("special") and self.count(scope, 0)) or self._integer()
        if choice < 6 and not scope.get("special"):
            count = self.count(scope, depth - 1)
            if count:
                return count
        if choice < 7:
            pick = self.picked(scope, depth - 1, numeric=True)
            if pick:
                return pick
        op = self.pick(ARITH)
        left = self.number(scope, depth - 1)
        if op == "*" and "pg-integer-overflow" in self.avoid:
            right = str(self.rng.randint(-3, 3))
        else:
            right = self.number(scope, depth - 1) if self.chance(0.4) else self._integer()
        if self.chance(0.5):
            left, right = right, left
        return f"({left} {op} {right})"

    def count(self, scope, depth: int) -> Optional[str]:
        lists = self._lists_in(scope)
        if self.chance(0.3):
            fields = scope.get("fields") or {}
            arrays = _value_arrays(fields, scope["prefix"])
            if arrays:
                return f"len({self.pick(arrays)})"
        if not lists:
            return None
        path, kind = self.pick(lists)
        var = next(self._vars)
        entry = self._entry_scope(kind, var, depth)
        form = self.rng.randrange(4)
        if form == 0 or depth <= 0:
            return f"len({path})"
        condition = self.condition(entry, depth - 1)
        if form == 1 or entry.get("special"):
            return f"len([{var} for {var} in {path} if {condition}])"
        leaves = [l for l in self._leaves_in(entry) if l[1] != "type"]
        if kind[0] == "collection" and kind[2] and self.chance(0.4):
            hops = list(self._vocabulary(kind[2])["relationships"])
            if hops:
                leaves.append((f"{var}.{self.pick(hops)}", "record", {}))
        if not leaves:
            return f"len({path})"
        field = self.pick_leaf(leaves)[0]
        wrapped = f"[{field} for {var} in {path}" + (f" if {condition}]" if self.chance(0.5) else "]")
        if form == 3:
            return f"len(distinct({wrapped}))"
        return f"len({wrapped})"

    def picked(self, scope, depth: int, numeric: bool = False) -> Optional[str]:
        """`[x.f for x in list if cond][n]`."""
        lists = [l for l in self._lists_in(scope) if not (l[1][0] == "collection" and l[1][2] is None)]
        if not lists or depth <= 0:
            return None
        path, kind = self.pick(lists)
        var = next(self._vars)
        entry = self._entry_scope(kind, var, depth - 1)
        leaves = [
            l for l in self._leaves_in(entry)
            if (l[1] in ("integer", "number")) == numeric and l[1] != "type"
        ]
        if not leaves:
            return None
        field = self.pick_leaf(leaves)[0]
        condition = f" if {self.condition(entry, depth - 1)}" if self.chance(0.6) else ""
        return f"[{field} for {var} in {path}{condition}][{self.pick([0, 0, 1])}]"

    # --- conditions -------------------------------------------------------

    def generate(self, depth: int = 3) -> str:
        return self.condition(self._scope(depth), depth)

    def order_by(self) -> List[Tuple[str, str]]:
        """One or two sort columns, `(path, "asc" | "desc")`: text more
        often than not, since that's where backends sort differently."""
        self._selecting = True
        try:
            leaves = [l for l in self._leaves_in(self._scope(0)) if l[1] in ("string", "integer", "boolean")]
        finally:
            self._selecting = False
        text = [l for l in leaves if l[1] == "string"]
        columns = []
        for _ in range(self.pick([1, 1, 2])):
            path = self.pick_leaf(text if text and self.chance(0.7) else leaves)[0]
            columns.append((path, self.pick(["asc", "desc"])))
        return columns

    def value(self, depth: int = 2) -> str:
        """Something to `select`: a field, a count, a pick or arithmetic."""
        scope = self._scope(depth)
        self._selecting = True
        try:
            if self.chance(0.5):
                return self.pick_leaf(self._leaves_in(scope))[0]
            return self.number(scope, depth)
        finally:
            self._selecting = False

    def condition(self, scope, depth: int) -> str:
        if scope.get("special"):
            return self._special_condition(scope)
        choice = self.rng.randrange(10)
        if depth > 0 and choice < 3:
            left = self.condition(scope, depth - 1)
            right = self.condition(scope, depth - 1)
            return f"({left} {self.pick(['and', 'or'])} {right})"
        if depth > 0 and choice == 3:
            return f"not ({self.condition(scope, depth - 1)})"
        if depth > 0 and choice == 4:
            return self._any(scope, depth - 1)
        if depth > 0 and choice == 5:
            number = self.number(scope, depth - 1)
            return f"{number} {self.pick(COMPARE)} {self._integer(number)}"
        if depth > 0 and choice == 6 and self.chance(0.5):
            pick = self.picked(scope, depth - 1)
            if pick:
                return f"{pick} {self.pick(['==', '!=', 'is'])} {self.pick([self._string(), 'None'])}"
        return self._comparison(scope)

    def _special_condition(self, scope) -> str:
        var = scope["var"]
        if scope["special"] == "backlinks":
            classes = ["Person", "Family", "Event", "Place", "Source", "Citation", "Media", "Note"]
            if self.chance(0.3):
                return f"{var}._class in {self.rng.sample(classes, 2)!r}"
            return f"{var}._class {self.pick(['==', '!='])} {self.pick(classes)!r}"
        rel = self.pick(["frel", "mrel"])
        if self.chance(0.5):
            return f"{var}.{rel} == {self.pick(['Birth', 'Adopted', 'Stepchild', 'None'])!r}"
        return f"{var}.{rel}.value {self.pick(['==', '!='])} ChildRefType.{self.pick(['BIRTH', 'ADOPTED', 'NONE'])}"

    def _any(self, scope, depth: int) -> str:
        lists = self._lists_in(scope)
        if not lists:
            return self._comparison(scope)
        path, kind = self.pick(lists)
        var = next(self._vars)
        entry = self._entry_scope(kind, var, depth)
        form = self.rng.randrange(3)
        if form == 0:
            return f"any({var} for {var} in {path})"
        condition = self.condition(entry, depth)
        if form == 1:
            return f"any({condition} for {var} in {path})"
        return f"any({var} for {var} in {path} if {condition})"

    def _operator(self, kind: str) -> str:
        if kind in ("type", "boolean"):
            return self.pick(["==", "!="])
        if kind == "string" and "string-ordering" in self.avoid:
            return self.pick(["==", "!="])
        return self.pick(COMPARE)

    def _comparison(self, scope) -> str:
        leaves = self._leaves_in(scope)
        leaf = self.pick_leaf(leaves)
        path, kind, _node = leaf
        choice = self.rng.randrange(12)
        if choice == 0:
            return f"{path} {self.pick(['is', 'is not', '==', '!='])} None"
        if choice == 1:
            values = ", ".join(self._literal_for(leaf) for _ in range(self.rng.randrange(1, 4)))
            return f"{path} {self.pick(['in', 'not in'])} [{values}]"
        if choice == 2:
            same = [l for l in leaves if l[1] == kind and l[1] != "type"]
            if kind in ("integer", "boolean") and "pg-column-vs-json" in self.avoid:
                same = []
            if same:
                return f"{path} {self._operator(kind)} {self.pick_leaf(same)[0]}"
        if kind == "string" and choice in (3, 4, 5):
            if choice == 3:
                return f"{self._like_pattern(path)} {self.pick(['in', 'not in'])} {path}"
            if choice == 4:
                return f"like({path}, {self._like_pattern(path)})"
            return f"regex({path}, {self._regex_pattern(path)})"
        op = self._operator(kind)
        literal = self._literal_for(leaf)
        if self.chance(0.15):
            return f"{literal} {op} {path}"
        return f"{path} {op} {literal}"


def main(argv: Optional[List[str]] = None) -> int:
    """Hunt for disagreements: run many random queries, print each failure
    once (grouped by its first line), then a summary."""
    import argparse

    from gramps_object_query_language.tests.test_random_parity import Harness

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--count", type=int, default=2000, help="queries per namespace")
    parser.add_argument("--seed", type=int, default=0, help="seed for the tree and the queries")
    parser.add_argument("--people", type=int, default=40, help="people in the random tree")
    parser.add_argument("--namespace", action="append", help="only these (repeatable)")
    parser.add_argument("--show", type=int, default=3, help="examples per kind of failure")
    parser.add_argument("--rejected", type=int, default=0, help="also print this many rejected expressions")
    args = parser.parse_args(argv)

    harness = Harness.open(seed=args.seed, people=args.people)
    try:
        failures: Dict[str, List[str]] = {}
        totals = {"ok": 0, "rejected": 0, "failed": 0}
        for namespace in args.namespace or Harness.NAMESPACES:
            for index in range(args.count):
                outcome, message = harness.check(namespace, args.seed, index)
                totals[outcome] += 1
                if outcome == "rejected" and args.rejected > 0:
                    args.rejected -= 1
                    print("REJECTED", message)
                if outcome == "failed":
                    key = message.splitlines()[0]
                    key = re.sub(r"'[^']*'|\d+", "?", key)[:120]
                    failures.setdefault(key, []).append(message)
        for key, messages in sorted(failures.items(), key=lambda kv: -len(kv[1])):
            print(f"=== {len(messages)} x {key}")
            for message in messages[: args.show]:
                print(message)
                print("---")
        print(totals, harness.stats)
        return 1 if failures else 0
    finally:
        harness.close()


if __name__ == "__main__":
    raise SystemExit(main())
