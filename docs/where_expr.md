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

These two lines are the same query. (This doesn't extend to `len(...)`,
only ever recognized as the left-hand operand -- see
[Counting a collection](#counting-a-collection-len) and
[Array length](#array-length-lenpath).)

Text compares exactly with `==`, `!=` and `in [...]` (`surname == 'smith'`
doesn't find "Smith"), but ignoring case with `<`, `<=`, `>` and `>=`, so
that a comparison agrees with how text [sorts](#sorting-the-same-paths-in-order_by):
`surname < 'b'` is true for "Adams" and "adams" alike. Only the letters
`A`-`Z` are folded, and text is otherwise compared character by
character, the same on SQLite, PostgreSQL and under privacy filtering. A
`collation` passed in (a locale's) decides `<`/`>` instead. This holds
for every text field, on the record itself or through a relationship
(`father.surname < 'b'`). For a case-insensitive match, use
[`like(...)`](#in-contains-and-like).

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

To match a literal `%` or `_` with `like(...)`, put a backslash in front of
it: `like(description, '%100\\%%')` matches `"paid 100%"` but not
`"paid 1000"` (the backslash is doubled because it's inside a Python string
literal; `r'%100\%%'` works too). A literal backslash needs escaping the
same way (`r'C:\\%'` matches anything starting with `C:\`), and a pattern
can't end in a lone backslash.

Both `like(...)` and the substring form of `in` ignore case on every
backend -- `like(surname, 'smi%')` matches `"Smith"`. On SQLite, that only
applies to the ASCII letters `A`-`Z`: `like(surname, 'é%')` matches
`"Élise"` on PostgreSQL but not on SQLite.

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
Person "death.date.sortval is None"
Family "mother.gramps_id is not None"
Person "gender not in [Person.FEMALE, Person.OTHER]"
Person "'Jan' not in given_name"
```

`is`/`is not` are exactly `==`/`!=` -- this language has no notion of object
identity distinct from value equality, so `death.date.sortval is None` and
`death.date.sortval == None` compile to the identical wire node, and read the same as
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

### One entry of a collection: `parent_families[0].father`

A [collection](#one-to-many-relationships-any) such as a person's
`parent_families` is a list of links. Indexing it picks one entry, and the
path then goes on into the record that entry links to, exactly as it would
after a relationship name:

```python
Person "parent_families[0].father.surname == 'Smith'"
```

`parent_families[0]` is a person's first parent family, which Gramps treats
as their main parents, so this matches everyone whose father's surname is
Smith. It works with every collection stored on the record itself
(`families`, `parent_families`, `children`, `events`, `citations`,
`media`, `notes`, `tags`, `associations`, `enclosing_places`,
`repositories`), at any point in a path, and chains like any other hop:

| Path | Reads |
|------|-------|
| `parent_families[0].mother.primary_name` | a person's mother |
| `families[0].children[0].birth.date` | a person's first child's birth date (in their first family) |
| `father.parent_families[0].father.primary_name` | a family's paternal grandfather |
| `citations[0].source.title` | the source of a record's first citation |

As with a relationship, something has to follow the index
(`parent_families[0].gramps_id`, not `parent_families[0]` alone). An entry
that isn't there (a person with no parent family) gives no value, like a
missing birth event. `backlinks` and `child_refs` can't be indexed:
`backlinks` isn't stored on the record, and a `child_refs` entry is about
this person, not a record to go to.

To test *every* entry instead of one, use a comprehension:
`any(f.father.surname == 'Smith' for f in parent_families)`.

### One entry chosen by a condition: `[...][0]`

A list comprehension taken by index picks the first (or second, ...)
entry that matches its `if`, in the order the list stores them:

```python
Person "[a.value for a in attribute_list if a.type.value == AttributeType.OCCUPATION][0] == 'Farmer'"
```

reads "the person's first Occupation attribute is Farmer". What the
comprehension collects is what you get: `a.value` gives the attribute's
value; `a` gives the whole attribute, which a path after the index can then
read (`[a for a in attribute_list if ...][0].value` means the same).

Over a [collection](#one-to-many-relationships-any), the entry is a
record, and the path goes on into it as after `parent_families[0]`:

| Expression | Reads |
|------------|-------|
| `[f for f in parent_families if f.father.surname == 'Smith'][0].mother.primary_name` | the mother in a person's first parent family whose father is a Smith |
| `[f.father.primary_name for f in parent_families if len(f.children) > 2][0]` | the father of a person's first parent family with more than two children |
| `[n.type for n in alternate_names][1]` | the type of a person's second alternate name |

Without an `if` it's the same as plain indexing
(`[f for f in parent_families][1]` is `parent_families[1]`). When fewer
entries match than the index asks for, there's no value, as for a missing
birth event. The index is a plain number, 0 or more. `backlinks` and
`child_refs` can't be picked from, for the same reasons they can't be
indexed.

### Entries by type: `attribute_list['Age']`

Picking entries of a list by their type is common enough to have a
shorthand: put the type's name in the brackets instead of a number.
`attribute_list['Occupation']` is a person's Occupation attributes.

Read straight off, with a path after it or compared on its own, it is the
first one:

```python
Person "attribute_list['Occupation'].value == 'Farmer'"
Citation "source.attribute_list['Film'].value"
Person "urls['Web Home'].path != None"
```

Looped over, counted or tested with `any(...)`, it is all of them:

```python
Person "len(attribute_list['Occupation']) > 1"
Person "any(a.value == 'Miller' for a in attribute_list['Occupation'])"
Person "[a.value for a in attribute_list['Occupation']][1] == 'Miller'"
Person "any(attribute_list['Nickname'])"
Person "len(urls['E-mail'], path != '') > 1"
```

Each is turned into its long form before anything else happens:

| Shorthand | Long form |
|-----------|-----------|
| `attribute_list['Occupation'].value` | `[a for a in attribute_list if <a is an Occupation>][0].value` |
| `for a in attribute_list['Occupation'] if c` | `for a in attribute_list if <a is an Occupation> and c` |
| `len(attribute_list['Occupation'])` | `len([a for a in attribute_list if <a is an Occupation>])` |
| `any(attribute_list['Occupation'], c)` | `any(attribute_list, <is an Occupation> and c)` |

where `<a is an Occupation>` is `a.type.string == 'Occupation' or
a.type.value == AttributeType.OCCUPATION`. So the shorthand works
everywhere the long form does, in `where_expr`, `select` and `order_by`,
and reaches just as far: on the record itself, or on a record one link
away (`source.attribute_list`, `father.attribute_list`), inside `any(...)`
too.

The name is matched two ways because Gramps stores them two ways. A type
you made up yourself ("Film", "Hair color") is stored by its name. A
built-in type (Age, Nickname, Occupation, ...) is stored by number only,
with an empty name, so its English name is looked up and matched by
number. Names match exactly, so `'hair color'` doesn't find "Hair color".
When there's no entry of that type, there's no value, and
`attribute_list['Film'] == None` is true.

A name in brackets works on `attribute_list` (by `AttributeType`) and
`urls` (by `UrlType`:
`'E-mail'`, `'Web Home'`, `'Web Search'`, `'FTP'`, or your own). Other
lists are indexed by number, or by a comprehension.

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

## One-to-many relationships: `any(...)`

Every relationship in the table above is one-to-one -- a family has exactly
*one* father, a person has exactly *one* birth event. Some relationships are
naturally one-to-many instead -- a family has any number of children, a
person can have any number of notes -- and those are written the way
Python asks whether any item of a list matches, with `any(...)` around a
generator expression:

```python
Family "any(c.given_name == 'Steve' for c in children)"
Family "not any(c.given_name == 'Steve' for c in children)"
Family "any(c for c in children)"
```

`children`/`notes`/... are **collection** names -- registered separately
from the relationship table above. A whole collection is never a dotted-path
segment (`children.surname` would be ambiguous: which child?); it's what a
comprehension's `for c in ...` goes through, or, with an index, one entry
of it (`children[0].surname`, see
[One entry of a collection](#one-entry-of-a-collection-parent_families0father)).
Registered on every one of the ten record types, following Gramps' own
object model exactly (a `Source` has no `citations` since a source doesn't
cite other citations; a `Repository` has neither `citations` nor `media`;
`Tag` has none at all -- a tag doesn't tag itself):

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
changes, `any(a.given_name == 'Bob' for a in associations)` reads and
behaves exactly like any other collection.

The condition (`c.given_name == 'Steve'`) is an ordinary `where_expr`
about one related record, reached through the loop variable -- anything
legal as a top-level expression is legal here too (`and`/`or`/`not`,
chained relationships such as `c.birth.place.title`, even a nested
`any`). An `if` adds to it: `any(c for c in children if c.gender ==
Person.MALE)` and `any(c.gender == Person.MALE for c in children)` mean
the same. With neither (`any(c for c in children)`), it asks for at least
one related row at all.

Under the hood, `any(...)` compiles to a real `EXISTS (...)` subquery that
iterates the JSON array (`json_each` on SQLite, `jsonb_array_elements`/
`jsonb_array_elements_text` on PostgreSQL) joined against the target table by
handle -- not a correlated *scalar* subquery the way every relationship above
is, since there can be any number of matching rows, not just one.

One consequence worth knowing: unlike an ordinary comparison, `any(...)`
never produces SQL's `UNKNOWN` -- a family with no children at all simply
has zero matching rows in the subquery, the same as a family whose children
don't happen to match the condition, so `any(...)` there is a definite
`False` either way (never `None`/"missing"). That means `not any(...)`
is always plain negation, with none of the "a missing value under `not`
stays excluded, not included" three-valued-logic subtlety described above
for ordinary comparisons.

## Counting a collection: `len(...)`

`any(...)` only answers "at least one" -- `len(...)` asks "how many," over
the same registered collections: on its own for all of them, or around a
list comprehension with an `if` for the ones that match:

```python
Family "len(children) > 2"
Family "len([c for c in children if c.gender == Person.MALE]) >= 1"
```

Unlike `any(...)`, `len(...)` isn't itself a condition -- it produces a
*number*, so it's compared like a field (`len(children) > 2`, not a bare
`len(children)`), shown as a column, sorted by, or used in
[arithmetic](#arithmetic). The list comprehension collects the loop
variable (`[c for c in ...]`) or a plain literal (`[1 for c in ...]`); what
it counts comes from its `if`.

```python
Family "len(children) in [0, 1]"
```

A count can go on either side of a comparison, and be compared with
another count, a numeric field or a calculation -- "every child is a birth
child of both parents":

```python
Family "len([c for c in child_ref_list if c.frel.value == ChildRefType.BIRTH and c.mrel.value == ChildRefType.BIRTH]) == len(children)"
```

The same holds for a [picked entry](#one-entry-chosen-by-a-condition-0)
and for [arithmetic](#arithmetic). Comparing a count with a text or
True/False field is refused, since it can only be a mistake.

Under the hood, `len(...)` reuses `any(...)`'s own subquery shape
verbatim, just wrapped as `(SELECT COUNT(*) FROM ...)` instead of
`EXISTS (SELECT 1 FROM ...)` -- a missing collection (no children recorded
at all) is `0`, not `NULL`, the same way `COUNT(*)` over zero matching rows
always is in SQL.

### Counting a field, and different values: `distinct([...])`

The list can also collect a field of each entry. `len(...)` then counts
the entries where that field is filled in, and `distinct(...)` around the
list counts each different value once:

```python
Person "len(distinct([e.place for e in events])) > 1"
```

"People whose events happened in more than one place." A relationship on
its own (`e.place`, `f.father`) means the record it points to, so two
events at the same place count once; a field further along
(`e.place.title`) compares that text instead.

A list of fields leaves out the entries where the field is empty:
`[f.father for f in parent_families]` is "the fathers of these families,"
so a family with no father recorded isn't in it. That makes the two
counts comparable -- "more than one father," and "a father recorded in
more than one of this person's parent families" (often a duplicate family):

```python
Person "len(distinct([f.father for f in parent_families])) > 1"
Person "len([f.father for f in parent_families]) > len(distinct([f.father for f in parent_families]))"
```

As a [column](#selecting-values-the-same-paths-in-select),
`len(distinct([f.father for f in parent_families])) - 1 as other_fathers`
shows how many fathers a person has besides one (`-1` when no father is
recorded at all).

`distinct([...])` needs a field to compare (`[f.father for f in ...]`, not
`[f for f in ...]`), the field must hold one value (a date's `sortval`,
not the whole date), and so far it can only be counted -- it always sits
inside `len(...)`. In SQL it is `COUNT(DISTINCT ...)` in place of
`COUNT(*)`.

## Array length: `len(path)`

The collections above are lists of handles reaching into another table
(`Family.children`, `Person.notes`). `len(path)` measures a plain JSON
array already living inside the current row itself, no second table
involved -- an attribute list, a URL list, or (the motivating case) a
person's *other* recorded surnames:

```python
Person "len(primary_name.surname_list) > 1"
Person "len(note_list) > 0"
```

Gramps lets a person have several last names at once (a maiden name and a
married name, say). Before `len(...)` existed, the only way to ask "does
this person have more than one surname recorded" was checking whether a
particular index position was filled in
(`primary_name.surname_list[1].surname != None`) -- that only really answers
"at least two," and breaks down for "at least three" or more. `len(...)`
counts how many are actually there.

`path` can cross a relationship first, the same way any other field
reference can:

```python
Family "len(father.attribute_list) > 0"
```

A missing path and a non-array value at that path both count as `0`, not
`NULL` or an error -- "no list recorded" reads as "zero," not "unknown,"
matching the collection form's own "missing collection is `0`" behavior
above.

## Lists inside a record

A comprehension goes through a list stored inside the record just as it
goes through a collection -- the condition is then about one entry of
that list:

```python
Person "any(n.first_name == 'Doyle' for n in alternate_names)"
Person "len([n for n in alternate_names if n.first_name == 'Doyle']) > 1"
Family "any(a.value == 'Blacksmith' for a in father.attribute_list)"
```

`alternate_names` isn't a collection (a `Name` has no `gramps_id`, no
page of its own -- it only ever exists embedded inside the person record
it belongs to). Rule of thumb for telling the two apart: if the thing
you're filtering on would show up as its own record elsewhere in the tree
(a child, a note, a citation), it's a collection; if it's just one of
several alternative values recorded directly on this record (a name, an
address, an attribute), it's a list inside the record. The same spelling
covers both. A list inside a record can be reached across a relationship
first (`father.attribute_list`, above); a collection can't yet
(`any(n for n in father.notes)` is rejected).

Only a **list-of-structs** array -- one whose elements are themselves
records with their own fields (`Name`, `Attribute`, `Address`, `Surname`)
-- can take a condition; a plain list of values (`note_list`, `tag_list`)
has no sub-field to write one against, and is rejected with a clear error
if you try (`len(note_list) > 0` already covers "has any at all" for
those).

## Reverse references: `backlinks`

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
Note "not any(bl for bl in backlinks)"
Note "any(bl._class == 'Person' for bl in backlinks)"
Note "any(bl for bl in backlinks) and len(backlinks) > 1"
```

`_class` is the one field a `backlinks` condition can test -- the
referrer's own type (`"Person"`, `"Family"`, ...), matching the same field
name every record's own serialized JSON already uses for its type. Unlike
`children`/`notes`/every other collection above, `backlinks` has no single
target type to reach further into -- a backlink's referrer can be any of
the ten record types at once -- so `_class` can't be followed any deeper
(`bl._class.primary_name` isn't supported): `==`/`!=`/`is`/`is not`/`in`
against a class-name string (or a list of them, for `in`) is the whole
vocabulary:

```python
Note "any(bl._class in ['Person', 'Family'] for bl in backlinks)"
Note "any(bl._class != 'Media' for bl in backlinks)"
```

`len(backlinks)` works exactly like `len(...)` above -- `len(backlinks)
== 0` is another way to spell `not any(bl for bl in backlinks)`.

### One kind of referrer: `backlinks['Person']`

Name a kind of record in brackets and `backlinks` becomes those records
themselves -- every Person (or Family, Event, ...) pointing at this one --
so a condition can read their own fields:

```python
Note "any(p.primary_name.first_name == 'Ann' for p in backlinks['Person'])"
Note "len(backlinks['Family']) > 1"
Event "any(backlinks['Person'], gender == Person.FEMALE)"
Note "backlinks['Person'].birth.date.sortval < Date('1900-01-01')"
```

It is a collection like `children` or `events`: looped over, counted,
tested with `any(...)`, `exists(...)` or `count(...)`, and counted by a
field with `distinct([...])`. Read straight off with a path after it, it
is the first of them, and `backlinks['Person'][1]` the second. "First" is
by the records' handles, byte by byte, the same in SQL and in Python -- a
fixed order, not a meaningful one, so it reads as "one of them".

A record pointing here twice is one record: `len(backlinks['Person'])`
counts people, not references. The kind is one of Citation, Event,
Family, Media, Note, Person, Place, Repository, Source or Tag, spelled as
here. Through a privacy proxy, a record you can't see isn't among them.

For an event's people and families *with their roles*, see
[`event_refs`](#who-an-event-belongs-to-eventevent_refs) below: those are
the links themselves, which `backlinks` doesn't have.

## A self-linked collection: `Person.child_refs`

Every collection so far reads like `any(c.given_name == 'Steve' for c in
children)` -- a condition tested against the *joined row's own fields*
(`children`'s condition sees a `Person`, `notes`' sees a `Note`).
`child_refs` is the one exception: it's still a registered collection,
reached the same way, but its condition is about a field on the *link*
between the two records, not either record itself:

```python
Person "any(ref for ref in child_refs if ref.frel.value == ChildRefType.ADOPTED or ref.mrel.value == ChildRefType.ADOPTED)"
```

This is the query behind "was this person adopted" -- Gramps records that
on the `ChildRef` entry a `Family` keeps for each of its children (`frel`/
`mrel`, the father's/mother's side of the relationship), not on the
`Person`/`Family` records themselves. `child_refs` walks each of a person's
own parent families (`parent_family_list`, the same list `parent_families`
above reaches) and, for each one, finds *the one entry in that family's own
`child_ref_list` that names this person* -- `frel`/`mrel` (along with every
other `ChildRef` field: `note_list`, `citation_list`, `private`) are that
entry's own fields, not `Family`'s. Writing `ref.frel`/`ref.mrel` ties
those two fields to the loop variable, the `ChildRef` link itself.

`child_refs` works with `any(...)` and `len([...])` like any other
collection; it can't be indexed or picked from (`child_refs[0]`), since its
entries are about this person, not records to go to.

`ref.referrer` is the family the entry is stored in, read like any other
link:

```python
Person "any(ref.referrer.father.surname == 'Smith' for ref in child_refs if ref.frel.value == ChildRefType.ADOPTED)"
Person "len(distinct([ref.referrer.father for ref in child_refs])) > 1"
```

## Who an event belongs to: `Event.event_refs`

An event doesn't list the people it belongs to: each person (or family)
lists the event in its own `event_ref_list`, each with a role -- Primary,
Witness, Family, ... `event_refs` turns that around: on an Event, it is
every one of those links pointing at this event, from people and families
alike. Each entry is the link itself (an EventRef: `role`, `private`,
`attribute_list`, ...), and `referrer` is the person or family it is
stored in:

```python
Event "any(event_refs['Primary'])"
Event "len(event_refs['Witness']) > 2"
Event "any(r.referrer.primary_name.surname == 'Smith' for r in event_refs['Primary'])"
Event "event_refs['Primary'].referrer.gender == Person.FEMALE"
Event "(date.sortval - event_refs['Primary'].referrer.birth.date.sortval) // 365 > 90"
```

A role in brackets picks the entries with that role, the way a type does on
`attribute_list` (see [Entries by type](#entries-by-type-attribute_listage)).
So `event_refs['Primary'].referrer.primary_name` is the event's first
Primary person's name -- in `select` it is the "Main Participants" column
-- and the last example finds deaths at over 90.

`referrer` can be a Person or a Family. A path after it is checked against
both and needs to exist on at least one of them; where the referrer is the
other kind, there's no value, as for a missing birth event. So
`referrer.father.surname` is the family's father's surname, and nothing
for a person's link; `referrer.gramps_id` is either one's ID.

The first entry (`[0]`, or a path read straight off `event_refs['Primary']`)
is the first in a fixed order: people before families, then by the
record's handle, then by position in its list. With several Primary people
that is just "one of them" rather than the most important one, but the
same one every time, in SQL and in Python alike.

`event_refs` finds the records through Gramps' own `reference` table, as
`backlinks` does, so it costs an index lookup per event rather than a scan
of every person. Through a privacy proxy, a link from a record you can't
see isn't there.

## Comprehension rules

The comprehensions on this page are a few fixed shapes of Python's, not
Python itself:

- `any(<condition> for x in <list>)` -- the condition is about `x`; a bare
  `x` (`any(x for x in rel)`) has none. An `if` on the generator adds to
  it (`any(x for x in rel if x.a == 1)` is `any(x.a == 1 for x in rel)`).
- `len([x for x in <list> if <condition>])` -- counts by the `if`; what it
  collects is `x` itself, a plain literal (`[1 for x in ...]`), or a field
  of `x` (`[x.f for x in ...]`: only the entries where it's filled in).
- `len(distinct([x.f for x in <list> if <condition>]))` -- how many
  different values of the field, see
  [Counting a field](#counting-a-field-and-different-values-distinct).
- `[x.<path> for x in <list> if <condition>][n]` -- one matching entry,
  see [One entry chosen by a condition](#one-entry-chosen-by-a-condition-0).
- Only one `for` clause, except in `any(...)`: there each `for` after the
  first goes over a list of the loop variable just before it,
  `any(c.gender == Person.FEMALE for f in parent_families for c in
  f.children)`, the same as nesting one `any(...)` per `for`. An `if`
  belongs to the `for` before it and can only test that loop variable.
- The loop variable is a plain name (no tuple-unpacking).
- `<list>` is a collection name, a list inside the record (optionally
  across a relationship, `father.attribute_list`), or -- inside another
  comprehension -- exactly one attribute off the enclosing loop variable:
  `any(any(e.type.value == EventType.BIRTH for e in c.events) for c in
  children)`, not a longer chain.
- `all(...)`/`sum(...)` aren't recognized -- there's no established
  motivating query for them yet, and `all(...)` in particular would need a
  double negation (`not any(not ... for ...)`) under the hood.

Older spellings -- `exists(rel, cond)`, `count(rel, cond)`, `any(rel,
cond)` and `len(rel, cond)` -- still parse, for saved filters that use
them, and mean the same as the comprehensions above. They aren't
documented further and shouldn't be used in new queries.

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

**`any(... for c in children)`** -- a family with at least one child
matching a condition, and its negation:

```python
Family "any(c.given_name == 'Steve' for c in children)"
Family "not any(c.given_name == 'Steve' for c in children)"
```

**`any(c for c in children)`** -- a family with any recorded child at all,
no condition:

```python
Family "any(c for c in children)"
```

**`any(n for n in notes)`** -- starting from `Person` instead of `Family`,
and over a flat handle list (`note_list`) rather than a list of ref objects
(`child_ref_list`) -- the two collection shapes registered today, both
spelled the same way from `where_expr`:

```python
Person "not any(n for n in notes)"
```

**`len(...)`** -- how many children there are, or how many match a
condition:

```python
Family "len(children) > 0"
Family "len([c for c in children if c.given_name == 'Robert']) == 1"
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
Person "any(c.confidence >= Citation.CONF_HIGH for c in citations)"
```

**`associations` (self-referencing)** -- a person linked to another person
by name, via an association:

```python
Person "any(a.given_name == 'Bob' for a in associations)"
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
is a custom one," not which. For those, and as a shorter way to write the
built-in ones, compare the type itself with its name:

### Type names

A Gramps type object (anything stored as `{"_class": "EventType", "value":
..., "string": ...}`) can be compared with `==`, `!=`, `in` and `not in`
against names (or plain numbers), and the comparison is rewritten into the
fields it is stored as before the query runs:

```python
Event "type == 'Census'"                  # type.value == EventType.CENSUS
Event "type in ['Birth', 'Baptism']"      # type.value in [12, 15]
Event "type == 'Tax list'"                # type.value == EventType.CUSTOM and type.string == 'Tax list'
Family "type == 'Handfasting'"            # type.value == FamilyRelType.CUSTOM (4) and type.string == ...
Family "any(c.frel == 'Adopted' for c in child_ref_list)"
Person "birth.type == 'Birth'"
Person "any(r.role == 'Primary' for r in event_ref_list)"
```

A name is looked up on the type's own class, in this order:

1. Gramps' English name for a built-in type (the one Gramps writes to its
   XML files, the same in every language), ignoring case;
2. the built-in type's name in the language Gramps is running in, ignoring
   case;
3. otherwise it is a custom type's name: `value` is that class's `CUSTOM`
   number and `string` is exactly the name. The `CUSTOM` number is read off
   the class, since it isn't the same for all of them (`FamilyRelType` uses
   4, `ChildRefType` 7, most others 0). `StyledTextTagType` has no custom
   types, so an unknown name there is an error.

A custom type can't share a built-in type's name: Gramps itself turns
"Census" typed into a type into the built-in Census. `type is None` and
the fields themselves (`type.value`, `type.string`) are left as written.
The same rewrite applies to a `where` JSON body passed to
`where_list_to_ast`, so `{"column": "type", "op": "eq", "value": "Census"}`
works there too.

## Values passed in: `Env`

`Env.NAME` is a value supplied by whoever runs the expression, rather than
written into it -- the person a user calls "home", the records they've
bookmarked, a cutoff date. Pass them as `env` to `parse_expr`,
`parse_expr_for_spec`, `compile_expr` or `compile_expr_for_spec`:

```python
compile_expr("Person", "handle == Env.HOME_PERSON", env={"HOME_PERSON": "GNUJQCL9MD64AM56OH"})
compile_expr("Family", "father.handle == Env.HOME or mother.handle == Env.HOME", env={"HOME": "GNUJQCL9MD64AM56OH"})
compile_expr("Event", "handle in Env.BOOKMARKS", env={"BOOKMARKS": ["a1b2...", "c3d4..."]})
compile_expr("Note", "Env.WORD in text.string", env={"WORD": "census"})
```

```python
Person "handle == Env.HOME_PERSON"
Person "handle in Env.BOOKMARKS"
Person "handle not in Env.BOOKMARKS"
Family "father.handle == Env.HOME or mother.handle == Env.HOME"
```

An `Env` value can go anywhere a literal can: either side of a comparison,
in a substring test, as a `like`/`regex` pattern, inside `any(...)`. A
value is a string, number, boolean or `None`; a list (or tuple) of those
is for `in`. It's a value, never parsed as part of the expression, so a
string holding quotes or GOQL needs no escaping, and on the SQL side it's
a bound parameter like any other literal.

An empty list is allowed, unlike a literal `[]`: `handle in Env.BOOKMARKS`
with no bookmarks matches nothing, and `handle not in Env.BOOKMARKS`
matches everything (even a record whose field is missing -- an empty list
is plain false, not unknown).

`parse_select`/`parse_order_by` (and their `_entry` forms) take the same
`env`, for an `Env` inside a comprehension's condition, e.g. a select
entry `"len([e for e in events if e.description == Env.D]) as n"`.

A value of the wrong type for the field it's compared with
(`len(event_ref_list) > Env.N` with `N` = `"a"`, `gramps_id == 3`) is
rejected when the expression is compiled, as is a `regex(...)` pattern
Python's `re` can't compile -- not left to fail, or quietly match nothing,
when the query runs. The same goes for literals written in the expression.

`Env` is capitalized like the constant classes above, so it can't be
mistaken for a field: no Gramps field starts with a capital letter. A
name that isn't in `env` is an error naming the ones that are; names are
case-sensitive. Using a list where a single value belongs
(`handle == Env.BOOKMARKS`), or a single value with `in`
(`handle in Env.HOME`), is an error that says which one it is.

### What to pass in

GOQL defines no `Env` names of its own: each app chooses which ones it
supplies, and an expression only has to use names the `env` passed with it
has. Some that make a saved expression more useful, because they're true
when it runs rather than when it was written:

- **The user's own reference points** -- a home person, the database's
  default person, a home place, a tag the team uses for "to do":

  ```python
  Family "father.handle == Env.HOME_PERSON or mother.handle == Env.HOME_PERSON"
  Event "place.handle == Env.HOME_PLACE"
  Person "any(t.name == Env.TODO_TAG for t in tags)"
  ```

- **What the user is looking at** -- the active record, the current
  selection, bookmarks, recently visited records. The same saved expression
  then works on whatever is selected:

  ```python
  Family "any(c.handle == Env.ACTIVE for c in children)"
  Person "handle in Env.SELECTION and len(citations) == 0"
  Note "handle not in Env.VISITED"
  ```

- **Points in time GOQL can't compute** -- there's no date arithmetic, so a
  relative cutoff is passed in ready-made: when the user last looked (in
  seconds, the same scale as `change`), or a date sort value such as
  "110 years ago" for a rough "possibly living" check:

  ```python
  Person "change >= Env.LAST_VISIT"
  Person "birth.date.sortval > Env.LIVING_CUTOFF and death.date.sortval == 0"
  ```

  For today itself, `Date('today')` already works -- see [Dates](#dates).

- **Sets GOQL can't work out** -- there's no recursion, so ancestors,
  descendants or relatives within a few steps can be computed by the
  caller and passed as a handle list:

  ```python
  Person "handle in Env.ANCESTORS and len(citations) == 0"
  ```

  A large tree can make that list long; computing it next to the database
  is cheaper than in a client.

Values like a home person, the selection or the last visit differ per
user, so one saved expression can find different records for different
people. Apps that want to share saved expressions should agree on names
for the values they have in common.

## Dates

`Date('...')` parses a human-readable date string with Gramps' own date
parser and resolves to `.sortval` -- a plain comparable integer (a Julian
day number) -- so it can be compared with the ordinary numeric operators:

```python
Person "birth.date.sortval >= Date('Jan 1, 1968')"
```

`Date('today')` is today's date, worked out each time the expression is
compiled, so a saved expression that uses it stays relative to the day it
runs. The parser only knows today itself: offsets such as "1 year ago"
don't parse, and neither does a time ("now") -- pass a ready-made value in
[`Env`](#values-passed-in-env) for those.

```python
Person "birth.date.sortval > Date('today')"
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

## Arithmetic

Numbers can be combined with `+`, `-`, `*`, `/` and `//`, with Python's
precedence and parentheses, anywhere a value is used -- a comparison (on
either side), `select` and `order_by`:

```python
Person "death.date.dateval[2] - birth.date.dateval[2] > 90"
Person "(death.date.sortval - birth.date.sortval) // 365 > 90"
Family "mother.birth.date.dateval[2] > father.birth.date.dateval[2] + 10"
```

The first reads "died more than 90 years after the year they were born";
the second counts whole years from the exact dates (`sortval` is a day
count), so someone born in March 1850 who died in February 1930 is 79
there, 80 by year. The third finds families where the mother is more than
ten years younger than the father.

- **Numbers only.** Each side is a number, a numeric field, a count
  (`len(...)`) or a [picked entry](#one-entry-chosen-by-a-condition-0). A
  date isn't a number: use its `.sortval` (days) or `.dateval[2]` (year).
  At least one side has to be a field; `2 + 3` is rejected.
- **`/` and `//`.** `/` divides exactly (`7 / 2` is `3.5`); `//` rounds
  down, as in Python (`-7 // 2` is `-4`, not SQL's `-3`).
- **Missing values.** A side with no value (no death event) gives no
  value, and so does dividing by zero, rather than an error. A comparison
  against no value is false, as for any missing field.
- **A date that is there but empty** (an event with no date, or a
  text-only one) is stored with `sortval` 0 and zeros in `dateval`. In
  arithmetic, any number read from such a date -- its `sortval` or a
  `dateval` item -- counts as missing, so a person whose birth event has no
  date gets no lifespan rather than one of nearly 2,000 years. Only
  arithmetic does this: `birth.date.sortval == 0` still finds undated
  births.
- `%` and `**` aren't supported.

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
    "len(events) as n_events",
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
- **`len(...)` requires an alias.** Unlike a path, it has no text to derive
  a name from. (Neither does a comprehension taken by index; without an
  alias its key is the entry's own text.) `len(...)` takes the same
  comprehension a filter does:
  `len([c for c in child_ref_list if c.frel.value == ChildRefType.BIRTH and c.mrel.value == ChildRefType.BIRTH]) as birth_children`.
- **Paths are checked, in `select` and `where` alike.** See
  [Every path is checked](#every-path-is-checked) below.

`Query.select` also accepts a path string on its own, without going through
`parse_select`, if you don't need aliases or response keys:

```python
compile_query(PERSON, Query(select=["handle", "birth.place.title"]), dialect=Dialect.SQLITE)
```

## Sorting: the same paths in `order_by`

An `order_by` entry is a column reference written the same way, sorted
ascending, or descending with a leading `-`. `parse_order_by` turns a list of
them, most significant first, into `OrderBy`s for `Query`:

```python
from gramps_object_query_language.query_lang import parse_order_by

parse_order_by(PERSON, ["surname", "given_name", "-birth.date.sortval"])
parse_order_by(FAMILY, ["-len(child_ref_list)"])
```

Anything a `select` entry can name works here: a flat column, a path, a
relationship path, `len(...)`, a picked entry or arithmetic. `as` has no meaning in a sort,
so it's an error, as is a comparison (`birth.date.sortval > 0`). Sorting by a
path costs what selecting it does; see [What sorting costs](#what-sorting-costs)
below.

Built by hand, the same sort is:

```python
Query(select=["handle", "birth.date.sortval"],
      order_by=[OrderBy("birth.date.sortval", "desc")])
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
- Comprehensions, mostly -- `any(cond for x in rel)`, `len([... for x in
  rel])` and `[x.f for x in rel if ...][n]` are recognized (see
  [Comprehension rules](#comprehension-rules) and
  [One entry chosen by a condition](#one-entry-chosen-by-a-condition-0)
  above), but only in exactly those shapes: every other comprehension form
  (a bare list/set/dict comprehension not wrapped in `any(...)`/`len(...)`/
  `distinct(...)` or indexed,
  more than one `for` clause, a tuple-unpacking loop target, `all(...)`/
  `sum(...)`, ...) is rejected the same as any other unrecognized node.
- A collection reached through a relationship hop -- `any(n for n in
  father.notes)` is rejected with a clear error, even though `father.notes` looks
  just like the relationship-hop *paths* this page covers elsewhere
  (`father.attribute_list`, a plain array, works fine). `notes`/`children`/
  etc. are only ever reachable directly on the type being queried today,
  never one hop away -- not a design decision so much as nobody has needed
  it yet. One *entry* of one is reachable across a hop
  (`father.parent_families[0].father.surname`, see
  [One entry of a collection](#one-entry-of-a-collection-parent_families0father)).

## Using it from Python

```python
from gramps_object_query_language.query_lang import compile_expr
from gramps_object_query_language.query import Query, compile_query, Dialect

spec, where = compile_expr("Family", "mother.death.date.sortval < father.death.date.sortval")
sql, params = compile_query(spec, Query(select=["handle"], where=where), dialect=Dialect.SQLITE)
rows = connection.execute(sql, params).fetchall()
```

`compile_expr(namespace, expr, env=None)` parses and translates a `where_expr` string
in one step, returning the matching `ObjectTypeSpec` alongside a `where` AST
ready for `compile_query`/`compile_count_query`. If you only need the
intermediate JSON shape (e.g. to send over an HTTP API as a `where_expr`
request field), use `parse_expr`/`parse_expr_for_spec` from the same module
instead.

Selecting more than `handle`? The database returns some values in its own
form, not their Gramps type: a boolean is `0`/`1` on SQLite, every JSON
number or boolean is text on PostgreSQL (`'2389036'`, `'true'`), and an
object or list from SQLite is JSON text. `select_normalizer` turns each row
into the values the evaluator path gives -- `True`/`False`, numbers, parsed
objects -- on any backend:

```python
from gramps_object_query_language.query import select_normalizer

select = ["handle", "private", "birth.date.sortval", "birth.date"]
sql, params = compile_query(spec, Query(select=select, where=where), dialect=Dialect.POSTGRESQL)
normalize = select_normalizer(spec, select)
rows = [normalize(row) for row in connection.execute(sql, params).fetchall()]
# e.g. ("a1b2...", False, 2433283, {"dateval": [1, 1, 1950, False], ...})
```

For a database you can't run raw SQL against (a proxied/privacy-filtered
database), `gramps_object_query_language.evaluator.evaluate_where` evaluates
the same AST directly against real Gramps objects instead of compiling to
SQL -- see the [README](../README.md) for the module overview.
