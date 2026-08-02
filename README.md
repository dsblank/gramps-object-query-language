# gramps-object-query-language

A small, closed query AST and SQL compiler for fast object queries against
[Gramps](https://gramps-project.org/) genealogy data.

This is not a general query language, not GraphQL, and not a raw-SQL
passthrough. It compiles a structured `Query` (`select`/`where`/`order_by`/
`limit`/`after`) -- or an "almost Python" expression string -- into
parameterized SQL against Gramps' flattened secondary columns, with every
column checked against a fixed per-type whitelist before the compiler ever
touches it.

It is standalone and privacy-agnostic: it carries no knowledge of proxies,
permissions, or any particular web API. An `evaluator`/`proxied_query` path
is also included for evaluating the same query AST directly against real
(possibly proxied) Gramps objects, for callers that can't run raw SQL
against an unproxied database.

## Install

```bash
pip install gramps-object-query-language
```

## Modules

- `gramps_object_query_language.query` -- the query AST and SQL compiler.
- `gramps_object_query_language.query_lang` -- an "almost Python" expression
  parser that translates into the same `where` shape.
- `gramps_object_query_language.evaluator` -- evaluates the AST directly
  against real Gramps objects (no SQL), for use with a proxied database.
- `gramps_object_query_language.proxied_query` -- runs a `where` expression
  through Gramps' own `Filter`/`Rule` machinery against a possibly-proxied
  database.

## Development

```bash
pip install -e ".[test]"
pytest
```

## License

GNU Affero General Public License v3 or later (AGPL-3.0-or-later). See
[LICENSE](LICENSE).
