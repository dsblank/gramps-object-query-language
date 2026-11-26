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
Person "len(primary_name.surname_list) > 1"
```

Gramps lets a person have several last names at once (a maiden name and a
married name, say). `len(...)` counts how many are actually recorded --
before it existed, the only way to ask this was checking whether a
particular position in the list was filled in
(`primary_name.surname_list[1].surname != None`), which only really answers
"two or more" and breaks down for "three or more."

### Goal: Find people with an alternate name recorded as "Doyle"

```
Person "any(n.first_name == 'Doyle' for n in alternate_names)"
```

`any(... for n in ...)` asks whether *any* entry of a list matches, the way
it would in Python. `alternate_names` -- a person's names beyond the
primary one (a maiden name, a name used before marriage, an anglicized
name, ...) -- isn't a collection like `notes`/`citations`: each one only
ever exists embedded in the person record itself, with no page of its
own. The comprehension works the same over either kind of list. Without a
condition, `any(n for n in alternate_names)` just asks "is there at least
one at all," identical to `len(alternate_names) > 0`.

### Goal: Find people whose occupation is recorded as Farmer

```
Person "attribute_list['Occupation'].value == 'Farmer'"
```

A name in the brackets picks a person's first attribute of that type.
Built-in types like Occupation, Age or Nickname and types you made up
yourself ("Hair color") both work, spelled exactly as they appear in
Gramps. It works one link away too: in the Citation view,
`source.attribute_list['Film'].value` reads the cited source's "Film"
attribute. See
[Entries by type](docs/where_expr.md#entries-by-type-attribute_listage).

### Goal: Find people who had more than one occupation, one of them Miller

```
Person "len(attribute_list['Occupation']) > 1 and any(a.value == 'Miller' for a in attribute_list['Occupation'])"
```

Counted, looped over or tested with `any(...)`, `attribute_list['Occupation']`
is every Occupation attribute, not just the first. Web links work the same
way by their type: `urls['E-mail'].path` is a person's first e-mail
address, and `len(urls['E-mail'])` how many they have.

### Goal: Find notes attached to someone named Ann

```
Note "any(p.primary_name.first_name == 'Ann' for p in backlinks['Person'])"
```

`backlinks` is everything pointing at a record; with a kind of record in
brackets, it is those records themselves, so their own fields can be
tested. `len(backlinks['Family'])` counts the families pointing here, and
`backlinks['Person'].gramps_id` reads one of the people. See
[One kind of referrer](docs/where_expr.md#one-kind-of-referrer-backlinksperson).

### Goal: Show who each event belongs to, and their age at it

```
Event "any(r.referrer.primary_name.surname == 'Smith' for r in event_refs['Primary'])"
```

with `select`:

```
event_refs['Primary'].referrer.primary_name as participant
(date.sortval - event_refs['Primary'].referrer.birth.date.sortval) // 365 as age
```

An event doesn't list its people -- each person and family lists the event,
with a role. `event_refs` is those links seen from the event, and
`referrer` is the person or family holding one. `event_refs['Primary']` is
the Primary ones; read straight off, the first of them. A path that only a
person has (`primary_name`) gives no value for a family's link. See
[Who an event belongs to](docs/where_expr.md#who-an-event-belongs-to-eventevent_refs).

### Goal: Find people who were adopted

```
Person "any(ref for ref in child_refs if ref.frel.value == ChildRefType.ADOPTED or ref.mrel.value == ChildRefType.ADOPTED)"
```

Gramps doesn't record "adopted" as a plain field on a person -- it's
recorded on the *link* between a person and each of their parent families
(the father's-side and mother's-side relationship, `frel`/`mrel`), since a
person can be born into one family and later adopted into another.
`child_refs` is a special collection for exactly this: for each of a
person's own parent families, it reaches the one entry in that family's
own child list that names *this* person, so `frel`/`mrel` mean that link's
own relationship type, not a field on the person or the family themselves.
The comprehension makes that provenance explicit -- `ref.frel`/`ref.mrel`
visibly belong to the bound `ref`, i.e. the link, not to `Person` or
`Family`.

### Goal: Find families that have a child named Steve

```
Family "any(c.given_name == 'Steve' for c in children)"
```

This matches a family if *any* of its children satisfies
the condition -- unlike `father`/`mother`, which each always reach exactly
one person, a family can have any number of children, so this needs its own
"does at least one of them match" check rather than an ordinary field
reference.

### Goal: Find families with no children recorded at all

```
Family "not any(c for c in children)"
```

Leaving out the condition (`any(c for c in children)` alone) just asks "does this
family have any recorded child at all" -- `not` in front flips that to "no
children recorded."

### Goal: Find people who don't have any notes attached to their record

```
Person "not any(n for n in notes)"
```

The same idea, starting from `Person` instead: `notes` reaches every note
attached to a person's record, and `not any(n for n in notes)` matches
whenever there aren't any.

### Goal: Find families with more than two children

```
Family "len(children) > 2"
```

`len(children)` counts how many children a family has recorded --
`any(...)` can only tell you whether *at least one* child matches
something, `len(...)` tells you *how many*.

### Goal: Find families with more than one son

```
Family "len([c for c in children if c.gender == Person.MALE]) > 1"
```

A list comprehension with an `if` (the same kind of condition `any(...)`
takes) counts only the children who match it -- here, only the sons.

### Goal: Find people with more than one father

```
Person "len(distinct([f.father for f in parent_families])) > 1"
```

`[f.father for f in parent_families]` is the list of fathers of the
person's parent families (a family with no father recorded leaves no
entry), and `distinct(...)` counts each different father once -- a man
recorded as father in two of the families is one father, not two. As a
column, `len(distinct([f.father for f in parent_families])) - 1 as
other_fathers` shows how many fathers someone has besides one.

### Goal: Find people with a grandchild named Robert

```
Person "any(c.given_name == 'Robert' for f in families for k in f.children for g in k.families for c in g.children)"
```

Several `for`s in one `any(...)` walk from list to list: each one goes
over a list of the loop variable just before it -- the person's families,
each family's children, each child's own families, and their children.

### Goal: Find people whose events happened in more than one place

```
Person "len(distinct([e.place for e in events])) > 1"
```

The same works for any field: `e.place` compares the places themselves,
`e.place.title` their titles, `n.first_name for n in alternate_names`
the names.

### Goal: Find people with a well-sourced record

```
Person "any(c.confidence >= Citation.CONF_HIGH for c in citations)"
```

`children` isn't the only collection; there are more recorded
per-person -- `notes`, `citations`, `media`, and `tags` are
available the same way on almost every record type, plus a few more
specific to each type: a person's `families` (as a spouse), `parent_families`
(as a child), `associations` (links to other people), and `events` (every
recorded event, not just birth/death).

### Goal: Find people linked to someone named Bob via an association

```
Person "any(a.given_name == 'Bob' for a in associations)"
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
holds for every text field (`father.surname` too), on SQLite (its
built-in `NOCASE` collation), on PostgreSQL (the same fold spelled out)
and under privacy filtering -- see ROADMAP.md's "Default `NOCASE`
collation on the SQL path". It's ASCII-only case folding, not full locale
collation, so accented letters aren't folded anywhere. `<` and `>` on text
ignore case the same way, so a filter agrees with the sort.

Every path you can filter on, you can also return — `father.surname`,
`primary_name.surname_list[0].surname`, `birth.date.sortval`. Add `as
<name>` to choose what the column is called in the result; without it, the
column is named by the path itself. `len(events) as n_events` returns a
count per row the same way (the alias is required there, since a count has
no path to be named after).

The "show me where they're buried" half is a list comprehension taken by
index: a person's burial isn't a shortcut field the way birth/death are,
but the first of their events that is a burial can be picked out of the
`events` collection, and the path goes on into it:

> select: ["handle", "birth.place.title as born", "[e.place.title for e in events if e.type.value == EventType.BURIAL][0] as buried"]

with the "born in Chicago" filter above as the `where_expr`. Someone with
no burial event recorded gets an empty `buried`.

#### If you only want the ones buried in one cemetery

in the Person view:
> "birth.place.title == 'Chicago, Cook, Illinois, USA' and any(e.type.value == EventType.BURIAL and e.place.title == 'Rosehill Cemetery, Chicago, Cook, Illinois, USA' for e in events)"

This matches everyone born in Chicago whose recorded burial event's place is exactly Rosehill Cemetery. The `e.type.value == EventType.BURIAL and e.place.title == '...'` part is checked against each of that person's events together, so it has to be one event that is both a burial and at Rosehill.

#### Before comprehensions could be taken by index

The burial half used to need more care, for two reasons:

1. where_expr is a filter language — every query it writes is a true/false test per person, not a report that hands back a value. Handing values back is `select`'s job instead, and `select` takes the *same* paths (see [Showing a value, not just filtering on it](#showing-a-value-not-just-filtering-on-it) above) — so "show me their birth place" is one query. Burial is the harder half, for reason 2 below: there's no path that reaches it, so there's nothing for `select` to name.
2. Burial isn't a shortcut field the way birth/death are — birth/death reach a person's event directly by name, but a person's other events (burial included) are reached through the general events collection instead.

As a gramps-connect Gramplet, the workaround was: let people(where, ...) do the part it's good at, then walk each match's own events by hand for the rest — the same technique the plugins/events.py/plugins/children.py examples use, since a burial event has no birth_ref_index-style shortcut to jump straight to it:

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
Note "not any(bl for bl in backlinks)"
```

Every collection covered above reaches *outward* from a record --
`children`, `notes`, and the rest. `backlinks` is the one that reaches the
other way, asking "does anything else point to this record at all" --
useful here for finding orphaned notes left behind after whatever they were
once attached to (a person, a source, an event) was deleted or edited.

### Goal: Find notes that are only referenced by sources, not by people

```
Note "any(bl for bl in backlinks) and not any(bl._class == 'Person' for bl in backlinks)"
```

`_class` is the one field a `backlinks` condition can test -- the
referrer's own type (`"Person"`, `"Source"`, ...), matching the same
`_class` name every record's serialized JSON already carries. Combined
with `len(backlinks)` (which works exactly like `len(...)` above),
`len(backlinks) > 1` is another way to ask "is this note shared by more
than one record."

### Goal: Find every census, and every event of a type you made up yourself

```
Event "type == 'Census'"
Event "type in ['Census', 'Tax list']"
Person "any(e.type == 'Burial' for e in events)"
```

A type -- an event's type, a family's relationship type, a child's
relationship to its parents, and so on -- can be compared with its name.
Gramps' own types match by their English name or by their name in the
language Gramps is running in, ignoring case (`'census'` works too). Any
other name means a custom type someone typed in, like "Tax list", and
matches only that exact text. `type == 'Census'` is the same query as
`type.value == EventType.CENSUS`, and runs just as fast.

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

