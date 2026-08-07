# Roadmap

This is a running list of where `where_expr` currently draws its lines, and
what's been scoped out as a possible next step. It's a design/planning
document, not user-facing reference -- see
[`README-query-language.md`](README-query-language.md) and
[`docs/where_expr.md`](docs/where_expr.md) for what the language actually
does today.

## Current limitations

**Boolean structure**
- `and`/`or`/`not` are all supported, and can be mixed and nested
  (`not (a and b) or c`), following Python's own precedence and grouping.
- Chained comparisons (`1 < gender < 3`) are supported too -- see
  [Chained comparisons](#chained-comparisons-1--gender--3) under Done.

**Relationships**
- Seven one-to-one relationship links are registered: `Person` -> `birth`/
  `death` (-> `Event`), `Family` -> `father`/`mother` (-> `Person`), `Event`
  -> `place` (-> `Place`), `Citation` -> `source` (-> `Source`), `Place` ->
  `enclosed_by` (-> `Place`, self-referencing -- see Done below). Checked
  directly against Gramps' own object model (every `get_*_handle()` across
  all ten primary types) rather than assumed -- this appears to be the
  complete set of genuine one-to-one FK-like fields; a person's other
  (non-birth/death) events, for instance, aren't a missing one-to-one
  candidate at all -- Gramps has no dedicated ref-index column for them the
  way `birth_ref_index`/`death_ref_index` exist, so they're inherently
  one-to-many (already reachable via the `events` collection), not a gap
  in this list.
- One-to-many **collections** are registered on every type Gramps' own
  object model gives one to, queryable via `exists(name, condition)`/
  `count(name, condition)` (see `docs/where_expr.md`'s "One-to-many
  relationships"/"Counting a collection" sections and its full collections
  table): `notes`/`citations`/`media`/`tags` wherever the type has them,
  plus `Person.families`/`parent_families`/`associations`/`events`,
  `Family.children`/`events`, `Place.enclosing_places`,
  `Source.repositories`. `Tag` alone has no collections at all. There's
  still no `len()` over a plain intra-record JSON array
  (`primary_name.surname_list`, not a registered `Collection`), no `any(...)`
  over one either, and no way for an `exists(...)`/`count(...)` condition to
  reference the *outer* row (e.g. "a child with the same surname as the
  father") -- all three still flagged as follow-ups, not solved here.

**Values and functions**
- Only two whitelisted function-call forms exist: `like(field, 'pattern')`
  and `Date('...')`. No arithmetic, string functions (`upper()`, `lower()`,
  concatenation), or general function calls.
- `ClassName.CONST` constants: **this bullet was stale** -- found and
  corrected while adding `is`/`is not`/`not in` below. `_CONSTANT_CLASSES`
  (query_lang.py) already covers the full `GrampsType` constant space
  (`EventType`, `FamilyRelType`, `NameType`, `PlaceType`, `AttributeType`,
  `ChildRefType`, `EventRoleType`, `MarkerType`, `NameOriginType`,
  `NoteType`, `RepositoryType`, `SourceMediaType`, `SrcAttributeType`,
  `StyledTextTagType`, `UrlType` -- 15 classes total, plus `Person`/
  `Citation`/`Note`/`Date`), not just the three this bullet used to claim.
  Verified directly: `event.type.value == EventType.BIRTH` and
  `family.rel_type.value == FamilyRelType.MARRIED` both parse and compile
  today. What's still true: those `GrampsType` classes additionally
  support arbitrary user-defined custom values with no fixed constant to
  name, which no amount of registry-wiring fixes.
- `count(...)` answers "how many" for a registered `Collection`
  (`count(children) > 2`, see Done below) -- there's still no `len()` for a
  plain intra-record JSON array not backed by a `Collection`
  (`primary_name.surname_list`), and no other aggregates. See
  [`len()` / array-length comparisons](#len--array-length-comparisons) below
  for a scoped-out example of what closing that part of the gap would take.

**Sorting / pagination**
- `order_by` and keyset pagination (`after`) only work against flat SQL
  columns -- a `JsonPath` or `RelatedObject` field (`primary_name.surname_list[0].surname`,
  `birth.date.sortval`) can be filtered on but not sorted by. (query.py) See
  [Sortable JSON/relationship columns](#sortable-jsonrelationship-columns-order_bykeyset-on-jsonpathrelatedobject-item-k)
  below for a scoped-out example of what closing this would take.
- ~~The evaluator/proxied path (`proxied_query.py::run_query`) has no
  `order_by`/`limit`/`after` support at all~~ -- **Done**, see [Evaluator-path
  pagination/sort parity](#evaluator-path-paginationsort-parity-order_bylimitafterselect)
  below. `run_query` now accepts `order_by`/`limit`/`after`/`select`
  directly, capped at the same "flat columns only" scope this bullet's own
  SQL-path limitation already has.

**Cross-dialect behavior**
- `like(...)` and the substring form of `in` both compile to a plain SQL
  `LIKE`. SQLite's `LIKE` is case-insensitive by default for ASCII (and the
  `evaluator.py` path deliberately mirrors that); PostgreSQL's `LIKE` is
  case-*sensitive* by default. Nothing in the compiler accounts for this --
  the same `where_expr` string can match different rows depending on which
  dialect executes it. No test currently exercises this gap.

**Integration**
- The `where_expr` language itself isn't wired to any HTTP endpoint yet --
  `object_query.py`'s `where` field takes the JSON shape directly;
  `parse_expr`/`compile_expr` exist as a standalone layer on top, not yet
  exposed to a client that only has a query string.

## Done

### `or`

Implemented. Turned out much smaller than this document originally
estimated ("likely the largest single item here") -- `query.py`'s `Or`
class and `evaluator.py`'s `Or` branch already existed, fully working and
tested, before this change; the only work was teaching `query_lang.py`'s
parser to walk `ast.BoolOp` recursively instead of rejecting anything but a
top-level `and`, and choosing a wire shape (`{"or": [...]}`, with nested
`{"and": [...]}` where needed) that's additive -- any expression that
doesn't use `or` still produces the exact same flat list it always did. No
SQL or evaluator changes were needed at all. See `query_lang.py`'s
`_translate_bool_or_leaf`/`_translate_top_level`/`_node_from_json`.

### `evaluate_where`'s `Not`/missing-value divergence

Fixed. `evaluate_where` computed a plain `bool` at every leaf, collapsing a
missing value (a masked field, or a relationship/path that doesn't resolve)
straight to `False` -- correct on its own, but wrong once `Not` wraps it:
SQL's `NOT UNKNOWN` is still `UNKNOWN` (excluded from a `WHERE` clause,
same as the un-negated form), not `True`, and the old code had no way to
tell "definitely false" and "unknown" apart by the time `Not` saw it.
Verified empirically (not just reasoned about) before fixing: `Not(Gt(...))`,
`Not(In(...))`, and `Not(Like(...))` against a missing value all matched
the un-negated SQL query's *complement* incorrectly.

Fix: the recursion was split into a new `_evaluate_tri` (returns `True`/
`False`/`None`-for-`UNKNOWN`, matching SQL's three-valued logic exactly,
including `AND`/`OR`'s dominance rules -- a definite `False` always wins an
`AND` over an `UNKNOWN` sibling, a definite `True` always wins an `OR`
the same way) and the public `evaluate_where` (unchanged signature/
contract), which now just collapses to a real `bool` once, at the very
end, the same way a SQL `WHERE` clause treats `UNKNOWN` as excluded.
`query.py`/`query_lang.py` needed no changes -- SQL already had this right.

Covered by `test_evaluator.py`'s three-valued-logic section, including a
dedicated test that runs the same `where` AST through the real SQLite
compiler and through `evaluate_where` against equivalent data and asserts
they agree -- the regression guard for this exact class of bug.

### `not`

Implemented, once the `Not`/missing-value divergence above was fixed --
`query.py`'s `Not` class and `evaluator.py`'s `Not` branch already existed,
fully working and tested; the only remaining work was `query_lang.py`'s
parser, mirroring `or`'s addition almost exactly: `_translate_bool_or_leaf`
gained a case for `ast.UnaryOp`/`ast.Not` producing a `{"not": node}` wire
node, and `_node_from_json` a matching `Not(...)` case. No SQL or dialect
changes. `not` binds tighter than `and`, which binds tighter than `or`,
matching Python's own precedence (`ast.parse` resolves this before the
translator ever sees the tree, same as it already did for `and`/`or`).

### `exists(...)` -- one-to-many relationships (v1: `Family.children`, `Person.notes`)

Implemented, scoped to exactly the two collections described in "Current
limitations" above -- the recommended v1 from this section's earlier
scoping pass (see git history), chosen to prove both `Collection` shapes a
future registration might need:

- **`children`** (`Family` -> `Person`, via `child_ref_list`) -- a list of
  *ref objects*, each needing its `.ref` sub-key extracted for the handle.
- **`notes`** (`Person` -> `Note`, via `note_list`) -- a list of *plain
  handle strings*, no sub-key extraction needed.

`exists(relationship[, condition])` is a new whitelisted call form in
`query_lang.py` (alongside `like(...)`), producing a
`{"exists": {"relationship": ..., "where": [...]}}` wire node -- `where`
omitted entirely when `condition` is, meaning "at least one related row at
all." `condition` is parsed as an ordinary `where_expr` against the
collection's *target* type (reusing `_translate_top_level` recursively), so
it can chain further relationships, use `and`/`or`/`not`, or even nest
another `exists(...)`.

`query.py` gained `Collection` (the registry entry: target type, JSON list
path, and the optional ref sub-key) and `Exists` (the AST node), kept in a
separate `_COLLECTIONS` registry from `_RELATIONSHIPS` on purpose -- a
collection name is never valid as a dotted-path segment (`children.surname`
would be ambiguous, which child?), only as `exists`'s first argument; a
name collision between the two registries on the same table raises at
import time. `Exists.compile()` renders a real `EXISTS (...)` subquery, not
a correlated *scalar* one like `RelatedObject` -- it iterates the JSON array
via `json_each` (SQLite) or `jsonb_array_elements`/`jsonb_array_elements_text`
(PostgreSQL, the `_text` variant for a flat-handle list, since `->>` has no
meaning against a bare jsonb scalar) joined against the target table by
handle.

`evaluator.py` mirrors this with a matching `Exists` branch in
`_evaluate_tri`, walking the real in-memory list and fetching each related
object through `db` (proxy-safe, same as every other relationship hop).
**Simpler than every other branch there**: SQL's `EXISTS`/`NOT EXISTS` never
produces `UNKNOWN` the way an ordinary comparison against a missing value
does -- a row that fails the inner condition (or a family with zero
children at all) just isn't counted, it doesn't propagate a `NULL` outward
-- so `Exists` always resolves to a definite `True`/`False`, and `not
exists(...)` needs no three-valued-logic special case at all, unlike `Not`
wrapping an ordinary comparison.

Verified via a SQL-vs-evaluator agreement test (same style as
`evaluate_where`'s `Not`/missing-value regression guard), run against the
fixture's real underlying Gramps SQLite backend rather than a hand-built
mock table, since that's the one case where the same `json_data`/`handle`
schema `query.py` targets already exists for free.

### `count(...)` -- Collection cardinality

Implemented, following this section's own earlier scoping pass almost
exactly (see git history) -- the cheapest of the `count`/`len`/`any`
follow-ups, since it reuses `exists(...)`'s own `Collection`/SQL machinery
rather than adding new machinery of its own.

`query.py`'s `Exists.compile()` was refactored to extract a shared
`_collection_subquery_body(collection, outer_table, condition, dialect,
treeid)` helper (the `<target_table>, <source> WHERE ...` fragment both
need); a new `CollectionCount` dataclass wraps it as
`(SELECT COUNT(*) FROM ...)` instead of `Exists`'s
`EXISTS (SELECT 1 FROM ...)`. Unlike `Exists` (its own top-level boolean AST
node, since a bare `exists(...)` *is* the condition), `CollectionCount` is
just another `ColumnRef` -- `count(children) > 2` compiles to an ordinary
`Gt(CollectionCount(children, None), 2)`, so it plugs into every existing
`Comparison`/`In` class (and `evaluator.py`'s matching `resolve_column_ref`
branch) with no new AST node, no `_evaluate_tri` case, and no new
three-valued-logic wrinkle to reason about at all.

`query_lang.py`'s `count(relationship[, condition])` is recognized only on a
comparison's *left-hand side* (`_translate_column_or_count`, dispatched from
`_translate_compare` in place of the ordinary `_translate_column` call) --
producing `{"count_of": {"relationship": ..., "where": [...]}}`, parallel to
`{"json_path": [...]}`. Enforced explicitly, matching the v1 scope
recommendation: a bare `count(children)` with no comparison is rejected (not
a boolean leaf), and `count(...)` compared against another field
(`count(children) == mother.surname`) is rejected too, with its own error
message rather than silently building a nonsensical `value_column` --
`count(...)` on the *right*-hand side needed no special-casing at all, since
a bare `Call` node there was already rejected as "not a valid literal" by
the existing value-translation code, pre-`count()`.

`count(...)`'s missing-collection case turned out simpler than `len()`'s
planned one: `COUNT(*)` over zero matching rows is just `0` in SQL, no
`NULL`/`COALESCE` handling needed at all, unlike `json_array_length`'s
NULL-on-missing-path behavior `len()` will have to work around.

### More relationships/collections (all ten primary types)

Implemented -- registered every remaining `_COLLECTIONS` candidate flagged
under "Other gaps" in an earlier pass, plus one new one-to-one
`_RELATIONSHIPS` entry (`Citation.source`), confirming this really was "one
registry entry each," no new `query.py`/`query_lang.py`/`evaluator.py`
machinery required. Every field name and shape (flat handle list vs.
ref-object list needing `.ref`) was verified directly against
`gramps/gen/lib/*.py`'s real class hierarchy, not assumed from naming --
e.g. confirming `Source` has no `citation_list` at all (a source doesn't
cite other citations) and `Repository` has neither `citation_list` nor
`media_list`, so those are correctly absent from the registry rather than
present-but-always-empty. `Tag` gets no collections registered at all --
nothing in Gramps' object model gives a tag its own notes/citations/media/
tags/etc.

Now registered, per type: `Person` -- `notes`, `citations`, `media`, `tags`,
`families`, `parent_families`, `associations`, `events`. `Family` -- (already
had `children`) `notes`, `citations`, `media`, `tags`, `events`. `Event`/
`Citation`/`Media` -- the applicable subset of `notes`/`citations`/`media`/
`tags`. `Place` -- that subset plus `enclosing_places`. `Source` -- that
subset (no `citations`) plus `repositories`. `Repository` -- just `notes`/
`tags`. `Note` -- just `tags`.

**Found a real bug in the process, not just registered names**: `Person`'s
new `associations` collection (`Person.person_ref_list` -> `Person`, an
association with another person) is the first *self-referencing* collection
-- its target table is the same bare name (`person`) as whatever outer row
`exists`/`count` is being compiled against. `_collection_subquery_body`'s
`FROM {target_table}, {source}` unconditionally reintroduced that bare name
inside the same `FROM` clause `source` already correlates back to the outer
row through (`json_each(<outer_table>.json_data, ...)`) -- SQL resolves the
newly-introduced local binding as nearer in scope, silently shadowing the
outer correlation. Confirmed empirically before diagnosing: `Person
"exists(associations, ...)"` matched zero rows for every person, even ones
with a real matching association. Fixed by aliasing the target row
unconditionally (`{target_table} AS {target_table}__target`), not just when
a collision is detected -- one code path stays correct regardless of which
future collection happens to target its own table, rather than needing a
same-table special case. Existing `children`/`notes` tests (no self-
reference) needed only a cosmetic SQL-shape-assertion update, not a
behavior fix -- their target and outer tables were never the same to begin
with.

### `Place.enclosed_by` -- one-to-one self-reference (item E)

Implemented -- but not the vague "several missing relationships" item E's
own name originally implied. Checked Gramps' actual object model first
(every `get_*_handle()` across all ten primary types, plus each
`ObjectTypeSpec`'s flat columns) rather than assuming: the six already
registered turned out to be *every* genuine one-to-one FK-like field except
one -- `Place.enclosed_by`, a real, indexed flat column already in Gramps'
own DBAPI schema (`enclosed_by VARCHAR(50)`, computed as the first
`placeref_list` entry's handle -- see `gen/db/generic.py`), already exposed
on `PLACE`'s spec via `extra_columns`, but never registered in
`_RELATIONSHIPS`. "A person's other events," this section's own original
example of a missing one-to-one relationship, turned out not to be one at
all -- Gramps has no dedicated ref-index column for any event type besides
birth/death, so anything else is inherently one-to-many (the `events`
collection already covers it), not a one-to-one gap.

**Found a second self-reference bug, not just registered a name** --
`enclosed_by` is self-referencing (`Place` -> `Place`), the same shape as
`Person.associations` above, and it exposed a bug in `RelatedObject`
rendering nobody had hit before, since no two existing relationships ever
targeted the same table as their own outer table. `_render_related_object`
rendered every hop's target as a bare, unaliased table name
(`FROM place WHERE place.handle = ...`) -- for a self-referencing hop, the
subquery's own `FROM place` shadows the outer scope's `place`, so a
correlated reference meant for the outer row (`place.enclosed_by`) resolves
to the subquery's *own* row instead, making `place.handle = place.enclosed_by`
true only for a place enclosing itself (never, in real data) -- the whole
relationship silently matched nothing, for every row, regardless of real
data. Confirmed empirically before diagnosing (`enclosed_by.title ==
'Cook County'` matched zero rows against a real 3-level place hierarchy
where it should have matched one).

Unlike the `Collection` fix (a single flat subquery, a *fixed* alias
suffix was enough), `RelatedObject` nests arbitrarily deep, so a fixed
suffix isn't sufficient here -- two nested self-referencing hops
(`enclosed_by.enclosed_by`) would still collide with *each other* under a
fixed suffix. Fixed with a `_depth` parameter threaded through
`_render_related_object`'s recursion, giving every level a distinct alias
(`{target_table}__hop{depth}`) unconditionally -- not just when a collision
is detected, matching the same "one code path stays correct regardless of
which future relationship happens to self-reference" philosophy as the
`Collection` fix. Every pre-existing `RelatedObject` test needed only a
cosmetic SQL-shape-assertion update (the alias appearing in the expected
SQL text), not a behavior fix -- no existing relationship chain was ever
actually self-referencing before this one.

Verified three ways: parser-level shape (`enclosed_by.title`,
`enclosed_by.enclosed_by.title`), real end-to-end SQLite execution against
a 3-level hierarchy (city -> county -> state) for both one and two hops,
and a SQL-vs-evaluator agreement test in the same style as the
`associations`/`Not`/missing-value regression guards.

### `is` / `is not` / `not in`

Implemented -- item C from the [difficulty survey](#rough-difficulty-survey-of-unsupported-where_expr-shapes)
below, done first since (once checked directly rather than assumed) it
turned out to already be ~90% built by composition of pieces that were
each Done independently: `not (x in y)`, `x == None`, and
`not (x == None)` all already parsed and compiled correctly *before* this
change, since `not`/`==`/`in` were all Done and their three-valued-logic
composition (`Not` over a comparison against a possibly-missing value) was
already fixed (see `evaluate_where`'s `Not`/missing-value divergence,
above). `gender is None` and `gender not in [...]` failed only at the very
first gate (`op_type not in _COMPARE_OPS`, `query_lang.py`), before any
real logic ran.

`is`/`is not` needed no new wire shape at all -- added directly to
`_COMPARE_OPS` as `ast.Is: "eq"`/`ast.IsNot: "ne"`, since this language has
no notion of object identity distinct from value equality (`gender is
Person.MALE` and `gender == Person.MALE` compile identically). Every
existing `"eq"`/`"ne"` code path -- field-vs-field, `count(...)`'s
left-hand-side rejection, both SQL dialects, the evaluator -- handles them
with zero new branches.

`not in` reuses `in`'s own translation (both shapes: list-membership and
the substring test) verbatim, then wraps the result in `{"not": ...}` --
`_translate_compare` (query_lang.py) now computes a `negate` flag from
`op_type is ast.NotIn` up front, looks up `_COMPARE_OPS[ast.In]` in that
case, and wraps the leaf it would have returned in `{"not": leaf}` at the
very end instead of returning early. No `query.py`/`evaluator.py` changes
needed -- `"not"` wrapping was already fully generic (`_node_from_json`'s
`"not"` case predates this).

Confirmed the compiled output is byte-identical to what a user could
already write by hand: `parse_expr("person", "gender not in [1, 2]") ==
parse_expr("person", "not (gender in [1, 2])")`, and the same for the
substring form and for `is`/`==`. See `test_query_lang.py`'s "is / is not /
not in" section.

### Operand ordering (`value OP field`)

Implemented -- item B from the difficulty survey, "piece 2" of the
[Chained comparisons / operand ordering](#chained-comparisons--operand-ordering)
possibility below (piece 1, the actual chain rewrite, is still open --
see that section, now trimmed down to just piece 1).

`_translate_compare` (query_lang.py) no longer assumes `node.left` is
always the column -- it classifies both sides via `_is_path_node`/a new
`_is_count_call` helper before deciding how to translate them. Four
combinations now exist, only one of which is new code:
- column vs value, column vs column (field-vs-field) -- unchanged, exactly
  as before.
- **value vs column** (`Date('Jan 1, 1968') < mother.birth.sortval`) -- the
  new case. The literal is translated via the ordinary `_translate_value`,
  the field via `_translate_column`, and the operator flips through a new
  `_FLIP_OP` table (`lt`<->`gt`, `lte`<->`gte`, `eq`/`ne` unchanged) so the
  wire shape still renders with the column first
  (`{"column": ..., "op": ..., "value": ...}`) -- the one shape
  `query.py`/`evaluator.py` already know how to read. No changes needed in
  either.
- **value vs value** (`5 < 3`) -- rejected outright with a clear error
  ("a comparison must have a field path on at least one side"), since
  it references no field to filter on at all.

`count(...)`'s existing left-hand-side-only v1 scope was deliberately left
alone, not widened by this change: `_is_count_call` is checked only when
classifying the *left* operand, so `2 < count(children)` still doesn't
compile (rejected with the same "must have a field path" error, since a
bare Call on the right isn't recognized as a path either) -- symmetric with
`count(...)` never having been recognized on the right before this change.
`count(...)` vs another field (`count(children) > mother.surname`) is
still rejected too, regardless of which side each ends up on.

Verified two ways: parser-level equivalence (`parse_expr("5 < gender") ==
parse_expr("gender > 5")`, and the same for `Date(...)`/reversed
constants), and real end-to-end SQLite execution
(`test_where_expr_examples.py`'s `test_operand_order_value_on_left`/
`test_operand_order_reversed_constant`) confirming the flipped form
returns the identical result set as writing it the usual way, not just an
identical wire shape.

### Field-vs-field substring `in` (`other_field in field`)

Implemented -- item D. Turned out to need real `query.py`/`evaluator.py`
changes, not just a parser tweak (revised down from an initial "closer to
1" estimate, made before actually checking whether `Contains` already
supported a field-vs-field `value` the way the base `Comparison` class
does -- it didn't): `Contains.compile()` overrides `compile()` entirely
and unconditionally treated `self.value` as a Python string to escape at
*compile* time (`self.value.replace(...)`), with no `is_field_comparison`
branch at all. Same gap in `evaluator.py`'s `Contains` branch, and
`query_lang.py`'s `_condition_from_json` (the `"contains"` case returned
before the `value_column` check even ran).

`query_lang.py`'s `_translate_compare` now checks `_is_path_node(node.left)`
inside the `in`-branch before falling back to `_translate_value` --
producing `{"column": ..., "op": "contains", "value_column": ...}`,
parallel to every other operator's field-vs-field shape. `Contains.compile()`
gained an `is_field_comparison` branch: since the needle is only known at
query *execution* time now, not compile time, the wildcard-escaping
Python's `.replace(...)` does upfront for a literal has to happen in SQL
instead, via nested `REPLACE(...)` in the same backslash-first order,
wrapped in `'%' || ... || '%'` -- both dialects support `REPLACE`/`||`
identically, no per-dialect branch needed. `evaluator.py`'s `Contains`
branch mirrors it: resolves `expr.value` via `resolve_column_ref` when it's
a field, treating a missing *needle* as `None`/unknown the same way a
missing haystack already was.

**Found a real, pre-existing, silent correctness bug in the process, not
just missing Contains support**: every field-vs-field test/doc example
before this change happened to cross a relationship or JSON path
(`father.surname == mother.surname`, `mother.death.date.sortval <
father.death.date.sortval`) -- both `RelatedObject`, unambiguous. Two plain
flat columns on the *same* table, compared directly (`given_name ==
surname`), was never actually exercised anywhere. `_translate_column`
resolves a flat column to a bare `str`, and `Comparison.compile()`'s
`is_field_comparison = isinstance(self.value, (JsonPath, RelatedObject))`
doesn't recognize a bare `str` as a field at all (by design -- see the
class's own docstring: "a bare string is exactly as likely to be a literal
value... as a column name"). Confirmed empirically:
`compile_expr("person", "given_name == surname")` silently compiled to
comparing `given_name` against the *literal text* `"surname"`, not the two
columns -- reachable directly from the documented language, not a
contrived edge case, and would have made the new Contains feature wrong
for the most natural case (two flat columns) if left unfixed.

Fixed with a new `FlatColumnRef(name: str)` wrapper (query.py) -- added to
`ColumnRef`, recognized in `_render_column` (unwraps to a plain column,
same as a bare `str` already was), and added to every
`isinstance(..., (JsonPath, RelatedObject))` field-comparison check across
`Comparison.compile()`, `Contains.compile()`, and `evaluator.py`'s two
matching branches plus `resolve_column_ref`. `query_lang.py`'s
`_condition_from_json` wraps a `value_column` that resolves to a bare
`str` in `FlatColumnRef` before constructing the comparison object --
the one place this ambiguity needs resolving, since it's the only code
path that turns a wire-format `value_column` into a real object.

Verified with a dedicated regression suite (`test_query.py`'s "FlatColumnRef"
section): SQL-shape assertions, real end-to-end SQLite execution proving
`given_name == surname` actually compares columns (not a string literal),
and a SQL-vs-evaluator agreement test in the same style as
`evaluate_where`'s `Not`/missing-value regression guard (see Done above) --
the same AST run through the real SQLite compiler and through
`evaluate_where` against equivalent fake data, asserting they agree.

### Chained comparisons (`1 < gender < 3`)

Implemented -- item A, the last of the two pieces originally hiding behind
the single "no chained comparisons" bullet in Current limitations (piece 2,
operand ordering, was item B -- see
[Operand ordering](#operand-ordering-value-op-field) above, shipped
earlier).

`_translate_compare` (query_lang.py) no longer rejects a multi-op
`ast.Compare` node (`len(node.ops) != 1`) -- it desugars it into pairwise
legs first: `operands = [node.left, *node.comparators]`, then
`ast.Compare(left=left, ops=[op], comparators=[right])` for each
consecutive triple, each leg translated by recursing into
`_translate_compare` itself and the results joined as `{"and": [...]}`.
No new comparison semantics at all -- every leg is an ordinary two-term
comparison, so it transparently supports whatever a plain comparison
already does: operand ordering, `is`/`is not`/`in`, field-vs-field, even
*mixed* operators in one chain (`1 < gender != 3`), none of which needed
special-casing since nothing here assumes same-operator legs.

Confirmed the originally-motivating combined example now works, requiring
both A and B together: `Date('Jan 1, 1968') < mother.birth.sortval <
Date('Jan 30, 1968')` -- B alone (shipped first) wasn't sufficient, since
chaining itself was still rejected; A alone wouldn't have been sufficient
either, since the first leg (`Date(...) < mother.birth.sortval`) needs
operand-order support to compile at all.

Verified two ways: parser-level equivalence (`parse_expr("1 < gender < 3")
== parse_expr("gender > 1 and gender < 3")`) and real end-to-end SQLite
execution (`test_where_expr_examples.py`'s
`test_chained_comparison_readme_example`) confirming the chained form
returns the identical result set to the `and`-joined form, not just an
identical wire shape.

### Comprehension sugar for `exists(...)`/`count(...)` (`any(... for x in rel)`, `len([... for x in rel])`)

Implemented -- `_desugar_comprehensions`/`_ComprehensionDesugarer`
(query_lang.py), a preprocessing pass over the raw `ast` tree that runs
once, before `_translate_top_level` -- rewrites `any(cond for x in rel [if
...])` into `exists(rel, cond)` and `len([... for x in rel if ...])` into
`count(rel, ...)`, so every `_translate_*` function still only ever sees
the two original call forms; nothing downstream knows the sugar spelling
was involved.

The one real piece of work is dropping the loop variable: `exists`/
`count`'s condition is parsed directly against the collection's target
type with no prefix of its own, so `x.field` inside the comprehension has
to become plain `field` -- `_BoundNameStripper` does that once per
comprehension level, and composes correctly across nested comprehensions
(`any(any(e.type.value == X for e in c.events) for c in children)` ->
`exists(children, exists(events, type.value == X))`) with no symbol table,
since each level's stripper fully consumes or errors on every reference to
its own bound name before the next level up ever runs -- including the
case where an inner comprehension reuses the same loop-variable name as an
outer one (verified: resolves correctly, matching real Python's own rule
that a comprehension's first `for` clause's `iter` is evaluated in the
*enclosing* scope, not its own).

Scope matches what `exists`/`count` already support by hand, on purpose,
so the sugar can't silently become more expressive than the call syntax
it's standing in for: exactly one `for` clause, a plain-name loop target
(no tuple unpacking), and `in ...` restricted to a bare collection name or
exactly one attribute off an *enclosing* comprehension's own loop variable
(mirroring `exists(children, exists(events, ...))`'s existing nesting,
never a longer chain). `all(...)`/`sum(...)` deliberately not covered --
`all(...)` would need double-negation (`not exists(rel, not cond)`) for
not much payoff without a concrete motivating case yet.

**Naming note for whoever eventually builds the `len()`/`any()` items
below:** both names are now spoken for by this sugar too, but the two
meanings don't actually collide -- Python's own call syntax keeps them
apart. This sugar only ever fires when the *sole* argument is a real
`ast.GeneratorExp`/`ast.ListComp` node (`_rewrite_any`/`_rewrite_len` in
`query_lang.py` check the node type explicitly and raise otherwise); the
array-length/membership forms described below take a bare path (or path +
condition) as their argument instead, a different, unambiguous AST shape.
Building either later item just means adding an `else` branch to the
existing `any`/`len` dispatch in `_ComprehensionDesugarer.visit_Call` for
"argument wasn't a comprehension," not renaming anything.

### Evaluator-path pagination/sort parity (`order_by`/`limit`/`after`/`select`)

Implemented -- `run_query` (proxied_query.py) accepts `order_by`, `limit`,
`after`, and `select` directly now, applied entirely in Python over the
match list `Filter.apply()`/the direct-loop path already collects, so a
`Query` means the same thing on this path as it does through `query.py`'s
compiled SQL, matching this section's own motivation exactly.

Landed close to its own scoping pass above, with one deliberate scope
change: `select` was **not** left out. The scoping pass's argument for
leaving it out (`run_query` returns real objects, not projected rows) turned
out not to force a choice between the two -- `select` is optional and
additive: omitted, `run_query` still returns full objects, byte-for-byte the
same as every caller from before this change relied on; given, it returns a
list of value tuples (one per `select` entry, resolved via the existing
`resolve_column_ref`) instead, the evaluator-path equivalent of the SQL
path's own `SELECT` list. No caller that doesn't pass `select` can observe
any difference at all.

Sort/seek/limit landed exactly as the scoping pass laid out:

- **Sort** -- `effective_order_by` (renamed from `_effective_order_by`,
  now public so `proxied_query.py` can import it, alongside `query.py`'s
  existing `after_columns`/`check_columns`) supplies the same trailing
  `handle` tiebreaker the SQL path always compiles; `_sort_matches`
  resolves each row's sort key via `resolve_column_ref` and orders with
  `functools.cmp_to_key` (needed because a mixed asc/desc multi-column sort
  isn't expressible as a single `sorted(key=...)` tuple once NULL placement
  is direction-dependent).
- **NULL placement (verified, not assumed)** -- confirmed directly against
  real SQLite (`ORDER BY v ASC`/`DESC` over a mixed NULL/int column) before
  writing `_null_safe_cmp`: NULL sorts as the smallest value in *both*
  directions, so `DESC` is the exact reverse of `ASC`, NULLs included --
  matches this section's own "pin down against SQLite's real, verified
  default" requirement.
- **Collation** -- ASCII/codepoint only (plain Python `<`/`>`), exactly the
  documented gap this section anticipated; no attempt at locale-aware
  `COLLATE` parity.
- **`order_by` stays flat-columns-only** -- `OrderBy.column` is already
  structurally a plain string (no `JsonPath`/`RelatedObject` shape exists
  for it), so this path was never at risk of leapfrogging past item K's own
  SQL-path cap; no extra guard was needed beyond the existing
  `check_columns` whitelist check `compile_query` already applies.

**Found a real, pre-existing bug in `query.py`'s own `_compile_keyset` in the
process, not just a Python-side mirroring gap** -- surfaced while writing
`_matches_keyset` and explaining the design out loud, not found by staring at
the code cold. The original compiled seek predicate (`(c1 > ?) OR (c1 = ? AND
c2 > ?) OR ...`) used plain, non-NULL-safe SQL comparisons throughout. Since a
comparison against a real SQL `NULL` is always `UNKNOWN` (never `TRUE`), this
silently broke keyset pagination in two different, independently-confirmed
ways (checked against real SQLite before fixing, in both directions):

- **Seeking past a cursor row whose own sort value was `NULL`** returned
  *zero* further rows, even when real rows exist after it in the established
  order -- every leg touching that bound `NULL` parameter was `UNKNOWN`, so
  the whole `OR` was too.
- **A `desc`-sorted column silently dropped its own `NULL` rows from every
  later page**, regardless of what the cursor was -- `NULL` sorts *last* in
  `desc` (the same "NULL is smallest" total order plain `ORDER BY` already
  uses, just at the other end), but a plain `col < ?` against a `NULL` row is
  `UNKNOWN`, not `TRUE`, so those rows never satisfied the predicate on any
  page after the first.

Fixed with `_keyset_tie_sql`/`_keyset_leg_sql` (query.py): each leg is now
built NULL-safely, using the concrete cursor value already known at *compile*
time (not a runtime unknown) to choose `col IS NULL`/`col IS NOT NULL` instead
of a bound comparison whenever the cursor value at that position is `None`,
and unconditionally admitting `NULL` rows on a `desc` leg
(`(col IS NULL OR col < ?)`) regardless of the cursor. A leg that can never
hold at all (`desc`, cursor already `None` -- nothing ranks after the
minimum) returns `None` from `_keyset_leg_sql` and is dropped from the `OR`
entirely; if *every* leg drops this way, `_compile_keyset` emits a literal
`0 = 1` ("no further rows") rather than an empty, malformed `OR`.

This fix applies to **both paths at once**, and had to -- `_matches_keyset`
(the Python mirror) was simplified to directly reuse `_null_safe_cmp` (the
exact same NULL-is-smallest total order plain sorting already uses) rather
than hand-rolling its own three-valued-`UNKNOWN` logic, so seeking and
sorting now share one NULL rule, not two, on both the SQL and evaluator
paths. Fixing only the Python side (to keep the parity goal this whole
section exists for) would have meant deliberately reproducing a bug on the
SQL path that's now known to be wrong -- not an option once found.

Verified with the same SQL-vs-evaluator agreement style as every other
regression guard in this document (`test_evaluator.py`'s Not/Exists/Count
tests) -- `test_proxied_query.py`'s "order_by/limit/after/select parity"
section runs the identical `Query` shape through `query.py`'s real compiled
SQL (against the fixture's own underlying Gramps SQLite backend) and through
`run_query`, and asserts the rows match, across ASC, DESC, `limit`, a keyset
second page, `select` projection, and a genuinely-nullable flat column
(`Family.father_handle` -- `Person.given_name` doesn't work for this, since
Gramps always stores it as `""`, never a real SQL NULL) -- plus two dedicated
regression tests for the NULL-keyset bug itself, one seeking past a `NULL`
cursor and one confirming a `desc` page doesn't drop a `NULL` row after an
ordinary cursor. `test_query.py` additionally covers the fixed SQL shape and
a real end-to-end SQLite execution directly, independent of the Gramps
fixture.

## Possibilities

### `len()` / array-length comparisons

**Naming note:** `len(...)` is already in use for a *different* thing --
comprehension sugar for `count(...)`, see Done above -- but the two don't
collide; that form only fires when `len(...)`'s sole argument is a real
`ast.ListComp` node, so a bare-path argument here (`len(x) > 1`) is free to
mean this instead. Building this just adds an `else` branch to the
existing dispatch, not a rename.

Motivated by: "does a person have more than one surname recorded?" --
today only answerable indirectly, by indexing a fixed position
(`primary_name.surname_list[1].surname != None`, see
README-query-language.md's cookbook) rather than asking for a count
directly.

**Note (written after `count()` shipped, see Done above):** the "column can
be a computed value" plumbing this section originally worried about most is
now a proven pattern, not a design risk -- `count()`'s
`_translate_column_or_count`/`CollectionCount` did exactly this for
`ColumnRef`, so `len()`'s parser/`query.py` work below can copy that shape
directly rather than inventing it. The layer-by-layer breakdown and open
semantic questions below are otherwise unchanged from the original pass.

**Difficulty:** medium. **Invasiveness:** touches every layer (parser, both
SQL dialects, the non-SQL evaluator, docs, tests), but each touch is small.
Structurally bigger than a typical new-operator addition (e.g. the
`'substring' in field` addition, ~210 lines across 9 files) because `len()`
isn't a new comparison operator -- it's a new kind of *operand*, a computed
value derived from a column, which has to be threaded through every place
a "column" currently means "a path, verbatim."

**What it would take, layer by layer:**

1. **Parser (`query_lang.py`)** -- `len(x) > 1` breaks the assumption that a
   comparison's left side is always `_translate_column(node.left, spec)`.
   Needs a new case for `ast.Call(func=Name('len'))` before falling through
   to plain path translation, producing a wire shape like
   `{"column": {"length_of": {...}}, "op": "gt", "value": 1}`.
   `_is_path_node` needs to keep saying "no" for `len(...)`, same as it
   already does for `Date(...)`.
2. **`query.py`** -- a new `ColumnRef` variant (e.g. `Length(inner:
   ColumnRef)`), a new branch in `_render_column`, and dialect-specific
   rendering:
   - SQLite: easy -- `json_array_length(json_data, '$.path')` is a sibling
     function to `json_extract`, same path syntax.
   - PostgreSQL: harder -- `jsonb_array_length(...)` needs a *jsonb* value,
     not text, so it has to reuse the `jsonb_extract_path` (non-`_text`)
     branch that today only fires for numeric/boolean value-casting, not as
     a general "give me raw jsonb" path.
   - Wrapping a `RelatedObject` field (`len(father.aka_surnames)`) needs a
     third branch in `_render_related_object`'s field dispatch, alongside
     its existing `JsonPath`/`RelatedObject`/plain-column handling.
3. **`evaluator.py`** -- a matching `Length` branch in `resolve_column_ref`
   (`len(value) if isinstance(value, list) else ...`). This has to agree
   with the SQL path on every input or the two execution modes silently
   diverge -- the main correctness risk in the whole feature.
4. **Docs + tests** -- `docs/where_expr.md`, `README-query-language.md`
   (replacing the index-based workaround), plus `test_query_lang.py`,
   `test_query.py` (both dialects -- SQL-shape assertions are expected for
   each, per existing pattern), `test_evaluator.py`,
   `test_where_expr_examples.py`.

**Risk isn't the code, it's the semantics -- three decisions with no
obviously-correct default:**

- **Missing field.** `len(x)` where `x` doesn't exist at all -- SQLite's
  `json_array_length` on a missing path returns `NULL`, not `0`. If that's
  left as `NULL`, `len(x) != 0` renders via the existing `_NULL_SAFE_OPS`
  machinery as `IS DISTINCT FROM`, and `NULL IS DISTINCT FROM 0` is
  **true** -- so a person with *no list at all* would wrongly match "not
  zero." Likely needs `COALESCE(json_array_length(...), 0)`, with the
  evaluator side matched exactly.
- **Non-array value at that path.** `len(surname)` (a string, not a list)
  -- SQLite's `json_array_length` silently returns `0` for a non-array JSON
  value rather than erroring. No schema is tracked at parse time, so
  there's no way to reject `len()` on a scalar field up front -- misuse
  just quietly returns 0/no-match instead of surfacing an error.
- **Scope of where `len()` is legal.** Restricting it to "left side only,
  compared against a literal" (`len(x) > 1`) is the safe, contained
  version. Allowing it on the right, `len(a) == len(b)` field-vs-field, or
  inside `select`, are each additional surface area worth cutting from a
  first version.

**Recommended scope for a v1:** `len(path) <op> <int literal>`, left-hand
side only; missing path treated as `0` (matches user intuition -- "no list
recorded" reads as "zero", not "unknown"); non-array-value and NULL-vs-zero
edge cases written as tests *before* either dialect is wired up.

### `any(...)` -- intra-record JSON array membership

**Naming note:** `any(...)` is already in use for a *different* thing --
comprehension sugar for `exists(...)`, see Done above -- but the two don't
collide; that form only fires when `any(...)`'s sole argument is a real
`ast.GeneratorExp` node, so a path-and-condition argument list here
(`any(path, condition)`) is free to mean this instead. Building this just
adds an `else` branch to the existing dispatch, not a rename.

Resolves an open question from `exists(...)`'s own scoping pass (see
"Done" above): is `any` just redundant with `exists`? **No** -- they target
different data shapes and aren't substitutable:

- `exists(collection, condition)` -- a *cross-table* one-to-many
  relationship, registered via `Collection`/`_COLLECTIONS` (`Family.children`
  reaches a real `Person` row in another table).
- `any(path, condition)` -- an *intra-record* JSON array already living
  inside the current row's own `json_data` (`primary_name.surname_list`,
  `attribute_list`, `url_list`, ...) -- no second table, no registration at
  all, works on any JSON array path the same permissive way `JsonPath`
  already works on any nested field.

Motivated by: "people with a surname of Doyle recorded, in *any* position"
-- today only answerable by checking a fixed index
(`primary_name.surname_list[1].surname != None`, `len()`'s own motivating
example above), which breaks if the matching surname isn't at that exact
position.

**Difficulty:** large -- the biggest of the three items on this page.
Unlike `count()`/`len()` (operate on a value already reachable via the
existing `JsonPath`/`RelatedObject` machinery) or `exists()` (operates on a
real registered target table with its own `ObjectTypeSpec`), `any()`'s
condition has to resolve field references against an *anonymous* JSON
object with no `ObjectTypeSpec` at all -- a `Surname`/`Attribute`/`Url`
struct isn't one of the ten primary types with `get_secondary_fields()`.

**What it would take, layer by layer:**

1. **`query_lang.py`** -- `any(path, condition)` as a third whitelisted call
   form (alongside `like`/`exists`), producing
   `{"any": {"path": [...], "where": [...]}}`. Condition parsing needs a
   spec whose `.columns` is always empty, forcing every field reference
   inside the condition to fall through to `JsonPath` -- a synthetic
   `ObjectTypeSpec(table="", columns=frozenset(), text_columns=frozenset())`
   does this with no new parser logic, reusing `_translate_top_level`
   exactly as `exists` already does.
2. **`query.py`** -- a new AST node (e.g. `JsonArrayExists`), sibling to
   `Exists` but with *no target table at all* -- the condition's columns
   render as `json_extract(je.value, '$.<field>')` (SQLite) /
   `je.value ->> '<field>'` (PostgreSQL) instead of a real table column, so
   `_render_column`/`Comparison.compile()` need a way to resolve "against
   `je.value`, not `spec.table`" for anything nested inside an `any(...)`.
   This is the one place the existing `spec.table`-correlation assumption
   baked into `_render_column`/`RelatedObject`/`Exists` doesn't hold.
   - The array path itself (`primary_name.surname_list`) can be arbitrarily
     nested, unlike `Collection.list_path` (always a single top-level key)
     -- SQLite's `json_each` already accepts a full `'$.primary_name.
     surname_list'` path directly, no change needed; PostgreSQL's
     `jsonb_array_elements` needs the full `->` chain built out (reusing
     `_postgresql_handle_ref_path_sql`'s pattern, not
     `_collection_source_postgresql`'s single-key shortcut).
3. **`evaluator.py`** -- walking a raw JSON list (from `get_json_path`)
   rather than a real Gramps object per element -- `resolve_column_ref`'s
   object-based machinery (`get_flat_column`'s `getattr`, `get_json_path`'s
   `object_to_dict`) doesn't apply; needs a parallel, simpler resolver that
   walks a plain `dict` directly via `_walk_json_path` alone.
4. **Docs + tests** -- the same four files as every prior addition.

**Risk / open decisions:**

- **List-of-scalars fields** (`note_list`, `tag_list` -- plain handle
  strings, no sub-object) have no sub-field to write a condition against
  inside `any(...)`. Recommend v1 requires a list-of-structs field and
  rejects a bare-scalar list outright -- `len(note_list) > 0` already covers
  "has any at all" for a scalar list, and `exists(notes)` already covers
  `Person.notes` specifically once it's a registered `Collection`.
- **Chaining a relationship before the array** (`any(birth.attribute_list,
  value == 'X')`) -- structurally free if the array-path segment reuses
  `resolve_column_path` the same way `exists`'s relationship name does;
  recommend allowing it.
- **Nesting `any(...)` inside `exists(...)`'s condition, or vice versa** --
  should fall out for free from both being ordinary leaf/boolean nodes, but
  needs an explicit test once both exist, since neither was designed with
  the other in mind originally.

**Recommended scope for a v1:** `any(path, condition)` where `path` resolves
to a list-of-structs JSON array (relationship-chaining allowed ahead of the
array itself); condition fields resolve only as JSON paths relative to each
element (no flat-column fast path, no nested `any`/`exists` inside the
element for v1); list-of-scalars arrays explicitly unsupported and rejected
with a clear error rather than silently matching nothing.

### Suggested implementation order for `len`/`any` (revised after `count()`)

Originally planned as `count -> len -> any`, on the theory that `len()`
would be the item that first introduces the "a column can be a *computed*
value, not just a path" plumbing through `_translate_column`/`ColumnRef`/
`_render_column`. That plumbing turned out to arrive with `count()` itself
instead (`_translate_column_or_count`, `CollectionCount` as a `ColumnRef`
variant, both described under Done above) -- so `len()` is now the *easier*
item of the two remaining, since it can copy that exact pattern
(`_translate_column_or_len`, a `Length`/`ArrayLength` `ColumnRef` variant)
rather than inventing it from scratch. `any()` still goes last: it needs
that same pattern *and* its own new no-target-table `EXISTS`-over-JSON-array
rendering on top, the one piece neither `count()` nor `len()` needs.

### Sortable JSON/relationship columns (`order_by`/keyset on `JsonPath`/`RelatedObject`) (item K)

Motivated by: `order_by=birth.date.sortval` or
`order_by=primary_name.surname_list[0].surname` -- both already resolve fine
as a `where`/`select` field via `resolve_column_path`, but `OrderBy.column`
today is a plain `str` checked against `spec.columns`, so neither can be
sorted by (see the Current limitations bullet above).

**The blocking question this section resolves:** `_render_json_path`'s
`CAST` logic (`BOOLEAN`/`NUMERIC`/plain text, PostgreSQL only) is driven by
the runtime comparison `value` a `where` clause always has in hand --
`order_by` has no such value, so naively reusing that function for a sort
column would leave every JSON-path sort ordering PostgreSQL text
lexicographically (`"10" < "9"`), silently wrong for exactly the kind of
numeric field (`sortval`) this is motivated by. **Confirmed live that this
is solvable statically, not a hard blocker:** every `gramps.gen.lib` class
already exposes `.get_schema()`, a plain JSON-schema dict --
`Event.get_schema()["properties"]["date"]["properties"]["sortval"]` is
`{"type": "integer", ...}` -- so a path's terminal type is knowable at
compile time by walking the schema, the same walk
`../addons-source/GrampyScript/stub_generator.py`'s `_pytype`/`_walk`
already does (there, to render jedi type-stub annotations for
autocompletion; here, to pick a `CAST` instead of a type annotation). No
new dependency: `gramps.gen.lib` is already imported throughout query.py.

**Difficulty:** medium, now scoped by actual layer-by-layer analysis (like
`len()`/`any()` above), not an estimate by analogy.

**What it would take, layer by layer:**

1. **A schema-walk type resolver (new, small)** -- given a root class and a
   segment list (mirrors `resolve_column_path`'s own `(str | int)`
   segments), descend `schema["properties"][segment]` for a `str` segment,
   or `schema["items"]` for an `int` segment on an `array`-typed field,
   returning a terminal JSON-schema type string (`"string"`/`"integer"`/
   `"number"`/`"boolean"`, else a fallback -- see risk below). Only the
   traversal is needed, not `stub_generator.py`'s registry-building or
   stub-source-rendering machinery around it.
2. **`ObjectTypeSpec` needs a `cls` field** (query.py:59-64 currently
   carries only `table`/`columns`/`text_columns`) so a `JsonPath`'s or
   `RelatedObject`'s target class is resolvable back to a real
   `gramps.gen.lib` class to call `.get_schema()` on -- for a
   `RelatedObject` chain (`birth.place.title`), the walk needs to follow
   the same relationship hops `resolve_column_path` does, switching root
   classes at each hop.
3. **`query.py` core:**
   - Widen `OrderBy.column: str` to `OrderBy.column: ColumnRef` (query.py:1273-1276).
   - `_check_column` (query.py:1538-1539) skips its whitelist check for a
     non-`str` column -- a `JsonPath` already self-validates
     (`__post_init__`) and a `RelatedObject` is already only constructible
     via `resolve_column_path`.
   - `_column_expr` (query.py:1335-1353) dispatches to
     `_render_json_path`/`_render_related_object` instead of
     `_quote_column` for a non-`str` column; `COLLATE` applies only when
     the static schema type is `"string"` -- the `JsonPath`/`RelatedObject`
     equivalent of the existing `spec.text_columns` check for flat columns.
   - `_render_json_path`'s `CAST` branch (query.py:596-608) switches from
     being driven by the runtime `value` argument to being driven by the
     static schema type from step 1 -- a strict improvement for `where`
     too, not just `order_by`: today a field-vs-field comparison
     (`a.sortval < b.sortval`) has no literal `value` to inspect at all, so
     it silently skips the numeric `CAST` it should get.
   - `_compile_keyset`/`after_columns` (query.py:1297-1304, 1405-1461)
     mostly fall out for free once `_column_expr` handles the new column
     shapes, since both already funnel every column reference through it.
4. **`object_query.py` (wiring layer)** -- wherever a client-supplied
   `after=<handle>` cursor gets resolved into the tuple `after_columns()`
   names (per this module's own docstring, "resolving a client-supplied
   handle into that tuple... is the caller's job"), that resolution
   currently assumes each name is a real column it can select directly.
   Once `after_columns()` can return a `JsonPath`/`RelatedObject`, that
   lookup needs to render through the same path `select` already uses
   (`_render_column`), not a raw column-name `SELECT`.
5. **Evaluator-path parity (item L's cap, proxied_query.py)** -- simpler
   than the SQL half: `evaluator.py`'s `resolve_column_ref` already
   resolves a `JsonPath`/`RelatedObject` for `where`, and Python's own
   `<`/`>` don't need a `CAST` hint the way SQL does, so once `order_by`
   accepts non-flat columns structurally, the evaluator path likely needs
   no schema-walk of its own at all -- just widening the one
   `check_columns` whitelist call proxied_query.py's docstring already
   flags as the sole flat-columns-only assumption.
6. **Docs + tests** -- README-query-language.md's `order_by` examples,
   `test_query.py` (both dialects -- new SQL-shape assertions, per existing
   pattern), `test_proxied_query.py`, plus a new unit test file for the
   schema-walk resolver itself.

**Risk / open decisions:**

- **Ambiguous schema types.** A handful of fields have a JSON-schema `type`
  that's a *list*, not a single string (e.g. `Date.dateval`'s items are
  `["integer", "boolean"]`) -- `stub_generator.py`'s own `_pytype` already
  falls back to `"object"` for this shape. Sorting on one of these would
  fall back to plain-text rendering (no `CAST`, no `COLLATE`) -- a known,
  narrow limitation to document rather than a blocker for v1.
- **Multi-hop `RelatedObject` chains** need the schema walk to switch root
  classes at each relationship hop, using the same `_RELATIONSHIPS`
  registry `resolve_column_path` already threads through -- not a new
  registry, just a second consumer of the existing one.

**Recommended scope for a v1:** `OrderBy.column` accepts any `ColumnRef`
already resolvable by `resolve_column_path` (i.e. anything `where`/`select`
already support); static schema-walk types drive `CAST`/`COLLATE`
selection; the rare ambiguous-type fields sort as plain text, documented as
a known limitation rather than blocking the rest. Evaluator-path parity
(closing item L's remaining cap) is small enough to land in the same
change rather than a separate follow-up.

### Other gaps (not yet scoped to this level of detail)

- **`exists(...)`/`count(...)` condition referencing the *outer* row** (e.g.
  "a child with the same surname as the father") -- not supported; would
  need the condition's column resolution to see two rows (target *and*
  outer) at once, which nothing here does today.
- **LIKE case-sensitivity parity across dialects** -- smallest fix here:
  render `ILIKE` on PostgreSQL for `like(...)`/substring-`in`, or apply a
  case-insensitive collation. Needs a test that actually runs both dialects
  and compares results, since nothing currently catches this gap.
- **Duplicate `RelatedObject` subqueries across `And` legs** -- a chained
  comparison against the same relationship path
  (`Date(...) < mother.birth.sortval < Date(...)`) renders the *entire*
  correlated subquery chain (`family.mother_handle` -> `person` ->
  `birth_ref_index` -> `event` -> `sortval`) twice, byte-for-byte identical
  except the bound value -- each leg of the chain is an independent
  `Comparison` object, and nothing deduplicates a repeated `RelatedObject`
  expression across sibling `And` legs. Not chaining-specific (writing the
  same thing by hand with an explicit `and` has always had this cost) and
  not a correctness issue, just an efficiency one -- deliberately left
  as-is for now. Would need `Comparison`/`RelatedObject` rendering to
  recognize a structurally-identical sibling and factor it out (e.g. a
  `WITH` CTE or binding the subquery's result once), a general
  optimization rather than anything specific to chaining or operand
  ordering.

### Rough difficulty survey of unsupported `where_expr` shapes

A quick-reference table (informal 1-5 scale, 1 = easy, 5 = hard) gathering
gaps described elsewhere in this document into one place for planning
purposes. Only `len()`, `any()`, and K (sortable JSON/relationship
`order_by` columns) have difficulty ratings arrived at through actual
layer-by-layer scoping (see their sections above); the rest are estimates
by analogy to those two and to `count()`'s actual implementation cost, not
independently measured the same way. (LIKE
case-sensitivity parity is deliberately not in this table -- it's a
database/collation concern, not a `where_expr` language gap.)

Each row is lettered so the dependency analysis right below it can refer
back to individual items. Letters are kept stable as items get implemented
(rather than renumbered) so old discussion of e.g. "B depends on..." stays
correct -- **A, B, C, D, and E have shipped** (see Done above) and are kept
here, struck through, so that history stays legible.

| # | Example | Gap | Difficulty |
|---|---|---|---|
| ~~A~~ | ~~`1 < gender < 3` (chained, literal on the right of each leg)~~ | **Done** -- see [Chained comparisons](#chained-comparisons-1--gender--3) above | ~~1~~ |
| ~~B~~ | ~~`Date(...) < mother.birth.sortval` (literal on the left)~~ | **Done** -- see [Operand ordering](#operand-ordering-value-op-field) above | ~~2~~ |
| ~~C~~ | ~~`gender is None`, `mother is not None`, `tag not in tags`~~ | **Done** -- see `is` / `is not` / `not in` above | ~~1~~ |
| ~~D~~ | ~~`other_field in field`~~ | **Done** -- see [Field-vs-field substring `in`](#field-vs-field-substring-in-other_field-in-field) above | ~~2~~ |
| ~~E~~ | ~~a person's non-birth/death event, one-to-one~~ | **Done** -- see [`Place.enclosed_by`](#placeenclosed_by----one-to-one-self-reference-item-e) above | ~~1~~ |
| F | `event.type == EventType.MARRIAGE` (or `FamilyRelType.*`, `NameType.*`, ...) | ~~only `Person`/`Citation`/`Note` constants wired~~ -- **stale, already Done**: `_CONSTANT_CLASSES` (query_lang.py) already covers `EventType`/`FamilyRelType`/`NameType`/`PlaceType`/and 10 more `GrampsType` classes, verified directly against a live parse (`event.type.value == EventType.BIRTH` compiles today). This row's original premise no longer holds; kept only as a note to fix the "Values and functions" bullet in Current limitations, not as an open item. | n/a |
| G | `len(primary_name.surname_list) > 1` | see `len()` section above | 3 |
| H | `upper(surname) == 'SMITH'`, string concatenation, arithmetic | no general function calls -- only `like()`/`Date()` are whitelisted | 3-4 |
| I | `any(primary_name.surname_list, surname == 'Doyle')` | see `any()` section above | 5 |
| J | `exists(children, surname == father.surname)` | `exists`/`count` conditions can't see the outer row | 4 |
| K | `order_by=birth.date.sortval`, `order_by=primary_name.surname_list[0].surname` | see [Sortable JSON/relationship columns](#sortable-jsonrelationship-columns-order_bykeyset-on-jsonpathrelatedobject-item-k) above | 3 |
| ~~L~~ | ~~any `order_by`/`limit`/`after` under a proxy (e.g. `PrivateProxyDb`)~~ | **Done** -- see [Evaluator-path pagination/sort parity](#evaluator-path-paginationsort-parity-order_bylimitafterselect) above | ~~3-4~~ |

Difficulty here means "distance from today's code," not "priority" -- a low
number isn't necessarily higher-value, just cheaper to build.

**C shipped at difficulty 1**, one step down from the original difficulty-2
estimate -- confirmed before implementing (not just reasoned about) that
`not (gender in [1, 2])`, `not ('Jan' in given_name)`, and
`not (gender == None)` all already parsed and compiled correctly *before*
this change, since `not`/`==`/`in` were all Done and their
three-valued-logic composition was already fixed (see Done above).
`gender is None` and `gender not in [...]` failed only at the very first
gate (`op_type not in _COMPARE_OPS`) before any real logic ran -- so the
fix really was just wiring three more `ast` op types into that dispatch
table and reusing the existing `in`/`Not` branches verbatim, no new
machinery. See `is` / `is not` / `not in` under Done above for the actual
diff.

**B shipped at its original difficulty-2 estimate** -- see
[Operand ordering](#operand-ordering-value-op-field) under Done above for
the actual diff (a new `_is_count_call` helper, a `_FLIP_OP` table, and
`_translate_compare`'s classification generalized to both sides). Verified
both at the parser level and end-to-end against real SQLite execution, not
just wire-shape equivalence.

**D was re-estimated *up*, not down, once actually checked** -- the
opposite direction from B/C. Before implementing, D looked like it should
be cheaper than its original 2 (B's operand-classification pattern seemed
directly reusable); checking the actual `Contains`/evaluator code first
showed it overrides `compile()` entirely with no field-vs-field support at
all, unlike the base `Comparison` class B's pattern lives on. Landed at its
original difficulty-2 estimate, plus an unplanned, larger side-fix: a
pre-existing silent correctness bug in the field-vs-field mechanism itself
(flat-column-vs-flat-column silently comparing against a literal instead
of the other column), found only because D's own tests were the first ones
to try two bare flat columns directly. See
[Field-vs-field substring `in`](#field-vs-field-substring-in-other_field-in-field)
under Done above.

**A shipped at its original difficulty-1 estimate**, and needed no
asterisk this time -- unlike D, A's premise (that B, already shipped,
would make each leg of a chain "just work" regardless of operand order)
held exactly as predicted. Confirmed the originally-motivating combined
example (`Date(...) < mother.birth.sortval < Date(...)`, the reason B and A
were split apart in the first place) compiles correctly, needing both
pieces together. See
[Chained comparisons](#chained-comparisons-1--gender--3) under Done above.

**E shipped at its original difficulty-1 estimate on the surface, but its
own premise was wrong, the same way F's was** -- checked Gramps' actual
object model before assuming "several missing relationships" was accurate,
and found exactly one real candidate (`Place.enclosed_by`), not several --
"a person's other events," the item's own motivating example, turned out
not to be a one-to-one gap at all. The registry entry itself really was
difficulty 1, as predicted -- but registering it surfaced a second,
unplanned `RelatedObject` self-reference bug (see
[`Place.enclosed_by`](#placeenclosed_by----one-to-one-self-reference-item-e)
under Done above), the same class of issue as the `Collection`
self-reference bug found while shipping "More relationships/collections."
Two for two now: every self-referencing relationship/collection registered
in this project so far has exposed a latent same-table-shadowing bug the
first time it was tried, in a part of the rendering code that had never
needed to handle it before.

### Dependencies between the items above

Checked directly against `_translate_compare`'s actual branches rather than
assumed -- most items are independent, but a few share code paths or make
each other cheaper:

- **C and D had no real dependency on anything else here** (both now moot,
  since both shipped). C was already ~90% built by composition of
  already-Done pieces (see the note above). D's predicted dependency on
  B (the `_is_path_node` check B proved out) turned out correct as far as
  it went -- D's `in`-branch does call the same `_is_path_node(node.left)`
  check on the way to `{"column": ..., "op": "contains", "value_column":
  ...}` -- but that piece was the *easy* part of D. The larger, unpredicted
  piece (the `Contains`/evaluator compiler work, plus the pre-existing
  flat-column field-vs-field bug) had no relationship to B at all -- it was
  a gap in `Contains`'s own `compile()` override, not in
  `_translate_compare`'s operand classification.
- **B and F turned out to both already be moot, for different reasons.**
  B has shipped (see Done above). F's premise was already stale before
  this session touched it -- `_CONSTANT_CLASSES` covers the full
  `GrampsType` space, verified directly. The "shared code path" observation
  (both flow through `_is_path_node`/`_CONSTANT_CLASSES`'s constant-vs-path
  disambiguation) is now just a description of `_translate_compare`'s
  actual code, not a planning note.
- **G is the proven template H should copy, not a hard prerequisite.**
  `count()` (already Done) established the "a column can be a computed
  value" pattern; G copies it almost verbatim. H (arbitrary functions)
  should copy the same pattern a third time rather than invent a new one.
  H doesn't strictly need G to land first -- `count()` alone already
  proves the pattern -- but G gives H a second worked example to
  generalize from, lowering H's risk.
- **G/H would still need to extend B's classification, if their scope ever
  grows to allow either side.** B's classification
  (`_is_path_node(node) or _is_count_call(node)`) only recognizes plain
  paths and `count(...)` specifically -- it has no notion of a future
  `Length`/arbitrary-function-call computed column at all. If G/H ever
  want their own computed-column kind recognized on *either* side
  (`1 < len(x)`), that check needs a new case added, the same way
  `_is_count_call` was added alongside `_is_path_node` for B. `len()`'s
  recommended v1 scope (left-hand-side only, against a literal) sidesteps
  this on purpose, so it isn't a dependency today -- just a seam that
  reopens if that scope grows later.
- **I depends on `exists()` (already Done), not on G.** `any()`'s
  recursive condition-parsing trick (a synthetic empty-column
  `ObjectTypeSpec` forcing every field reference through `JsonPath`) is
  exactly what `exists()` already does. `len()` isn't a real prerequisite --
  nesting `len()` inside an `any()` condition is a plausible nice-to-have,
  not something I structurally depends on.
- **E, J, K were/are independent islands.** E (shipped) was pure registry
  entries plus its own `RelatedObject`-rendering fix, sharing no code with
  A-D/F-I -- its dependency was on the *actual object model*, not on
  anything else in this list, which is exactly what made "just a registry
  entry" an incomplete prediction. J extends `exists`/`count`'s
  already-Done machinery to see two rows at once -- orthogonal to A-I. K
  lives mostly in `order_by`/keyset pagination, a different subsystem from
  `where_expr` compilation -- touches neither `_translate_compare` nor
  `evaluator.py`'s comparison logic. **Update, now that K is scoped:** its
  one piece of overlap with `where_expr` compilation is
  `_render_json_path`'s `CAST` selection, which K's static schema-walk type
  (rather than a runtime comparison value) would drive for *both* code
  paths once built -- a shared improvement, not new coupling, since a
  field-vs-field `where` comparison already has no `value` to drive that
  logic from today.
- **L shipped downstream of K, capped rather than independent, exactly as
  predicted.** L's own recommended v1 scope (flat columns only) landed
  deliberately capped at whatever K still supports today, so the evaluator
  path didn't leapfrog the SQL path's own `order_by` capability -- L shares
  no rendering code with K at all (K is pure SQL generation in `query.py`,
  L is a pure Python sort/seek pass in `proxied_query.py`), but its scope
  boundary is a direct function of K's. **Update, now that K is scoped:**
  closing L's cap turns out to need none of K's schema-walk machinery --
  Python's `<`/`>` don't need a `CAST` hint the way SQL does, so L can
  likely widen its one `check_columns` call as soon as K's structural
  change (`OrderBy.column: ColumnRef`) lands, without waiting on K's
  schema-walk type resolver at all. If K ever ships (SQL path gains
  `JsonPath`/`RelatedObject` sort columns), L's cap is free to move with it
  -- nothing about L's implementation assumes flat-columns-only beyond the
  one `check_columns` whitelist call, so widening it later is additive, not
  a rewrite.

**Net effect on build order (as originally planned, before A/B/C/D
shipped):** B was the one item worth doing early even though its own
difficulty (2) wasn't special on its own -- it was the only item on this
list with downstream leverage (cheapens D, unblocks A, and is the seam G/H
would need if their scope grows). Actual order ended up C, B, D, A --
C shipped first since it turned out to be nearly free once checked
directly; A shipped last, deliberately, since it was blocked on B (a chain
with a value on the left of its first leg needs operand ordering solved
first) -- landed exactly at its predicted difficulty once its prerequisite
was actually in place. **D is the one lesson from this whole exercise worth
remembering going forward**: "looks cheap because a related item just
shipped" is a hypothesis to check against the actual code (does the target
class already have the pattern B/C proved, or does it override behavior
entirely, like `Contains` did?), not a difficulty estimate to trust on its
own -- D's own difficulty barely moved (2 either way), but the *reason*
moved entirely, from "reuse B's classifier" to "`Contains` never had
field-vs-field support at all, and neither did anything
flat-column-vs-flat-column."

**E adds a second, related lesson**: a difficulty-1 "just a registry
entry" item can still hide a real bug if it's the *first* instance of a
shape nothing before it exercised -- here, the first self-referencing
`RelatedObject`. This is now a pattern, not a one-off: both self-reference
cases tried so far (`Person.associations` as a `Collection`,
`Place.enclosed_by` as a `RelatedObject`) each broke on their first attempt,
in each mechanism's own previously-untested same-table-shadowing corner.
Worth checking proactively before registering any *future* self-referencing
relationship or collection, rather than waiting to find it by accident
again a third time.
