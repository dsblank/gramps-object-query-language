# Roadmap

This is a running list of where `where_expr` currently draws its lines, and
what's been scoped out as a possible next step. It's a design/planning
document, not user-facing reference -- see
[`README-query-language.md`](README-query-language.md) and
[`docs/where_expr.md`](docs/where_expr.md) for what the language actually
does today.

## Current limitations

**Boolean structure**
- No `not` -- a whole condition can't be negated. (README-query-language.md,
  docs/where_expr.md) `and`/`or` are both supported, and can be mixed and
  nested (`(a and b) or c`), following Python's own precedence.
- No chained comparisons (`1 < gender < 3`) -- write `gender > 1 and gender < 3`
  instead. (docs/where_expr.md)
- No `is`, `is not`, `not in`. (docs/where_expr.md)

**`in` / substring**
- `'text' in field` (the substring-test shape) requires a string *literal* on
  the left -- `other_field in field` (comparing two fields for substring
  containment) isn't supported, only a literal against a field.
  (docs/where_expr.md)

**Relationships**
- Only five relationship links are registered at all: `Person` -> `birth`/
  `death` (-> `Event`), `Family` -> `father`/`mother` (-> `Person`), `Event`
  -> `place` (-> `Place`). Anything else -- a family's children, a person's
  other (non-birth/death) events, notes, citations, sources, media, tags,
  attributes -- has no relationship path at all, only whatever's reachable
  as a JSON path within one record's own `json_data`.
- Every relationship is **one-to-one** (a correlated subquery with
  `LIMIT 1`). There's no way to ask a one-to-many question at all -- "a
  family with *any* child born before 1900," "a person with *any* citation
  below a confidence threshold" -- the compiler has no `EXISTS`/`ANY`
  construct, only "the one related row reached via this fixed handle
  reference."

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
- No way to ask "how many" of anything -- no `len()`, no `count()`, no
  aggregates. See [`len()` / array-length comparisons](#len--array-length-comparisons)
  below for a scoped-out example of what closing part of this gap would take.

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

## Possibilities

### `len()` / array-length comparisons

Motivated by: "does a person have more than one surname recorded?" --
today only answerable indirectly, by indexing a fixed position
(`primary_name.surname_list[1].surname != None`, see
README-query-language.md's cookbook) rather than asking for a count
directly.

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

### `not`

The remaining boolean-structure gap now that `or` is done (see "Done"
below). Expected to be small: `query.py`'s `Not` class and `evaluator.py`'s
`Not` branch already exist and are already tested (same as `Or` was before
`or` support landed) -- the work is almost entirely in `query_lang.py`,
teaching `_translate_bool_or_leaf` to also recognize `ast.UnaryOp` with
`ast.Not` and produce a `{"not": node}` wire shape, plus a matching case in
`_node_from_json`. No new SQL, no dialect work, no NULL-semantics decisions
-- the same reason `or` turned out to be smaller than originally estimated
here.

### Other gaps (not yet scoped to this level of detail)

Each maps to a limitation above; none has had the same close look as
`len()` yet:

- **More relationships** (children, non-birth/death events, notes,
  citations, sources, media, tags) -- mechanically similar to the existing
  five (a `_RELATIONSHIPS` registry entry each), *if* they stay one-to-one.
  A family's children, or "any citation below confidence X," are one-to-many
  and need real `EXISTS`-style semantics the correlated-subquery-with-`LIMIT
  1` design doesn't have today -- a bigger, separate piece of work.
- **LIKE case-sensitivity parity across dialects** -- smallest fix here:
  render `ILIKE` on PostgreSQL for `like(...)`/substring-`in`, or apply a
  case-insensitive collation. Needs a test that actually runs both dialects
  and compares results, since nothing currently catches this gap.
- **Sortable JSON/relationship columns** -- `order_by`/keyset pagination
  would need to accept a `JsonPath`/`RelatedObject` the way `where` already
  does, plus decide how keyset comparison and `COLLATE` selection behave
  for a column whose type isn't known until runtime.
