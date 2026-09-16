# Disabled gramps-connect filter presets

gramps-connect's Filters dialog (quick-pick redesign, commit `f9f70a6`) is
backed by a GOQL preset registry at
`../gramps-connect/app/src/data/gqlFilterPresets.ts`. Each preset maps a
Gramps built-in Rule to a GOQL expression, with a `supported: boolean` flag.
This doc started as the scoping pass for three `supported: false` presets;
two are now fixed (see below), one remains open.

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

## 2. `adopted` — "Adopted people" — STILL OPEN

- Source Gramps rule: `HaveAltFamilies`
- Blocker: the real rule walks each of a person's parent families, finds
  the `ChildRef` entry whose `ref` equals the person's own handle, and
  checks *that entry's own* `frel`/`mrel` against `ChildRefType.ADOPTED`.
  GOQL's `exists(parent_families, ...)` join only exposes the joined
  Family row's own fields to the condition — `frel`/`mrel` live on the
  `ChildRef` struct, a sibling of `ref` inside the family's child list, not
  reachable through that join today.
- What's needed: a way to join through to the specific `ChildRef` entry
  matching the current person (not just the parent `Family` row), exposing
  `frel`/`mrel` on that entry. Likely needs either a dedicated
  `child_ref_in(parent_families, frel=..., mrel=...)`-style construct, or
  making `parent_families` a richer join that carries the person's own
  `ChildRef` alongside the joined `Family`.
- **Not** the same gap as the other two turned out to be: this one is
  about a field on the *join/link* itself (`ChildRef`), not a plain count
  or a per-element condition over an array already living in the current
  row — `any(path, condition)` (see below) wouldn't help here even once
  built, since `parent_family_list` on Person is a plain list of Family
  handles, not a list of `ChildRef` structs.

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

## `any(path, condition)` — scoped, not built (not needed for #1/#3 after all)

A full session was spent scoping `any(path, condition)` — an intra-record
JSON array *membership* test (unlike `len()`'s plain count) — before
realizing, by reading the actual Gramps rule source for `#1`/`#3` above,
that neither preset actually needed it: both are plain counts, fully
covered by `len()` alone. The `any()` design (large difficulty, no target
table, condition resolved against a synthetic per-element `ObjectTypeSpec`)
is still recorded in `ROADMAP.md`'s "Possibilities" section for whenever a
real per-element-field use case shows up (e.g. "people with a nickname
recorded on *any* name, not just the primary one" — the `incomplete-names`/
`has-nickname` presets' own still-open limitation, noted in their `notes`
fields in `gqlFilterPresets.ts`).

**Lesson for next time:** check the real Rule's own `apply_to_one` source
first, before assuming a preset's GOQL gap matches the shape suggested by
its `notes` field — the notes here were written before `len()` existed and
overstated what was actually required.

## Source

Findings gathered 2026-09-16 in a gramps-connect session, from:
- `app/src/data/gqlFilterPresets.ts` (registry + `notes` per preset)
- `app/src/components/FilterPickerDialog.tsx` (disabled preset rendering)
- `app/src/data/goqlFilterCombiner.ts` (reference to unsupported presets)
- `gramps.gen.filters.rules.person.HasAlternateName`/`HasAddress` (real rule
  source, read directly rather than inferred from the presets' own notes)
