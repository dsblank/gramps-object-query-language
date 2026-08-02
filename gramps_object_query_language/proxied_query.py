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

"""Run a `where` expression against a possibly-proxied `db`.

Compiles a `Query.where` expression into a Gramps `Rule`, wrapped in the
matching core `Filter` class (`GenericFilterFactory`), and calls
`Filter.apply(db)` -- the same mechanism Gramps' own Custom Filters use.
`Filter.apply()` fetches each candidate through `db` before testing it
(`GenericFilter.get_object`), so when `db` is a proxy, everything this
module ever hands to `evaluate_where` has already been through that
proxy's own `include_*`/`sanitize_*` rules, at any relationship depth --
see `evaluator.py`'s module docstring for why that's sufficient on its own,
with no separate privacy handling needed here.

Not fast: `Filter.apply()` enumerates every handle of the type before
narrowing, and every enumerated handle is fetched (deserialized) to test
it. See `object_query.py`'s dispatch for when this path is used instead of
`query.py`'s SQL compiler.

`Filter.apply()` only ever returns handles, not the objects it built and
tested them with -- `_PredicateRule` holds onto each matched object itself
(`matched_objects`, keyed by handle) so `run_query` can hand them back
directly instead of fetching (and re-sanitizing) every match a second
time. Confirmed via profiling that this second fetch was ~70% of this
module's entire overhead relative to a hand-written equivalent loop: under
a proxy, re-fetching a handle means re-running the proxy's own
`sanitize_*`, not a cheap cache hit.
"""

from __future__ import annotations

from typing import Any, Dict, List

from gramps.gen.filters import GenericFilterFactory
from gramps.gen.filters.rules import Rule

from .evaluator import GETTER_BY_TABLE, evaluate_where
from .query import ObjectTypeSpec

# Core `Filter` namespace for each `ObjectTypeSpec.table` that has one.
# `Tag` is deliberately absent: `GenericFilterFactory("Tag")` returns `None`
# (Gramps core has no Filter class for it), and it has no privacy concept to
# delegate to a proxy for anyway (`TAG.has_privacy` is `False`) -- see the
# fallback in `run_query`.
_FILTER_NAMESPACE_BY_TABLE: dict[str, str] = {
    "person": "Person",
    "family": "Family",
    "event": "Event",
    "place": "Place",
    "repository": "Repository",
    "source": "Source",
    "citation": "Citation",
    "media": "Media",
    "note": "Note",
}


class _PredicateRule(Rule):
    """A `Rule` wrapping an already-compiled `where` expression.

    Not a new rule "language" -- `apply_to_one` just hands `obj` to the same
    `Query.where` AST evaluator every query endpoint already builds
    (`evaluator.evaluate_where`), so a query's `where`/`where_expr` means
    exactly the same thing on both the SQL and proxied paths.

    Also doubles as the match-object cache `run_query` reads afterward
    (`matched_objects`) -- `apply_to_one` is hand the real, already-fetched-
    and-sanitized object anyway, so keeping a reference here is free, and
    saves `run_query` from fetching (and re-sanitizing, under a proxy) every
    match a second time just to get the object back.
    """

    def __init__(self, where: Any, spec: ObjectTypeSpec) -> None:
        super().__init__([])
        self._where = where
        self._spec = spec
        self.matched_objects: Dict[str, Any] = {}

    def apply_to_one(self, db: Any, obj: Any) -> bool:
        # `obj is None` means this handle was excluded by whatever proxy
        # `db` is (or genuinely doesn't resolve) -- never a match,
        # regardless of `where` (an empty/`None` where means "match
        # everything", which must still exclude a row that isn't there).
        # Relying on every proxy's handle enumeration to have already
        # filtered this out before `Filter.apply()` ever calls this would be
        # exactly the kind of unverified cross-proxy assumption this
        # redesign exists to avoid -- see `evaluator.py`'s module docstring.
        if obj is None:
            return False
        matched = evaluate_where(db, obj, self._where, self._spec)
        if matched:
            self.matched_objects[obj.handle] = obj
        return matched


def run_query(db: Any, spec: ObjectTypeSpec, where: Any) -> List[Any]:
    """Every real object of `spec`'s type matching `where`, fetched through `db`.

    `db` may be a proxy or a plain database -- either way, the returned
    objects (and match decisions) reflect whatever `db` itself would return
    for each handle, never an unproxied lookup.
    """
    getter = getattr(db, GETTER_BY_TABLE[spec.table])
    namespace = _FILTER_NAMESPACE_BY_TABLE.get(spec.table)
    if namespace is None:
        handles = getattr(db, f"get_{spec.table}_handles")()
        return [
            obj
            for handle in handles
            if (obj := getter(handle)) is not None and evaluate_where(db, obj, where, spec)
        ]
    filter_class = GenericFilterFactory(namespace)
    gfilter = filter_class()
    rule = _PredicateRule(where, spec)
    gfilter.add_rule(rule)
    matched_handles = gfilter.apply(db)
    return [
        rule.matched_objects[handle]
        for handle in matched_handles
        if handle in rule.matched_objects
    ]
