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
- No chained comparisons (`1 < gender < 3`) -- write `gender > 1 and gender < 3`
  instead. (docs/where_expr.md)
- No `is`, `is not`, `not in`. (docs/where_expr.md)

**`in` / substring**
- `'text' in field` (the substring-test shape) requires a string *literal* on
  the left -- `other_field in field` (comparing two fields for substring
  containment) isn't supported, only a literal against a field.
  (docs/where_expr.md)

**Relationships**
- Only five one-to-one relationship links are registered: `Person` ->
  `birth`/`death` (-> `Event`), `Family` -> `father`/`mother` (-> `Person`),
  `Event` -> `place` (-> `Place`). Anything else one-to-one -- a person's
  other (non-birth/death) events, a citation's source, ... -- has no
  relationship path at all yet, only whatever's reachable as a JSON path
  within one record's own `json_data`.
- Two one-to-many **collections** are registered, queryable via
  `exists(name, condition)`/`count(name, condition)` (see `docs/where_expr.md`'s
  "One-to-many relationships"/"Counting a collection" sections): `Family` ->
  `children` (-> `Person`, via `child_ref_list`) and `Person` -> `notes`
  (-> `Note`, via `note_list`). Everything else one-to-many -- a family's/
  person's other event refs, citations, sources, media, tags, associations
  (`person_ref_list`) -- has no collection registered yet; see "More
  relationships" under Possibilities below. There's still no `len()` over a
  plain intra-record JSON array (`primary_name.surname_list`, not a
  registered `Collection`), no `any(...)` over one either, and no way for an
  `exists(...)`/`count(...)` condition to reference the *outer* row (e.g. "a
  child with the same surname as the father") -- all three flagged as
  follow-ups, not solved here.

**Values and functions**
- Only two whitelisted function-call forms exist: `like(field, 'pattern')`
  and `Date('...')`. No arithmetic, string functions (`upper()`, `lower()`,
  concatenation), or general function calls.
- `ClassName.CONST` constants are wired for exactly three classes today
  (`Person.{MALE,FEMALE,UNKNOWN,OTHER}`, `Citation.CONF_*`,
  `Note.{FLOWED,FORMATTED}`) -- the much larger `GrampsType` constant space
  (`EventType.BIRTH`, `FamilyRelType.MARRIED`, `NameType.*`, ...) isn't
  wired up, and those types additionally support arbitrary user-defined
  custom values with no fixed constant to name anyway. (query_lang.py)
- `count(...)` answers "how many" for a registered `Collection`
  (`count(children) > 2`, see Done below) -- there's still no `len()` for a
  plain intra-record JSON array not backed by a `Collection`
  (`primary_name.surname_list`), and no other aggregates. See
  [`len()` / array-length comparisons](#len--array-length-comparisons) below
  for a scoped-out example of what closing that part of the gap would take.

**Sorting / pagination**
- `order_by` and keyset pagination (`after`) only work against flat SQL
  columns -- a `JsonPath` or `RelatedObject` field (`primary_name.surname_list[0].surname`,
  `birth.date.sortval`) can be filtered on but not sorted by. (query.py)

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

## Possibilities

### `len()` / array-length comparisons

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

### Other gaps (not yet scoped to this level of detail)

- **More relationships**:
  - One-to-one candidates (non-birth/death events, a citation's source, ...)
    -- mechanically similar to the existing five, a `_RELATIONSHIPS` registry
    entry each.
  - One-to-many candidates beyond `children`/`notes` (event refs beyond
    birth/death, citations, sources, media, tags, `person_ref_list`
    associations) -- now that `exists(...)`/`Collection` exist (see Done
    above), each of these is "one `_COLLECTIONS` registry entry," the same
    low cost the one-to-one candidates already have -- no new machinery
    needed, just registering more (ref-object-list vs. flat-handle-list, the
    two shapes already proven).
  - `exists(...)` condition referencing the *outer* row (e.g. "a child with
    the same surname as the father") -- not supported; would need the
    condition's column resolution to see two rows (target *and* outer) at
    once, which nothing here does today.
- **LIKE case-sensitivity parity across dialects** -- smallest fix here:
  render `ILIKE` on PostgreSQL for `like(...)`/substring-`in`, or apply a
  case-insensitive collation. Needs a test that actually runs both dialects
  and compares results, since nothing currently catches this gap.
- **Sortable JSON/relationship columns** -- `order_by`/keyset pagination
  would need to accept a `JsonPath`/`RelatedObject` the way `where` already
  does, plus decide how keyset comparison and `COLLATE` selection behave
  for a column whose type isn't known until runtime.
