# Finding what you want: a plain-language guide

This page is for anyone who wants to search their family tree data for
something specific -- no programming experience needed. If you *are* a
programmer, see [`docs/where_expr.md`](docs/where_expr.md) instead for the
technical reference.

Every example on this page has been checked by an automated test, so if you
copy one exactly, it works.

## The basic idea

A search always has two parts:

1. **What kind of record are you looking for?** -- a `Person`, a `Family`,
   an `Event`, a `Place`, ...
2. **What has to be true about it?** -- written as a short sentence in
   quotes.

```
Person "surname == 'Smith'"
```

This reads as: *look at Person records, where the surname equals 'Smith'*.

A few symbols you'll see over and over:

| Symbol | Means |
|--------|-------|
| `==`   | is equal to |
| `!=`   | is not equal to |
| `<`, `<=` | is less than / is less than or equal to (earlier, smaller) |
| `>`, `>=` | is greater than / is greater than or equal to (later, bigger) |
| `and`  | both things must be true |
| `or`  | at least one of the two things must be true |
| `not`  | flips true/false -- matches when the thing *isn't* true |
| `in [ ... ]` | matches any one of a list of values |
| `'text' in field` | matches if `field` contains `'text'` anywhere in it |
| `like(field, 'pattern')` | matches a text pattern, where `%` stands for "anything" |

Text values go in single quotes (`'Smith'`); numbers don't (`1968`).

## Cookbook

### Goal: Find everyone whose last name is Smith

```
Person "surname == 'Smith'"
```

### Goal: Find all the men in the tree

```
Person "gender == Person.MALE"
```

`Person.MALE` means "male". (There's also `Person.FEMALE`, `Person.UNKNOWN`,
and `Person.OTHER`.)

### Goal: Find all the men whose last name is Smith

Join two conditions with `and`:

```
Person "gender == Person.MALE and surname == 'Smith'"
```

### Goal: Find everyone named John or Jane

```
Person "given_name in ['John', 'Jane']"
```

`in [...]` matches any name in the list -- add as many as you like, separated
by commas.

### Goal: Find everyone named John, or anyone with the last name Doyle

`in [...]` only works when it's the *same* field each time (like `given_name`
above). To match on two *different* fields instead, use `or`:

```
Person "given_name == 'John' or surname == 'Doyle'"
```

This finds anyone who satisfies *either* condition, not just people who
satisfy both -- unlike `and`, which needs both sides to be true.

### Goal: Find every man named Smith, or anyone at all named Mary

`and` and `or` can be combined -- `and` is checked before `or`, the same as
in ordinary arithmetic where multiplication happens before addition, so use
parentheses to group things exactly how you mean:

```
Person "(gender == Person.MALE and surname == 'Smith') or given_name == 'Mary'"
```

Without the parentheses, `gender == Person.MALE and surname == 'Smith' or
given_name == 'Mary'` still reads the *same* way -- `and` grouping happens
first regardless -- but writing the parentheses out makes the intent clear
to a future reader (including yourself).

### Goal: Find everyone whose last name *isn't* Smith

```
Person "not (surname == 'Smith')"
```

`not` flips a condition -- it matches whenever the thing inside it *isn't*
true. It works on a single condition, or a whole parenthesized group:

```
Person "not (gender == Person.MALE and surname == 'Smith')"
```

That finds everyone who *isn't* a male Smith -- women, and Smiths of any
other gender, and everyone whose last name isn't Smith at all.

### Goal: Find everyone whose first name starts with "J"

```
Person "like(given_name, 'J%')"
```

The `%` means "anything can follow" -- so this matches John, Jane, James,
Julia, and so on. (Use `like(field, '%son')` to match names *ending* in
"son" instead.)

### Goal: Find everyone whose name contains "an" anywhere in it

```
Person "'an' in given_name"
```

Unlike `like(...)`, you don't need to add any `%` signs -- `'an' in
given_name` already means "anywhere in the name," and matches Jane,
Alexander, Susan, and so on. If the text you're searching for happens to
contain a `%` or `_` itself (say, a note that literally says "50% off"),
it's still matched as plain text, not treated as a special pattern.

### Goal: Find everyone born on or after January 1, 1968

```
Person "birth.date.sortval >= Date('Jan 1, 1968')"
```

`Date('...')` understands ordinary date text. `birth.date.sortval` means
"the date of this person's birth event" -- `birth` reaches over to their
birth event, and `.date` is that event's date.

`sortval` is always a single point in time -- the year/month/day recorded on
the date, turned into one comparable number. It does *not* carry any
"about," "before," "after," or "estimated" qualifier along with it -- those
are recorded separately, in a field of their own called `modifier` (and
`quality`, for "estimated"/"calculated"). For example, a birth date entered
as "before 1968" has the *exact same* `sortval` as one entered as plain
"Jan 1, 1968", so `birth.date.sortval >= Date('Jan 1, 1968')` would count
that "before 1968" person as born on or after the cutoff, even though
"before" means the opposite. A date span or range (like "1968 to 1970")
behaves the same way -- its `sortval` is just the start of the range, not
the whole thing.

If that distinction matters, check `modifier` directly instead of, or
alongside, `sortval`:

```
Person "birth.date.modifier == Date.MOD_ABOUT"
```

`Date.MOD_ABOUT` is a named constant read straight from Gramps itself, the
same way `Person.MALE` is elsewhere in this guide -- see
[`docs/where_expr.md`](docs/where_expr.md#constants) for the full list of
modifiers (`MOD_BEFORE`, `MOD_AFTER`, `MOD_RANGE`, `MOD_SPAN`, ...) and the
other named constants available for event types, name types, and more.

### Goal: Find everyone who has died (not people still living)

```
Person "death.date.sortval < Date('Jan 1, 2100')"
```

This looks like an odd way to ask it, but it works because someone who is
still living has no recorded death date at all -- so they're automatically
left out, without needing a special "is alive" check.

### Goal: Find people whose death record mentions an accident

```
Person "like(death.description, '%accident%')"
Person "'accident' in death.description"
```

`death` reaches the whole death record, not just its date -- any detail
recorded there, like a description of what happened, can be searched too.
Both lines above find the same people; the second is just the plainer way
to write "contains" without needing to add the `%` signs yourself.

### Goal: Find everyone born in Chicago

```
Person "birth.place.title == 'Chicago, Cook, Illinois, USA'"
```

`birth.place.title` reaches from the person, to their birth event, to that
event's place, to the place's full name.

### Goal: Find everyone who was born and died in the same place

```
Person "birth.place.title == death.place.title"
```

Compares the place reached through `birth` against the place reached
through `death`, directly -- no need to name the place at all.

### Goal: Find all events that took place in Chicago

```
Event "place.title == 'Chicago, Cook, Illinois, USA'"
```

This one searches `Event` records directly, rather than reaching an event
through a person -- useful when you want every event recorded at a place,
regardless of whose it is.

### Goal: Find all families where the father's last name is Smith

```
Family "father.surname == 'Smith'"
```

`father` reaches from a family over to the father's own Person record --
after that, `.surname` is just their last name.

### Goal: Find all families where the mother's first name is Mary

```
Family "mother.given_name == 'Mary'"
```

`mother` works exactly like `father`, just reaching to the mother's own
Person record instead.

### Goal: Find all families where the father was born before 1850

```
Family "father.birth.date.sortval < Date('Jan 1, 1850')"
```

This reaches two steps from the family: to the father, then to *his* birth
event, then to its date -- useful for finding older generations without
knowing exactly who they are ahead of time.

### Goal: Find all families where the mother and father share the same last name

```
Family "father.surname == mother.surname"
```

Both sides of `==` can be a field to reach for, not just a fixed value --
here it's comparing the father's last name against the mother's, rather
than against a specific name.

### Goal: Find all the families where the mom died before the dad

```
Family "mother.death.date.sortval < father.death.date.sortval"
```

This reaches from the family to the mother, to *her* death event, to its
date -- and does the same for the father -- then compares the two dates
directly.

### Goal: Find all families where the mother and father died in the same place

```
Family "father.death.place.title == mother.death.place.title"
```

The same idea as the previous example, but comparing *where* each parent
died instead of *when*.

### Goal: Find citation sources you consider highly reliable

```
Citation "confidence >= Citation.CONF_HIGH"
```

`Citation.CONF_HIGH` is one of the confidence levels Gramps itself uses
(from lowest to highest: `CONF_VERY_LOW`, `CONF_LOW`, `CONF_NORMAL`,
`CONF_HIGH`, `CONF_VERY_HIGH`).

### Goal: Find people who have more than one last name recorded

```
Person "primary_name.surname_list[1].surname != None"
```

Gramps lets a person have several last names at once (a maiden name and a
married name, say) -- `surname_list[0]` is always the first one, and this
checks whether a *second* one (`[1]`) exists at all. There's no direct way
to ask "how many last names does this person have," but checking whether a
particular position in the list is filled in works just as well for "two or
more."

### Goal: Find families that have a child named Steve

```
Family "exists(children, given_name == 'Steve')"
```

`exists(children, ...)` matches a family if *any* of its children satisfies
the condition -- unlike `father`/`mother`, which each always reach exactly
one person, a family can have any number of children, so this needs its own
"does at least one of them match" check rather than an ordinary field
reference.

### Goal: Find families with no children recorded at all

```
Family "not exists(children)"
```

Leaving out the condition (`exists(children)` alone) just asks "does this
family have any recorded child at all" -- `not` in front flips that to "no
children recorded."

### Goal: Find people who don't have any notes attached to their record

```
Person "not exists(notes)"
```

The same idea, starting from `Person` instead: `notes` reaches every note
attached to a person's record, and `not exists(notes)` matches whenever
there aren't any.

### Goal: Find families with more than two children

```
Family "count(children) > 2"
```

`count(children)` counts how many children a family has recorded --
`exists(children, ...)` can only tell you whether *at least one* child
matches something, `count(...)` tells you *how many*.

### Goal: Find families with more than one son

```
Family "count(children, gender == Person.MALE) > 1"
```

Adding a condition (the same kind of condition `exists(...)` takes) counts
only the children who match it -- here, only the sons.

### Goal: Find people with a well-sourced record

```
Person "exists(citations, confidence >= Citation.CONF_HIGH)"
```

`exists(children, ...)`/`count(children, ...)` aren't the only collections
recorded per-person -- `notes`, `citations`, `media`, and `tags` are
available the same way on almost every record type, plus a few more
specific to each type: a person's `families` (as a spouse), `parent_families`
(as a child), `associations` (links to other people), and `events` (every
recorded event, not just birth/death).

### Goal: Find people linked to someone named Bob via an association

```
Person "exists(associations, given_name == 'Bob')"
```

### Goal: Find citations for a specific source

```
Citation "source.title == 'Census Records'"
```

`source` reaches from a citation to the source it cites -- works just like
`father`/`mother` reaching from a family to a parent.

### Goal: Find people with no death record at all

```
Person "death.date.sortval is None"
```

`is None` reads more naturally than `== None` for "this isn't recorded at
all" -- they mean exactly the same thing, so use whichever reads better in
context.

### Goal: Find everyone except the Smiths and the Joneses

```
Person "surname not in ['Smith', 'Jones']"
```

`not in` is the same list-membership check as `in`, just flipped -- this
matches anyone whose last name is neither of the two listed.

### Goal: Find everyone born before 1900

```
Person "Date('Jan 1, 1900') > birth.date.sortval"
```

The date doesn't have to go on the right -- this reads left-to-right as
"1900 is after this person's birthdate," and matches exactly the same
people as writing it the more usual way,
`birth.date.sortval < Date('Jan 1, 1900')`.

## Things this can't do (yet)

- Anything beyond the patterns shown above -- this is a small, fixed set of
  building blocks, not a full programming language, so anything outside it
  is rejected with an error rather than guessed at.

## Where to go from here

- [`docs/where_expr.md`](docs/where_expr.md) has the full technical
  reference, including every field and relationship name available on each
  record type.
- [`gramps_object_query_language/tests/test_where_expr_examples.py`](gramps_object_query_language/tests/test_where_expr_examples.py)
  is the test file that proves every example above actually works.
