# gramps-object-query-language

A small, closed query AST and SQL compiler for fast object queries against
[Gramps](https://gramps-project.org/) genealogy data.

This is not a general query language, not GraphQL, and not a raw-SQL
passthrough. It compiles a structured `Query` (`select`/`where`/`order_by`/
`limit`/`after`) -- or an "almost Python" expression string -- into
parameterized SQL against Gramps' flattened secondary columns, with every
column checked against a fixed per-type whitelist before the compiler ever
touches it.

Paths into the JSON blob are checked, not trusted: every Gramps class
publishes a complete recursive `get_schema()`, so
`primary_name.surname_list[0].surname` is whitelisted the same way a flat
column name is, and a typo is a compile-time error naming the fields that
exist rather than an all-NULL column.

It is standalone and privacy-agnostic: it carries no knowledge of proxies,
permissions, or any particular web API. An `evaluator`/`proxied_query` path
is also included for evaluating the same query AST directly against real
(possibly proxied) Gramps objects, for callers that can't run raw SQL
against an unproxied database.

## Install

```bash
pip install gramps-object-query-language
```

## Documentation

- [`README-query-language.md`](README-query-language.md) -- a plain-language,
  goal-first guide to `where_expr` for non-programmers ("Find all the
  families where the mom died before the dad" -> the query for it).
- [`docs/where_expr.md`](docs/where_expr.md) -- the technical reference for
  the "almost Python" `where_expr` filter language (`Person "gender ==
  Person.MALE"`, `Family "mother.death.date.sortval < father.death.date.sortval"`,
  ...), with every example tested against real SQLite.

## Modules

- `gramps_object_query_language.query` -- the query AST and SQL compiler.
- `gramps_object_query_language.query_lang` -- an "almost Python" expression
  parser (`parse_expr`) that translates into the same `where` shape, plus
  `compile_expr`, which translates it the rest of the way into `query.py`'s
  executable AST for callers that want to run it directly, and
  `parse_select`, which parses `select` entries (`"birth.place.title as
  birthplace"`, `"count(events) as n_events"`) written in that same path
  grammar. See [`docs/where_expr.md`](docs/where_expr.md).
- `gramps_object_query_language.evaluator` -- evaluates the AST directly
  against real Gramps objects (no SQL), for use with a proxied database.
- `gramps_object_query_language.proxied_query` -- runs a `where` expression
  through Gramps' own `Filter`/`Rule` machinery against a possibly-proxied
  database.

## Benchmarks

`benchmarks/sort_cost.py` measures what each kind of sort column costs
(indexed flat column vs. JSON path vs. relationship hop), with SQLite's own
query plans. Its output is quoted in
[`docs/where_expr.md`](docs/where_expr.md#what-sorting-costs) -- run it
rather than trusting the numbers there.

```bash
python benchmarks/sort_cost.py --people 20000
```

## Development

```bash
pip install -e ".[test]"
pytest
```

## License

GNU Affero General Public License v3 or later (AGPL-3.0-or-later). See
[LICENSE](LICENSE).
