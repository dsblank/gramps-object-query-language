# Disabled gramps-connect filter presets

gramps-connect's Filters dialog (quick-pick redesign, commit `f9f70a6`) is
backed by a GOQL preset registry at
`../gramps-connect/app/src/data/gqlFilterPresets.ts`. Each preset maps a
Gramps built-in Rule to a GOQL expression, with a `supported: boolean` flag.
This doc started as the scoping pass for three `supported: false` presets;
all three are now fixed (see below) -- the registry has no remaining
`supported: false` entries.

## 1. `has-alternate-name` — "People with an alternate name" — RESOLVED

- Source Gramps rule: `HasAlternateName`
- **Original diagnosis (wrong):** this doc initially assumed `alternate_names`
  needed to be a registered GOQL *collection* (`exists(alternate_names, ...)`)
  to express "has at least one." That's more machinery than the rule
  actually needs.
- **Actual fix:** checking `HasAlternateName.apply_to_one`'s real source
  shows it's exactly `bool(person.alternate_names)` — a plain count, no
  per-name field condition at all. `len()` (array-length comparisons over a
  plain intra-record JSON array, shipped this session in
  gramps-object-query-language) covers this directly:
  `len(alternate_names) > 0`. No collection registration, no `any()`,
  needed.
- Registry updated: `expr: "len(alternate_names) > 0"`, `supported: true`.
  Verified exact match against gramps-core's own `example.gramps` fixture
  (2/2 people).

## 2. `adopted` — "Adopted people" — RESOLVED

- Source Gramps rule: `HaveAltFamilies`
- Blocker: the real rule walks each of a person's parent families, finds
  the `ChildRef` entry whose `ref` equals the person's own handle, and
  checks *that entry's own* `frel`/`mrel` against `ChildRefType.ADOPTED` —
  a field on the *join/link* itself, not a plain count (#1/#3's actual
  shape) or a per-element condition over an array already living in the
  current row (`any(path, condition)`'s own shape, see below) --
  `parent_family_list` on Person is a plain list of Family handles, not a
  list of `ChildRef` structs, so neither `len()` nor `any()` alone reaches
  this.
- **Actual fix:** a new kind of *self-linked* `Collection` --
  `Person.child_refs` -- registered exactly like any other collection
  (reached identically via `exists`/`count`/`any`/`len`), but whose
  condition is about one entry in the *joined* row's own array (`Family
  .child_ref_list`) that links back to the outer row, not the joined row's
  own fields. Built on top of `any()`'s own element-schema-resolution
  machinery once that shipped (see `ROADMAP.md`'s "Self-linked collection:
  `Person.child_refs`" write-up for the full design).
- Registry updated: `expr: "any(child_refs, frel.value == ChildRefType
  .ADOPTED or mrel.value == ChildRefType.ADOPTED)"`, `supported: true`.
  Verified exact match against gramps-core's own `example.gramps` fixture
  (2/2 people) and against the real rule's own `apply_to_one` source
  directly, not inferred from its description.

## 3. `has-addresses` — "People with addresses" — RESOLVED

- Source Gramps rule: `HasAddress`
- **Original diagnosis (wrong):** same mistake as `has-alternate-name` —
  assumed a registered `addresses` collection was needed.
- **Actual fix:** `HasAddress.apply_to_one`'s real source is
  `len(person.address_list) <op> userSelectedCount` (op one of
  `<`/`>`/`==`, both parameterized in gramps-core's own filter UI). The
  gramps-connect quick-pick preset only needs the simple "at least one"
  case, which `len(address_list) > 0` answers directly — `address_list` is
  always serialized as `[]` (never absent) even with none recorded, so
  `len(...) > 0` doesn't misreport "none" the way a raw JSON-path presence
  check would have (the original concern that ruled that approach out).
- Registry updated: `expr: "len(address_list) > 0"`, `supported: true`.
  Verified exact match against gramps-core's own `example.gramps` fixture
  (1/1 person), running the real rule with its own `["0", "greater than"]`
  parameterization for the equivalent comparison.

## `any(path, condition)` — built, and unified with `exists()`/`count()`

A full session was spent scoping `any(path, condition)` — an intra-record
JSON array *membership* test (unlike `len()`'s plain count) — before
realizing, by reading the actual Gramps rule source for `#1`/`#3` above,
that *neither* of those two presets actually needed it: both are plain
counts, fully covered by `len()` alone. It turned out to be needed anyway,
for `#2` (`adopted`) once that was looked at properly — and once built, it
was unified with `exists()`/`count()` as one shared dispatch (`any`/`len`
are now the canonical spellings for both the collection case and the
new array-membership case; `exists`/`count` stay as recognized, unchanged
older spellings) rather than a separate standalone primitive. See
`ROADMAP.md`'s "one dispatch, two keyword generations" write-up for the
full design.

The `incomplete-names`/`has-nickname` presets' own still-open limitation
(noted in their `notes` fields in `gqlFilterPresets.ts` — GOQL only ever
checked `primary_name`, not `alternate_names`) is now worth revisiting
with `any()` in hand, but wasn't re-examined as part of this session --
not one of the three presets this doc originally scoped.

**Lesson for next time:** check the real Rule's own `apply_to_one` source
first, before assuming a preset's GOQL gap matches the shape suggested by
its `notes` field — the notes here were written before `len()` existed and
overstated what was actually required.

## Source

Findings gathered 2026-09-16 in a gramps-connect session, from:
- `app/src/data/gqlFilterPresets.ts` (registry + `notes` per preset)
- `app/src/components/FilterPickerDialog.tsx` (disabled preset rendering)
- `app/src/data/goqlFilterCombiner.ts` (reference to unsupported presets)
- `gramps.gen.filters.rules.person.HasAlternateName`/`HasAddress`/
  `HaveAltFamilies` (real rule source, read directly rather than inferred
  from the presets' own notes)
