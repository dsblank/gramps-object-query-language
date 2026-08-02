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
shapes is understood. Anything else -- chained comparisons, list/dict
comprehensions, lambdas, arbitrary function calls, f-strings, imports --
is rejected outright rather than silently misinterpreted.

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

Chained comparisons like `1 < gender < 3` aren't supported (write `gender
> 1 and gender < 3` instead).

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

`children`/`notes` are **collection** names -- registered separately from the
relationship table above, and never usable as a dotted-path segment
(`children.surname` would be ambiguous: which child?), only as `exists`'s
first argument. Two are registered today:

| On a...  | ...`name` | reaches | via |
|----------|-----------|---------|-----|
| `Family` | `children` -> `Person` | each entry's own record | `child_ref_list` |
| `Person` | `notes` -> `Note` | each entry's own record | `note_list` |

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

## What's *not* supported

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
