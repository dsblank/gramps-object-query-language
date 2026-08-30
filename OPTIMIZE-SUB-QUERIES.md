I asked in previous session:

> Which is better to optimize duplicated sub-queries: at generation time, or after the SQL has been generated in an optimize_query step?

You said:

---

Generation-time, via memoization — not a separate optimize_query
pass. The reason: your params are positional ? placeholders bound as
you build the string, so any post-hoc text-level dedup (via sqlglot or
similar) would need to re-derive correlation semantics from SQL alone
and correctly renumber/reorder bind params after merging — you'd be
solving the same "are these two subqueries actually equivalent"
problem but with less information (no access to the original
relationship-path/AST) and a new correctness risk around param
ordering, plus a new dependency to reconcile with the "no
raw-SQL-from-client-input" security invariant.

At generation time you already have the real equivalence key for free:
the relationship path + filter AST + the correlation context (which
outer alias it's bound to) — two calls with the same key are provably
the same subquery, no fuzzy text comparison needed. The practical fix
is a cache dict scoped to one query build (keyed on that tuple) that
either reuses the previously-built alias or hoists the subquery into a
WITH CTE computed once and referenced multiple times, rather than
trying to generalize CSE across the whole recursive descent up front —
start with the specific duplication pattern you're seeing (likely the
same RelatedObject hop requested by both a where_expr filter and a
select) rather than a fully general optimizer.

---

Find where in query.py those duplicate subqueries are actually being emitted so we can scope the memoization key precisely
