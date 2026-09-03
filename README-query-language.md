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
| `regex(field, 'pattern')` | matches a regular expression (for those who already know them) |

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

### Goal: Find everyone named John or Jane, using a regular expression

```
Person "regex(given_name, 'John|Jane')"
```

`regex(field, 'pattern')` is for people already comfortable with regular
expressions -- a more powerful (and more technical) kind of pattern than
`like(...)`'s `%`/`_`. This example matches the same people as
`given_name in ['John', 'Jane']`, just spelled a different way. If you
don't already know regular expressions, `in [...]`/`like(...)`/`'text' in
field` above cover most everyday searches just fine.

### Goal: Find everyone whose last name starts with S or D

```
Person "regex(surname, '^[SD]')"
```

`[SD]` means "either S or D" -- one thing a regular expression can do that
`like(...)` can't: `like(...)`'s patterns only have `%`/`_` to work with, no
way to say "one of these letters" without writing out a separate `or` for
each one (`surname == 'Smith' or surname == 'Doyle' or ...`). This matches
every Smith and Doyle in the tree in one go.

### Goal: Find people whose death record mentions an accident or an unknown cause

```
Person "regex(death.description, 'accident|unknown')"
```

The `|` means "either of these" -- another thing a plain `like(...)`/`in`
pattern can't express in a single condition; without it, this would need
`like(death.description, '%accident%') or like(death.description,
'%unknown%')` instead.

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

### Goal: Find places enclosed by a specific county

```
Place "enclosed_by.title == 'Cook County'"
```

`enclosed_by` reaches from a place to the place that encloses it (a city's
county, say) -- and since it points to another `Place`, it chains with
itself: `enclosed_by.enclosed_by.title` reaches two levels up (skipping the
county to get straight to the state).

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

### Goal: Find families where the mother's surname contains the father's

```
Family "father.surname in mother.surname"
```

The substring form of `in` can take a field on the left now, not just a
literal -- this reads "does the mother's surname contain the father's,"
the same test as `'Jan' in given_name` but with a field standing in for
the literal substring. Works the same for two plain columns on the same
row too (`Person "given_name in surname"`).

### Goal: Find everyone born strictly between 1900 and 1950

```
Person "Date('Jan 1, 1900') < birth.date.sortval < Date('Jan 1, 1950')"
```

Comparisons can be chained just like in real Python -- this means exactly
`Date('Jan 1, 1900') < birth.date.sortval and birth.date.sortval < Date('Jan
1, 1950')`, just shorter to write. Any comparison operator works in a
chain, including a mix of them.

### Goal: Find families that have a child named Steve, written as a comprehension

```
Family "any(c.given_name == 'Steve' for c in children)"
```

The same query as the "child named Steve" example above, just spelled the
way real Python would write "does any of these match" -- `any(...)`
wrapping a generator expression is sugar for `exists(...)` and compiles to
the exact same query, not merely an equivalent one. `count(...)` has a
matching spelling too, as a list comprehension inside `len(...)`:
`len([c for c in children if c.given_name == 'Robert']) == 1` means the
same thing as `count(children, given_name == 'Robert') == 1`.

### Goal: Find everyone born in Chicago – and show me where they're buried

The "born in Chicago" half is straightforward:

In the Person view:

> "birth.place.title == 'Chicago, Cook, Illinois, USA'"

birth.place.title reaches from the person to their birth event to that event's place, to the place's full name.

#### Showing a value, not just filtering on it

The same path works in `select`, which is what decides the columns you get
back rather than which rows:

> select: ["handle", "birth.place.title as birthplace"]

Paths are checked before the query runs: `primary_name.frist_name` is an
error that lists the fields that do exist, not a column of blanks.

You can sort by a path too (`order_by` on `birth.date.sortval`), but it's
much slower than sorting by a flat field like surname -- Gramps keeps a
sorted index for surname and none for anything inside a person's record, so
sorting by birth date means reading every person in the tree. Fine for a
list you've already narrowed down; slow as the default order of a big
shared tree. See [What sorting costs](docs/where_expr.md#what-sorting-costs)
for measurements.

Text `order_by` columns sort case-insensitively (ASCII only) by default, so
an uncapitalized surname prefix like "de Vos" or "von Hebel" interleaves
with the rest of the alphabet instead of sorting after every "Z...". This
holds on both the SQL path (SQLite's built-in `NOCASE` collation) and the
evaluator/proxied path used under privacy filtering (a matching ASCII case
fold) -- see ROADMAP.md's "Default `NOCASE` collation on the SQL path".
It's ASCII-only case folding, not full locale collation, so accented
letters aren't folded on either path.

Every path you can filter on, you can also return — `father.surname`,
`primary_name.surname_list[0].surname`, `birth.date.sortval`. Add `as
<name>` to choose what the column is called in the result; without it, the
column is named by the path itself. `count(events) as n_events` returns a
count per row the same way (the alias is required there, since a count has
no path to be named after).

The "show me where they're buried" half needs a bit more care, for two reasons:

1. where_expr is a filter language — every query it writes is a true/false test per person, not a report that hands back a value. Handing values back is `select`'s job instead, and `select` takes the *same* paths (see [Showing a value, not just filtering on it](#showing-a-value-not-just-filtering-on-it) above) — so "show me their birth place" is one query. Burial is the harder half, for reason 2 below: there's no path that reaches it, so there's nothing for `select` to name.
2. Burial isn't a shortcut field the way birth/death are — birth/death reach a person's event directly by name, but a person's other events (burial included) are reached through the general events collection instead, using exists(events, ...).

#### If you already know the cemetery

in the Person view:
> "birth.place.title == 'Chicago, Cook, Illinois, USA' and exists(events, type.value == EventType.BURIAL and place.title == 'Rosehill Cemetery, Chicago, Cook, Illinois, USA')"

This matches everyone born in Chicago whose recorded burial event's place is exactly Rosehill Cemetery. The `type.value == EventType.BURIAL and place.title == '...'` part has to stay inside the exists(events, ...) parentheses — it's a condition checked against each of that person's events, not against the person directly.

#### If you don't know the cemetery — the two-part scan version

When you want each person's burial place reported back, not filtered against a name you already know, there's no single where_expr that does it. As a gramps-connect Gramplet, the workaround is: let people(where, ...) do the part it's good at, then walk each match's own events by hand for the rest — the same technique the plugins/events.py/plugins/children.py examples use, since a burial event has no birth_ref_index-style shortcut to jump straight to it:

```
# To use this, in the Gramps Connect Gramplet Editor:
# 1. Enter a title, like "Born in Chicago"
# 2. Select View: Person
# 3. Place the following as Code:
#
# Like plugins/filter.py, this is a tree-wide search, not reactive to the
# selected person, so "Re-run automatically" isn't needed.

from gramps.gen.lib import EventType

# where= can filter on birth place directly -- "birth.place.title" crosses
# person -> birth event -> place in one hop.

chicago_born = people(
    "birth.place.title == 'Chicago, Cook, Illinois, USA'",
    order=[{"column": "surname", "direction": "asc"}],
    limit=200,
)

# There's no equivalent shortcut for burial, though -- Gramps only keeps a
# ref_index for birth/death (person.birth_ref_index/death_ref_index), so a
# where= condition can only ask "does a Burial event exist" (true/false),
# never hand back *where*. Getting the actual place means walking
# event_ref_list by hand, stopping at the first Burial event found.

columns("Person", "Burial place")
for person in chicago_born:
    burial_place = None
    for event_ref in person.event_ref_list:
        event = db.get_event_from_handle(event_ref.ref)
        if event.type == EventType.BURIAL:
            burial_place = db.get_place_from_handle(event.place) if event.place else None
            break
    row(person, burial_place)
```

This is a "scan" in the sense that the inner loop isn't indexed or query-optimized — it's a plain walk over each matched person's own event list, run once per person. For a typical personal genealogy tree (a few hundred to a few thousand people), that's negligible. It only becomes noticeably slow on a large shared tree with tens of thousands of people, where doing this per-row lookup for every match adds up.

### Goal: Find notes that aren't attached to anything

```
Note "not exists(backlinks)"
```

Every collection covered above reaches *outward* from a record --
`children`, `notes`, and the rest. `backlinks` is the one that reaches the
other way, asking "does anything else point to this record at all" --
useful here for finding orphaned notes left behind after whatever they were
once attached to (a person, a source, an event) was deleted or edited.

### Goal: Find notes that are only referenced by sources, not by people

```
Note "exists(backlinks) and not exists(backlinks, _class == 'Person')"
```

`_class` is the one field a `backlinks` condition can test -- the
referrer's own type (`"Person"`, `"Source"`, ...), matching the same
`_class` name every record's serialized JSON already carries. Combined
with `count(backlinks)` (which works exactly like `count(...)` above),
`count(backlinks) > 1` is another way to ask "is this note shared by more
than one record."

## Things GOQL can't do (yet)

- Anything beyond the patterns shown above -- this is a small, fixed set of
  building blocks, not a full programming language, so anything outside it
  is rejected with an error rather than guessed at.

## Where to go from here

- [`docs/where_expr.md`](docs/where_expr.md) has the full technical
  reference, including every field and relationship name available on each
  record type.
- [`gramps_object_query_language/tests/test_where_expr_examples.py`](gramps_object_query_language/tests/test_where_expr_examples.py)
  is the test file that proves every example above actually works.
[quote="GeorgeWilmes, post:26, topic:9915"]
3. Find everyone born in Chicago – and show me where they’re buried.
[/quote]

