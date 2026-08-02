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

## Things this can't do (yet)

- **"or"** -- there's no way today to say "match this condition *or* that
  one." You can only combine conditions with `and`.
- **"not"** -- there's no way to negate a whole condition.
- Anything beyond the patterns shown above -- this is a small, fixed set of
  building blocks, not a full programming language, so anything outside it
  is rejected with an error rather than guessed at.

## Where to go from here

- [`docs/where_expr.md`](docs/where_expr.md) has the full technical
  reference, including every field and relationship name available on each
  record type.
- [`gramps_object_query_language/tests/test_where_expr_examples.py`](gramps_object_query_language/tests/test_where_expr_examples.py)
  is the test file that proves every example above actually works.
