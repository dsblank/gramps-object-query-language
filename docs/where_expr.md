# `where_expr`: an "almost Python" filter language for Gramps objects

`where_expr` is a small filter expression language for querying Gramps
objects (`Person`, `Family`, `Event`, `Place`, ...). It looks like a single
Python boolean expression, and is written against one object type at a time:

```
Person "gender == Person.MALE"
Family "mother.death.date.sortval < father.death.date.sortval"
```

The first word (`Person`, `Family`, ...) says which object type the
expression is evaluated against; the quoted string is the expression itself.
Exactly which fields exist to query depends on that type -- `gender` is a
`Person` field, `father`/`mother` are `Family` fields, and so on (see
[Fields and relationships](#fields-and-relationships) below).

It is **not** general Python: it is parsed as syntax only (`ast.parse`,
never `eval`/`exec`), and only a small, explicitly whitelisted set of node
shapes is understood. Anything else -- `or`, `not`, chained comparisons,
list/dict comprehensions, lambdas, arbitrary function calls, f-strings,
imports -- is rejected outright rather than silently misinterpreted.

Every example on this page is executed against a real (in-memory) SQLite
database in
[`test_where_expr_examples.py`](../gramps_object_query_language/tests/test_where_expr_examples.py),
so what you read here is what actually runs.

## Basic comparisons

A comparison is `field OP value`, where `OP` is one of `== != < <= > >=`:

```python
Person "surname == 'Smith'"
Person "gender == Person.MALE"
```

`Person.MALE` is one of a handful of named constants (see
[Constants](#constants)) -- `gender == Person.MALE` and `gender == 1` compile
to exactly the same thing.

## Combining conditions with `and`

Multiple comparisons can be joined with `and`:

```python
Person "gender == Person.MALE and surname == 'Smith'"
```

`or` and `not` have no representation in the current query format and are
rejected -- as are chained comparisons like `1 < gender < 3` (write
`gender > 1 and gender < 3` instead).

## `in`, contains, and `like`

```python
Person "given_name in ['John', 'Jane']"
Person "'Jan' in given_name"
Person "like(given_name, 'J%')"
```

`in` has two shapes, told apart by what's on its right:

- `field in [v1, v2, ...]` -- list membership; the list must be a
  non-empty literal.
- `'substring' in field` -- a plain substring test (no wildcards): does
  `field`'s value contain the literal string on the left? This mirrors
  what `in` already means for two real Python strings (`'Jan' in 'Jane'`
  is `True` in plain Python too), just extended to a field reference on
  the right instead of a second literal. The left side must be a string
  literal -- `other_field in field` (both sides paths) isn't supported.

Note the reversed order compared to every other operator here: `field`
sits on the *left* for `==`, `<`, `in [...]`, and `like(...)`, but on the
*right* for the substring form of `in` -- because that's the order real
Python uses for substring tests.

`like(field, 'pattern')` is a whitelisted function-call form standing in
for SQL's `LIKE` (`%` matches any run of characters, `_` matches exactly
one) -- it isn't a Python operator, so it can't be spelled
`field like 'pattern'`. Unlike `'substring' in field`, `like(...)`'s
pattern is used as-authored: wildcards in it are real wildcards, and it's
your job to write `'%accident%'` rather than `'accident'` if that's what
you mean. `'substring' in field`, by contrast, always searches for the
substring literally -- if it happens to contain a `%` or `_`, that
character is escaped so it's matched literally too, not reinterpreted as
a wildcard.

## Fields and relationships

A field reference is a dotted/indexed path: `gender`, `primary_name.surname_list[0].surname`,
`birth.date.sortval`. Three kinds of path segment exist:

- **A flat column** -- one of the type's own indexed fields (`gender`,
  `surname`, `handle`, `father_handle`, ...). Fast: these are real SQL
  columns.
- **A path into JSON** -- anything else, e.g. `primary_name.first_name` or
  `attribute_list[0].value`. Still queryable, just not as fast as a flat
  column.
- **A relationship** -- a field name that instead points at a *different*
  object, letting a path cross into it and keep going. Currently registered:

  | On a...  | ...`name` traverses to | via |
  |----------|------------------------|-----|
  | `Person` | `birth`  -> `Event`    | the person's birth event |
  | `Person` | `death`  -> `Event`    | the person's death event |
  | `Family` | `father` -> `Person`   | `father_handle` |
  | `Family` | `mother` -> `Person`   | `mother_handle` |
  | `Event`  | `place`  -> `Place`    | the event's place |

  A relationship name needs something after it (`birth.date`, not just
  `birth`), and chains freely -- `birth.place.title` is `Person` ->
  (birth) `Event` -> (place) `Place` -> `title`:

  ```python
  Person "birth.date.sortval >= 2439857"
  Person "birth.place.title == 'Chicago, Cook, Illinois, USA'"
  Family "father.surname == 'Smith'"
  ```

That's five relationship links registered today, in total -- every example
in [Genealogy examples](#genealogy-examples) below exercises at least one of
them, and several combine two or three at once.

Under the hood, each relationship hop compiles to a correlated SQL
subquery -- not a `JOIN` -- so sibling hops through the same table (a
family's father and mother are both `Person` rows) and multi-level chains
both compose correctly.

### Field-vs-field comparisons

The right-hand side of a comparison can be a path too, not just a literal --
comparing two fields on the same row (or reached via relationships) to each
other:

```python
Family "father.surname == mother.surname"
Family "mother.death.date.sortval < father.death.date.sortval"
```

The second example is the motivating one: "families where the mother died
before the father" -- both sides cross a relationship (`Family` -> `Person`
-> `Event`) and are compared directly, with no literal value involved at
all.

`field in [...]` always expects a list literal on the right, never a
field-vs-field form -- and the substring form of `in` always expects a
string literal on the left, never a field-vs-field form either
(`other_field in field` is rejected, not interpreted as "does field
contain other_field's value").

## Genealogy examples

One example of each of the five registered relationship links, plus a few
that combine several -- all against the same small, two-generation fixture
(`test_where_expr_examples.py`): John and Jane Smith (`fam1`), their child
Robert, and John's parents William Smith and Mary Doyle (`fam2`).

**`Person -> birth -> Event`** -- a person's own birth event:

```python
Person "birth.date.sortval >= Date('Jan 1, 1968')"
```

**`Person -> death -> Event`** -- a person's own death event, and not just
its date; any of the event's fields are reachable the same way:

```python
Person "like(death.description, '%accident%')"
Person "'accident' in death.description"
```

(The two are equivalent here -- `'accident' in death.description` is just
the substring-test spelling of the same query.)

**`Family -> father -> Person`** / **`Family -> mother -> Person`** -- a
family's parents:

```python
Family "father.surname == 'Smith'"
Family "mother.given_name == 'Mary'"
```

**`Event -> place -> Place`** -- works starting directly from an `Event`
query too, not just reached via a `Person`'s birth/death:

```python
Event "place.title == 'Chicago, Cook, Illinois, USA'"
```

**Chaining two relationships** -- `Family` -> `father` (-> `Person`) ->
`birth` (-> `Event`) -> `date.sortval`:

```python
Family "father.birth.date.sortval < Date('Jan 1, 1850')"
```

**Chaining three relationships in one field-vs-field comparison** --
`birth`/`death` (both `Person -> Event`) combined with `place`
(`Event -> Place`) on both sides at once:

```python
Person "birth.place.title == death.place.title"
```

**Chaining four relationships at once** -- `father`/`mother`
(`Family -> Person`) combined with `death` (`Person -> Event`) and `place`
(`Event -> Place`) on both sides:

```python
Family "father.death.place.title == mother.death.place.title"
```

## Constants

`ClassName.CONST` reads a named constant straight off the real Gramps class,
so it can never drift out of sync with the class's actual values:

| Class      | Constants |
|------------|-----------|
| `Person`   | `MALE`, `FEMALE`, `UNKNOWN`, `OTHER` |
| `Citation` | `CONF_VERY_LOW`, `CONF_LOW`, `CONF_NORMAL`, `CONF_HIGH`, `CONF_VERY_HIGH` |
| `Note`     | `FLOWED`, `FORMATTED` |

```python
Person "gender == Person.MALE"
Citation "confidence >= Citation.CONF_HIGH"
```

## Dates

`Date('...')` parses a human-readable date string with Gramps' own date
parser and resolves to `.sortval` -- a plain comparable integer (a Julian
day number) -- so it can be compared with the ordinary numeric operators:

```python
Person "birth.date.sortval >= Date('Jan 1, 1968')"
```

## What's *not* supported

- `or`, `not` -- no representation in the current query format yet.
- Chained comparisons (`1 < gender < 3`) -- write `gender > 1 and gender < 3`.
- `is`, `is not`, `not in` -- only `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`
  (both its list-membership and substring-test shapes), and `like(...)`
  are recognized.
- `other_field in field` -- the substring-test shape of `in` requires a
  string *literal* on the left, not a second field (see
  [Field-vs-field comparisons](#field-vs-field-comparisons)).
- Arbitrary function calls, lambdas, comprehensions, f-strings, imports --
  the parser whitelists node *shapes*, so anything it doesn't explicitly
  recognize is rejected, not silently ignored.

## Using it from Python

```python
from gramps_object_query_language.query_lang import compile_expr
from gramps_object_query_language.query import Query, compile_query, Dialect

spec, where = compile_expr("Family", "mother.death.date.sortval < father.death.date.sortval")
sql, params = compile_query(spec, Query(select=["handle"], where=where), dialect=Dialect.SQLITE)
rows = connection.execute(sql, params).fetchall()
```

`compile_expr(namespace, expr)` parses and translates a `where_expr` string
in one step, returning the matching `ObjectTypeSpec` alongside a `where` AST
ready for `compile_query`/`compile_count_query`. If you only need the
intermediate JSON shape (e.g. to send over an HTTP API as a `where_expr`
request field), use `parse_expr`/`parse_expr_for_spec` from the same module
instead.

For a database you can't run raw SQL against (a proxied/privacy-filtered
database), `gramps_object_query_language.evaluator.evaluate_where` evaluates
the same AST directly against real Gramps objects instead of compiling to
SQL -- see the [README](../README.md) for the module overview.
