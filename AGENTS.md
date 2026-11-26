# Project rules

## Principles

### Things that look the same work the same

GOQL is written by genealogists, not database programmers. They can't see
how a field is stored -- a list kept inside the record (`child_ref_list`),
or a link GOQL follows to other records (`children`) -- so they reasonably
expect two forms that look alike to behave alike. When one works and the
other is rejected, the language feels arbitrary.

- A form that works in one position should work in every position where
  the same-looking form makes sense: on the record itself, after a link
  (`parent_families[0].`), inside a loop, in `where`, `select` and
  `order_by`, and in both the SQL translation and the Python evaluator.
  For example, `len(children)` works on a Family, so
  `len(parent_families[0].children)` should work on a Person too.
- When a feature can't cover every position in one change, say so in the
  plan before building it and decide on purpose. A partial version needs
  an explicit decision, not just a ROADMAP note added afterwards.
- When someone runs into one of these differences, fix the language first.
  Documenting the other form as a workaround ("use `child_ref_list`
  instead") can be a stopgap, never the fix.
- An error message for an unsupported form should say what to write
  instead, in the user's own words, but a good error doesn't make the gap
  acceptable.
- Tab completion should offer exactly what the language accepts, so the
  completer and the parser change together.

Known open case: a collection past a link can only be indexed
(`parent_families[0].children[0]` works; `len(...)`, `any(...)` and loops
over `parent_families[0].children` don't). See ROADMAP.md.
