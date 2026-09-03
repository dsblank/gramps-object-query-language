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
shapes is understood. Anything else -- list/dict comprehensions, lambdas,
arbitrary function calls, f-strings, imports -- is rejected outright rather
than silently misinterpreted.

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

The field doesn't have to be on the left -- `5 < gender` and `gender > 5`
compile to the identical query, and likewise for every other operator here
(`<`/`>` and `<=`/`>=` swap with each other when the field moves sides;
`==`/`!=` don't need to change):

```python
Person "Date('Jan 1, 1968') < birth.date.sortval"
Person "birth.date.sortval > Date('Jan 1, 1968')"
```

These two lines are the same query. (This doesn't extend to `count(...)`,
which is only ever recognized as the left-hand operand -- see
[Counting a collection](#counting-a-collection-count).)

## Combining conditions with `and`, `or`, and `not`

Multiple comparisons can be joined with `and`:

```python
Person "gender == Person.MALE and surname == 'Smith'"
```

...or with `or`:

```python
Person "given_name == 'John' or surname == 'Doyle'"
```

`and`/`or` can be mixed in one expression, and follow the same precedence
and grouping real Python uses -- `and` binds tighter than `or`, so

```python
Person "gender == Person.MALE and surname == 'Smith' or given_name == 'Mary'"
```

reads as `(gender == Person.MALE and surname == 'Smith') or given_name ==
'Mary'`, not `gender == Person.MALE and (surname == 'Smith' or given_name ==
'Mary')`. Parentheses group explicitly, exactly as in Python:

```python
Person "(gender == Person.MALE and surname == 'Smith') or given_name == 'Mary'"
```

`not` negates a single condition (or a parenthesized group):

```python
Person "not (surname == 'Smith')"
```

`not` binds tighter than `and`, which binds tighter than `or`, matching
Python -- `not gender == Person.MALE and surname == 'Smith'` reads as
`(not gender == Person.MALE) and surname == 'Smith'`, not `not (gender ==
Person.MALE and surname == 'Smith')`. As always, use parentheses when that
matters:

```python
Person "not (gender == Person.MALE and surname == 'Smith')"
```

`not` composes with `like(...)` and the substring form of `in` too, not
just plain comparisons: `not like(given_name, 'J%')`, `not ('Jan' in
given_name)`.

One thing worth knowing: `not` follows the same three-valued logic SQL
does for a missing value -- negating a condition that's *unknown* (because
it depends on a value that isn't recorded, e.g. `death.date.sortval` for
someone still living) stays unknown, and an unknown condition never
matches a `WHERE` clause, whether or not it's negated. So `not
(death.date.sortval < Date('Jan 1, 2100'))` still excludes someone with no
recorded death date, the same as the un-negated form does -- `not` doesn't
turn "we don't know" into "yes."

Chained comparisons work the same as real Python's do -- `1 < gender < 3`
means exactly `1 < gender and gender < 3`, evaluated as two independent
comparisons joined with `and`:

```python
Person "1 < gender < 3"
Person "gender > 1 and gender < 3"
```

These two lines compile to the same thing. Any comparison operator can
appear in a chain, including a mix (`1 < gender != 3`), and either side of
each leg can be a value or a field the same as any other comparison
(see [Basic comparisons](#basic-comparisons) above) --
`Date('Jan 1, 1968') < birth.date.sortval < Date('Jan 30, 1968')` works too.

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

## `regex(...)`

```python
Person "regex(given_name, '^(John|Jane)$')"
```

`regex(field, 'pattern')` is another whitelisted function-call form, for a
regex search SQL has no operator syntax for. Unlike `like(...)`'s `%`/`_`
wildcards, `pattern` is a real regular expression, matched unanchored (a
substring search, like Python's `re.search` -- not `re.fullmatch`, so
`regex(given_name, 'ohn')` matches `"John"` too) and case-sensitively
(unlike `like(...)`/`in`'s substring form, which both match
case-insensitively).

Two different regex engines actually run a `regex(...)` condition,
depending on the database backend: SQLite runs it through a small Python
function (so it's exactly Python's `re` syntax), while PostgreSQL uses its
own native operator (POSIX "advanced regular expressions"). The two mostly
agree, but not entirely -- PostgreSQL's flavor has no lookahead/lookbehind
and no `(?P<name>...)` named groups. Stick to plain character classes
(`\d`, `\w`, `\s`), quantifiers, alternation, groups, and anchors if the
same query needs to run correctly against both backends.

## `is`, `is not`, and `not in`

```python
Person "mother is None"
Person "mother is not None"
Person "gender not in [Person.FEMALE, Person.OTHER]"
Person "'Jan' not in given_name"
```

`is`/`is not` are exactly `==`/`!=` -- this language has no notion of object
identity distinct from value equality, so `mother is None` and
`mother == None` compile to the identical wire node, and read the same as
`gender is Person.MALE`/`gender == Person.MALE` do. `not in` is `in`
negated -- both of `in`'s shapes (list membership and substring test) work
the same on the left of `not in`, since `x not in y` is just `not (x in y)`
compiled directly, the same composition you could already write by hand
with an explicit `not (...)`.

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

  | On a...   | ...`name` traverses to | via |
  |-----------|------------------------|-----|
  | `Person`  | `birth`  -> `Event`    | the person's birth event |
  | `Person`  | `death`  -> `Event`    | the person's death event |
  | `Family`  | `father` -> `Person`   | `father_handle` |
  | `Family`  | `mother` -> `Person`   | `mother_handle` |
  | `Event`   | `place`  -> `Place`    | the event's place |
  | `Citation`| `source` -> `Source`   | `source_handle` |
  | `Place`   | `enclosed_by` -> `Place` | the place that encloses this one (e.g. a city's county) |

  A relationship name needs something after it (`birth.date`, not just
  `birth`), and chains freely -- `birth.place.title` is `Person` ->
  (birth) `Event` -> (place) `Place` -> `title`. `enclosed_by` is
  self-referencing, so it chains with itself too --
  `enclosed_by.enclosed_by.title` reaches two levels up (a city's *state*,
  say, skipping the county in between):

  ```python
  Person "birth.date.sortval >= 2439857"
  Person "birth.place.title == 'Chicago, Cook, Illinois, USA'"
  Family "father.surname == 'Smith'"
  Citation "source.title == 'Census Records'"
  Place "enclosed_by.title == 'Cook County'"
  ```

That's seven relationship links registered today, in total -- every example
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
field-vs-field form. The substring form of `in`, though, now supports a
field on the left too:

```python
Family "father.surname in mother.surname"
```

This reads "does the mother's surname contain the father's" -- the same
substring test as `'Jan' in given_name`, just with a field standing in for
the literal. Works the same whether both fields cross a relationship (as
above) or are plain columns on the same row (`Person "given_name in
surname"`).

## One-to-many relationships: `exists(...)`

Every relationship in the table above is one-to-one -- a family has exactly
*one* father, a person has exactly *one* birth event. Some relationships are
naturally one-to-many instead -- a family has any number of children, a
person can have any number of notes -- and those need a different construct:
`exists(name, condition)`, a whitelisted function-call form (like
`like(...)`), not an ordinary path:

```python
Family "exists(children, given_name == 'Steve')"
Family "not exists(children, given_name == 'Steve')"
Family "exists(children)"
```

`children`/`notes`/... are **collection** names -- registered separately
from the relationship table above, and never usable as a dotted-path
segment (`children.surname` would be ambiguous: which child?), only as
`exists`'s (or `count`'s, see below) first argument. Registered on every one
of the ten record types, following Gramps' own object model exactly (a
`Source` has no `citations` since a source doesn't cite other citations; a
`Repository` has neither `citations` nor `media`; `Tag` has none at all --
a tag doesn't tag itself):

| On a...     | ...`name`                    | reaches | via |
|-------------|-------------------------------|---------|-----|
| `Person`    | `notes` -> `Note`             | attached notes | `note_list` |
| `Person`    | `citations` -> `Citation`     | attached citations | `citation_list` |
| `Person`    | `media` -> `Media`            | attached media | `media_list` |
| `Person`    | `tags` -> `Tag`               | attached tags | `tag_list` |
| `Person`    | `families` -> `Family`        | families as a spouse/parent | `family_list` |
| `Person`    | `parent_families` -> `Family` | families as a child | `parent_family_list` |
| `Person`    | `associations` -> `Person`    | other people linked via an association | `person_ref_list` |
| `Person`    | `events` -> `Event`           | every recorded event, not just birth/death | `event_ref_list` |
| `Family`    | `children` -> `Person`        | the family's children | `child_ref_list` |
| `Family`    | `notes`/`citations`/`media`/`tags` | (as above) | (as above) |
| `Family`    | `events` -> `Event`           | family events (marriage, divorce, ...) | `event_ref_list` |
| `Event`     | `notes`/`citations`/`media`/`tags` | (as above) | (as above) |
| `Place`     | `notes`/`citations`/`media`/`tags` | (as above) | (as above) |
| `Place`     | `enclosing_places` -> `Place` | the place(s) this place is inside of | `placeref_list` |
| `Source`    | `notes`/`media`/`tags`       | (as above) | (as above) |
| `Source`    | `repositories` -> `Repository` | repositories holding this source | `reporef_list` |
| `Citation`  | `notes`/`media`/`tags`       | (as above) | (as above) |
| `Repository`| `notes`/`tags`               | (as above) | (as above) |
| `Media`     | `notes`/`citations`/`tags`   | (as above) | (as above) |
| `Note`      | `tags`                       | (as above) | (as above) |

`associations` is the one *self*-referencing collection here (`Person` ->
`Person`) -- worth knowing only because it's the one case where the SQL
compiler has to alias the related row's table so it doesn't collide with
the outer row's own table name; nothing about writing the query itself
changes, `exists(associations, ...)` reads and behaves exactly like any
other collection.

`condition` is a second, ordinary `where_expr` -- anything legal as a
top-level expression is legal here too (`and`/`or`/`not`, chained
relationships, even a nested `exists`) -- just evaluated against the
collection's target type (`Person`, for `children`) instead of the outer one.
It can be left out entirely (`exists(children)`), meaning "at least one
related row at all," with no further condition on it.

Under the hood, `exists(...)` compiles to a real `EXISTS (...)` subquery that
iterates the JSON array (`json_each` on SQLite, `jsonb_array_elements`/
`jsonb_array_elements_text` on PostgreSQL) joined against the target table by
handle -- not a correlated *scalar* subquery the way every relationship above
is, since there can be any number of matching rows, not just one.

One consequence worth knowing: unlike an ordinary comparison, `exists(...)`
never produces SQL's `UNKNOWN` -- a family with no children at all simply
has zero matching rows in the subquery, the same as a family whose children
don't happen to match `condition`, so `exists(...)` there is a definite
`False` either way (never `None`/"missing"). That means `not exists(...)`
is always plain negation, with none of the "a missing value under `not`
stays excluded, not included" three-valued-logic subtlety described above
for ordinary comparisons.

## Counting a collection: `count(...)`

`exists(...)` only answers "at least one" -- `count(name, condition)` asks
"how many," over the same registered collections:

```python
Family "count(children) > 2"
Family "count(children, gender == Person.MALE) >= 1"
```

Unlike `exists(...)`, `count(...)` isn't itself a condition -- it produces a
*number*, so it has to appear as the left-hand side of an ordinary
comparison (`count(children) > 2`, not a bare `count(children)`) the same
way a field reference does. `condition` is optional, exactly as with
`exists`, and parses the same way (a full nested `where_expr` against the
collection's target type); leaving it out (`count(children)`) counts every
related row, unfiltered.

```python
Family "count(children) in [0, 1]"
```

`count(...)` is deliberately narrower than a plain field: it's only
recognized on a comparison's left-hand side, never on the right and never
compared against another field or another `count(...)` (`count(a) ==
count(b)` isn't supported) -- the same restriction `len()`'s own array-length
form is planned to have (see `ROADMAP.md`), kept consistent between the two.

Under the hood, `count(...)` reuses `exists(...)`'s own subquery shape
verbatim, just wrapped as `(SELECT COUNT(*) FROM ...)` instead of
`EXISTS (SELECT 1 FROM ...)` -- a missing collection (no children recorded
at all) is `0`, not `NULL`, the same way `COUNT(*)` over zero matching rows
always is in SQL.

## Reverse references: `exists(backlinks)`

Every collection above reaches *outward* -- a family's own `children`, a
person's own `notes`. `backlinks` is the one collection that reaches the
other way: "does anything else point to *this* record at all?" It's
registered on every one of the ten record types the same way the
collections above are -- even `Tag`, which (unlike every collection in the
table above) has no *forward* collections of its own -- but it isn't a
field on the row itself the way `note_list`/`child_ref_list`/etc. are; it
comes from Gramps' own `reference` table, the same index
`find_backlink_handles()` uses internally to answer "what points here":

```python
Note "not exists(backlinks)"
Note "exists(backlinks, _class == 'Person')"
Note "exists(backlinks) and count(backlinks) > 1"
```

`_class` is the one field a `backlinks` condition can test -- the
referrer's own type (`"Person"`, `"Family"`, ...), matching the same field
name every record's own serialized JSON already uses for its type. Unlike
`children`/`notes`/every other collection above, `backlinks` has no single
target type to reach further into -- a backlink's referrer can be any of
the ten record types at once -- so `_class` can't be followed any deeper
(`_class.primary_name` isn't supported): `==`/`!=`/`is`/`is not`/`in`
against a class-name string (or a list of them, for `in`) is the whole
vocabulary:

```python
Note "exists(backlinks, _class in ['Person', 'Family'])"
Note "exists(backlinks, _class != 'Media')"
```

`count(backlinks)` works exactly like `count(...)` above -- `count(backlinks)
== 0` is another way to spell `not exists(backlinks)`. Reaching into the
referrer's own fields beyond its class (e.g. a Note referenced by a Person
whose surname is Smith) isn't supported yet -- see `ROADMAP.md`'s "Reverse
relationships" item for why that needs a genuinely different, per-class
construct rather than a straightforward extension of this one.

## Comprehension sugar: `any(...)` and `len([...])`

`exists(children, given_name == 'Steve')` reads reasonably close to plain
English, but for anyone more used to reaching for Python's own idiom, the
same query can be spelled as a generator expression instead:

```python
Family "any(c.given_name == 'Steve' for c in children)"
```

This is pure syntax sugar -- parsed and immediately rewritten into exactly
the `exists(...)` form above before anything else runs, so it compiles to
the identical query, not merely an equivalent one. `count(...)` has a
matching spelling, as a list comprehension inside `len(...)`:

```python
Family "len([c for c in children if c.given_name == 'Robert']) == 1"
```

which rewrites to `count(children, given_name == 'Robert') == 1`. Both
directions are checked directly in
[`test_query_lang.py`](../gramps_object_query_language/tests/test_query_lang.py)
(`parse_expr("any(...)") == parse_expr("exists(...)")`, and likewise for
`len([...])`/`count(...)`), not just documented as equivalent.

A few rules of thumb, all mirroring what `exists(...)`/`count(...)`
already support written by hand rather than adding anything new underneath:

- `any(...)`'s comprehension `elt` *is* the condition (`any(c.a == 1 for c
  in rel)`); a bare loop variable with no attribute after it (`any(c for c
  in rel)`) has no condition of its own, matching `exists(rel)` with
  nothing to filter on. An `if` clause on the generator ANDs in as an
  additional condition either way (`any(c for c in rel if c.a == 1)` and
  `any(c.a == 1 for c in rel)` compile identically).
- `len([...])`'s projection (its `elt`) is required to be trivial -- the
  loop variable itself, or a plain literal like `1` -- since unlike
  `any(...)`'s `elt`, it was never a condition to begin with; the condition
  comes entirely from the comprehension's `if` clause(s), if any.
- Only one `for` clause is allowed, and the loop variable must be a plain
  name (no tuple-unpacking) -- exactly what a single `exists`/`count` call
  already assumes.
- Nested comprehensions work, mapping onto nested `exists`/`count` calls
  the same way a hand-written nested call would --
  `any(any(e.type.value == EventType.BIRTH for e in c.events) for c in
  children)` is `exists(children, exists(events, type.value ==
  EventType.BIRTH))` -- but the inner `for ... in ...` is restricted to a
  bare collection name or exactly one attribute off the *enclosing*
  comprehension's own loop variable (`c.events`, not a longer chain), the
  same restriction `exists`'s own first argument already has.

`all(...)`/`sum(...)` aren't recognized -- there's no established
motivating query for them yet, and `all(...)` in particular would need a
double negation (`not exists(rel, not cond)`) under the hood to mean the
same thing SQL's `NOT EXISTS` doesn't already give you a shorter way to
reach for.

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

**`exists(children, ...)`** -- a family with at least one child matching a
condition, and its negation:

```python
Family "exists(children, given_name == 'Steve')"
Family "not exists(children, given_name == 'Steve')"
```

**`exists(children)`** -- a family with any recorded child at all, condition
omitted:

```python
Family "exists(children)"
```

**`exists(notes)`** -- starting from `Person` instead of `Family`, and over a
flat handle list (`note_list`) rather than a list of ref objects
(`child_ref_list`) -- the two collection shapes registered today, both
spelled the same way from `where_expr`:

```python
Person "not exists(notes)"
```

**`count(children, ...)`** -- how many children match a condition (or none,
to count every child):

```python
Family "count(children) > 0"
Family "count(children, given_name == 'Robert') == 1"
```

**`Citation -> source -> Source`** -- the newest one-to-one relationship
link, working the same way `father`/`mother`/`place` already do:

```python
Citation "source.title == 'Census Records'"
```

**A collection registered on a different type** -- `Citation -> source`
(one-to-one) combined with `Person -> citations` (one-to-many): people with
at least one high-confidence citation:

```python
Person "exists(citations, confidence >= Citation.CONF_HIGH)"
```

**`associations` (self-referencing)** -- a person linked to another person
by name, via an association:

```python
Person "exists(associations, given_name == 'Bob')"
```

## Constants

`ClassName.CONST` reads a named constant straight off the real Gramps class
(every all-caps `int` attribute the class defines -- see
`query_lang._int_constants`), so it can never drift out of sync with the
class's actual values, and picks up anything a future Gramps release adds
without code changes here:

| Class               | Constants |
|---------------------|-----------|
| `Person`             | `MALE`, `FEMALE`, `UNKNOWN`, `OTHER` |
| `Citation`           | `CONF_VERY_LOW`, `CONF_LOW`, `CONF_NORMAL`, `CONF_HIGH`, `CONF_VERY_HIGH` |
| `Note`               | `FLOWED`, `FORMATTED` |
| `Date`               | `MOD_NONE`, `MOD_BEFORE`, `MOD_AFTER`, `MOD_ABOUT`, `MOD_RANGE`, `MOD_SPAN`, `MOD_TEXTONLY`, `MOD_FROM`, `MOD_TO`, `QUAL_NONE`, `QUAL_ESTIMATED`, `QUAL_CALCULATED`, `CAL_GREGORIAN`, `CAL_JULIAN`, `CAL_HEBREW`, `CAL_FRENCH`, `CAL_PERSIAN`, `CAL_ISLAMIC`, `CAL_SWEDISH`, `NEWYEAR_JAN1`, `NEWYEAR_MAR1`, `NEWYEAR_MAR25`, `NEWYEAR_SEP1` |
| `EventType`          | `BIRTH`, `DEATH`, `MARRIAGE`, `DIVORCE`, `BURIAL`, ... (every standard Gramps event type) |
| `EventRoleType`      | `PRIMARY`, `WITNESS`, `FAMILY`, `CLERGY`, ... |
| `FamilyRelType`      | `MARRIED`, `UNMARRIED`, `CIVIL_UNION`, `UNKNOWN`, `CUSTOM` |
| `ChildRefType`       | `BIRTH`, `ADOPTED`, `STEPCHILD`, `FOSTER`, `SPONSORED`, `UNKNOWN`, `CUSTOM`, `NONE` |
| `NameType`           | `AKA`, `BIRTH`, `MARRIED`, `UNKNOWN`, `CUSTOM` |
| `NameOriginType`     | `PATRONYMIC`, `MATRONYMIC`, `INHERITED`, `GIVEN`, `TAKEN`, `PATRILINEAL`, `MATRILINEAL`, `FEUDAL`, `PSEUDONYM`, `OCCUPATION`, `LOCATION`, `NONE`, `UNKNOWN`, `CUSTOM` |
| `AttributeType`      | `CASTE`, `DESCRIPTION`, `ID`, `NATIONAL`, `NUM_CHILD`, `SSN`, `NICKNAME`, `CAUSE`, `AGENCY`, `AGE`, `FATHER_AGE`, `MOTHER_AGE`, `WITNESS`, `TIME`, `OCCUPATION`, `UNKNOWN`, `CUSTOM` |
| `UrlType`            | `EMAIL`, `WEB_HOME`, `WEB_SEARCH`, `WEB_FTP`, `UNKNOWN`, `CUSTOM` |
| `RepositoryType`     | `LIBRARY`, `CEMETERY`, `CHURCH`, `ARCHIVE`, `ALBUM`, `WEBSITE`, `BOOKSTORE`, `COLLECTION`, `SAFE`, `UNKNOWN`, `CUSTOM` |
| `SourceMediaType`    | `AUDIO`, `BOOK`, `CARD`, `ELECTRONIC`, `FICHE`, `FILM`, `MAGAZINE`, `MANUSCRIPT`, `MAP`, `NEWSPAPER`, `PHOTO`, `TOMBSTONE`, `VIDEO`, `UNKNOWN`, `CUSTOM` |
| `NoteType`           | `GENERAL`, `RESEARCH`, `TRANSCRIPT`, `PERSON`, `ATTRIBUTE`, `ADDRESS`, `ASSOCIATION`, `LDS`, `FAMILY`, `EVENT`, `EVENTREF`, `PLACE`, `REPO`, `REPOREF`, `SOURCE`, `SOURCEREF`, `CHILDREF`, `PERSONNAME`, `SOURCE_TEXT`, `HTML_CODE`, `TODO`, `LINK`, `ANALYSIS`, `REPORT_TEXT`, `CITATION`, `UNKNOWN`, `CUSTOM` |
| `PlaceType`          | `COUNTRY`, `STATE`, `COUNTY`, `CITY`, `PARISH`, `LOCALITY`, `STREET`, `PROVINCE`, `REGION`, `DEPARTMENT`, `NEIGHBORHOOD`, `DISTRICT`, `BOROUGH`, `MUNICIPALITY`, `TOWN`, `VILLAGE`, `HAMLET`, `FARM`, `BUILDING`, `NUMBER`, `UNKNOWN`, `CUSTOM` |
| `MarkerType`         | `NONE`, `COMPLETE`, `TODO_TYPE`, `CUSTOM` |
| `StyledTextTagType`  | `BOLD`, `ITALIC`, `UNDERLINE`, `FONTFACE`, `FONTSIZE`, `FONTCOLOR`, `HIGHLIGHT`, `SUPERSCRIPT`, `LINK`, `STRIKETHROUGH`, `SUBSCRIPT`, `NONE_TYPE` |
| `SrcAttributeType`   | `UNKNOWN`, `CUSTOM` |

Some of these attach to a real flat column (`Person.gender`,
`Citation.confidence`), most don't -- `Event`'s `type`, a `Family`'s
`type` (its relationship type), a name's `type`, and so on are all stored
nested in `json_data` as `{"_class": "EventType", "value": 12, "string":
""}`, so the field to compare is `<field>.value`, not `<field>` itself:

```python
Person "gender == Person.MALE"
Citation "confidence >= Citation.CONF_HIGH"
Event "type.value == EventType.BIRTH"
Family "type.value == FamilyRelType.MARRIED"
Person "primary_name.type.value == NameType.BIRTH"
```

These cover Gramps' built-in, fixed values for each type -- not a
tree's own custom type values (e.g. a `PlaceType` of "Ranch" someone typed
in), which have no fixed constant to name; only `.CUSTOM` identifies "this
is a custom one," not which.

## Dates

`Date('...')` parses a human-readable date string with Gramps' own date
parser and resolves to `.sortval` -- a plain comparable integer (a Julian
day number) -- so it can be compared with the ordinary numeric operators:

```python
Person "birth.date.sortval >= Date('Jan 1, 1968')"
```

`sortval` is a bare point on the calendar; it drops the date's modifier
(`MOD_ABOUT`, `MOD_BEFORE`, `MOD_AFTER`, `MOD_FROM`/`MOD_TO`) and quality
entirely, so e.g. a `MOD_BEFORE` date and a plain exact date for the same
year/month/day produce the same `sortval` (verified: year-only "before
1968" and exact "1968" both sort-value to the same JDN as `Date('Jan 1,
1968')`). For `MOD_SPAN`/`MOD_RANGE` dates, `sortval` is the start of the
range, not the end or a midpoint. Comparisons against `sortval` alone can't
distinguish "before X", "about X", "after X", or "X to Y" from plain "X" --
they only compare calendar position.

The modifier, quality, and raw values *are* separately reachable, though,
as ordinary `json_path` fields compared against the `Date` constants from
the table above:

```python
Person "birth.date.modifier == Date.MOD_ABOUT"
Person "birth.date.quality != Date.QUAL_NONE"
```

`dateval` is the raw `[day, month, year, is_bce]` tuple Gramps stores the
date as -- 4 elements normally, 8 for a `MOD_SPAN`/`MOD_RANGE` date
(`[day1, month1, year1, is_bce1, day2, month2, year2, is_bce2]`), so
`dateval[6]` reaches a span/range's *end* year, something `sortval` can't
give you at all:

```python
Person "birth.date.dateval[6] == 1970"
```

## Selecting values: the same paths in `select`

Everything above describes *which rows* come back. The same path grammar
also says *which values* -- a `select` entry is a column reference written
exactly as it would be on the left of a comparison:

```python
from gramps_object_query_language.query_lang import parse_select

parse_select(PERSON, [
    "handle",
    "birth.place.title as birthplace",
    "primary_name.surname_list[0].surname",
    "count(events) as n_events",
])
```

Each entry returns a `(column_ref, response_key)` pair: the ref goes into
`Query(select=[...])`, and the keys line up positionally with each result
row, so a caller can zip them into a dict. `birth.place.title` in a
`select` resolves to the identical `RelatedObject` that `birth.place.title
== 'Chicago'` resolves to in a `where_expr` -- one grammar, one meaning,
wherever a path is written.

Three details are specific to `select`:

- **`as <key>` renames the response key.** Without it the key is the path
  text itself (`"birth.place.title"`). An alias must be a plain name.
- **`count(...)` requires an alias.** Unlike a path, it has no text to
  derive a name from.
- **Paths are checked, in `select` and `where` alike.** See
  [Every path is checked](#every-path-is-checked) below.

`Query.select` also accepts a path string on its own, without going through
`parse_select`, if you don't need aliases or response keys:

```python
compile_query(PERSON, Query(select=["handle", "birth.place.title"]), dialect=Dialect.SQLITE)
```

Paths work in `order_by` too:

```python
Query(select=["handle", "birth.date.sortval"],
      order_by=[OrderBy("birth.date.sortval", "asc")])
```

Keyset pagination (`after`) works with it, but the cursor row's values can't
be read by interpolating a column name into SQL; use `compile_after_lookup`
to resolve them.

Sorting on a path is substantially more expensive than sorting on a flat
column. That difference is worth understanding before putting one behind a
UI control -- see below.

## What sorting costs

Reproduce these numbers with `python benchmarks/sort_cost.py`; the table
below is its output for 20,000 people, first page of 20:

| sort column | | ms | SQLite's query plan |
|---|---|---|---|
| `surname` | flat, indexed | ~0 | `SCAN person USING INDEX person_surname` |
| `given_name` | flat, indexed | ~0 | `SCAN person USING INDEX person_given_name` |
| `primary_name.first_name` | JSON path | 4 | `SCAN person` + `USE TEMP B-TREE FOR ORDER BY` |
| `birth.date.sortval` | relationship hop | 14 | `SCAN person` + `CORRELATED SCALAR SUBQUERY` + `USE TEMP B-TREE FOR ORDER BY` |

**An index is a pre-sorted copy of one column.** Gramps creates them for
`surname`, `given_name` and `gramps_id`. "First 20 by surname" walks the
first 20 entries of an already-sorted list and stops -- the other 19,980
rows are never read.

**Nothing indexes the inside of `json_data`.** It is one text blob per row.
Sorting by `primary_name.first_name` means opening all 20,000 blobs,
parsing each, extracting the field, sorting the lot in a temporary
structure, then keeping 20. `LIMIT` saves nothing: you cannot know which 20
sort first without looking at all of them. This is a property of the data
layout, not of this compiler -- no query formulation avoids it, and it is
why a path can be filtered and sorted but never sorted *cheaply*.

**A relationship hop adds a lookup per row.** `birth.date.sortval` isn't on
the person: for each row, find the birth event's handle, fetch that row from
`event`, parse its JSON, read `sortval`. That's `CORRELATED SCALAR
SUBQUERY`, run once per person, and it is why the hop costs roughly three
times the plain JSON path.

### Paging multiplies it

Each page is a fresh query, so the scan doesn't amortize across pages:

| sort column | per page | to walk all 20,000 |
|---|---|---|
| `surname` | 3.9 ms | ~4 s |
| `primary_name.first_name` | 6.1 ms | ~6 s |
| `birth.date.sortval` | 24.9 ms | ~25 s |

Walking the whole tree by `birth.date.sortval` re-scans all 20,000 rows and
re-runs 20,000 subqueries on *every one* of the 1,000 pages. The cost grows
with the square of the tree.

**Reasonable:** sorting a result set a `where` clause has already narrowed;
a report someone waits a moment for.

**Unreasonable:** a default sort order on a browse view of a large shared
tree, or anything that pages through everything.

**Caveats on the numbers.** They come from in-memory SQLite with short
synthetic strings, so a real disk-backed tree is slower in absolute terms.
The ratios are the point, not the milliseconds. PostgreSQL's plans differ in
detail (and a numeric path is additionally cast -- see above), but the same
three-way shape holds: indexed column, full scan, full scan plus a lookup
per row.

## Every path is checked

A path that doesn't exist is an error, not an empty result. Flat columns are
checked against the type's real SQL columns, as they always were; everything
inside `json_data` is checked against the Gramps class's own
`get_schema()` -- a complete, recursive JSON Schema that Gramps publishes
for every object type:

```
"primary_name.frist_name"   ->  unknown field 'frist_name' on Given name -- known fields: ...
"gendr"                     ->  unknown field 'gendr' on Person -- known fields: ...
"gender.value"              ->  'gender' is Gender, which has no fields
"primary_name[0]"           ->  'primary_name' is Name, not a list
```

`father.surname` written against a `Person` is caught the same way --
`father` is a relationship on `Family`, not on `Person`, and `Person` has no
such JSON field either.

This applies wherever a path is written: `select`, a `where` leaf's
`column`, and `where_expr`. The error names the fields that *would* have
worked, so a typo is a one-line fix rather than a debugging session over an
all-null column.

Three fields are serialized by Gramps but missing from its published schema
(`Date.format`, `Family.complete`, `Media.thumb`, as of Gramps 6.0.8). They
are real, queryable data, so the library patches them back in rather than
rejecting them; a test re-derives the list from live Gramps and fails if it
changes.

## What's *not* supported

- Arbitrary function calls, lambdas, f-strings, imports -- the parser
  whitelists node *shapes*, so anything it doesn't explicitly recognize is
  rejected, not silently ignored.
- Comprehensions, mostly -- `any(cond for x in rel)` and `len([... for x in
  rel])` are recognized (see [Comprehension sugar](#comprehension-sugar-any-and-len)
  above), but only in exactly that shape: every other comprehension form
  (a bare list/set/dict comprehension not wrapped in `any(...)`/`len(...)`,
  more than one `for` clause, a tuple-unpacking loop target, `all(...)`/
  `sum(...)`, ...) is rejected the same as any other unrecognized node.

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
