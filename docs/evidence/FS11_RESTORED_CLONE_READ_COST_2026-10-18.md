# FS-11 restored-clone read-cost failure, 2026-10-18

This is a failed preservation rehearsal, not UTI DEV acceptance. PR #93's
exact-head and main CI passed (1,922 tests, one skip); both final images were
prepared and independently verified. Generic deployment remained disabled.

After observing the genuine 00:30 NAV and 00:40 audit executions complete on
dataset 27, the operator took a new regional backup and sealed every retained
prior-layer row. Baseline: 14,405 funds and 4,248,810 NAV records. The five new
NAV rows relative to FS-10 came from the normal ingestion cycle, not remediation.

- Source SHA: `c80d2d9df40cff7e28d236d47dccb638ac7ebcf2`.
- Before dump: `5a310430f95f080d4d12872ba2860eb4763e144c3e0b8e0dd9fcfc33cc8c5215`.
- Clone execution: `mf-book-clone-dev-uti-261017a-nf57q`, terminal failure at
  2026-10-18 01:15:29 UTC; one failed task, no retry.
- Failure: PostgreSQL `TransactionTimeout` in the second clone activation's
  current-field check. Stack-location timing shows repeated prior-population
  projection expansion consuming roughly 90–105 seconds per capture, in
  addition to actual source acquisition, full original-data sealing, activation,
  recovery, replay and reactivation. The source inputs were not rejected.

The coordinator stopped before private staging or live schema installation.
Its frozen root, backup, originals, intent and failure receipt remain retained.
No failed clone may be counted as acceptance or reused as a live rollout root.

## Bounded read-path repair

Reuse the installed, sealed graph within each SELECT statement. Materialize all
assertion rows for the requested codes once, then execute the exact installed
head, selected-field, current-value and resolved definitions against that local
read result. No selection, conflict, fallback, provenance, source parsing or
calculation rule is reimplemented. Quoted source text is not rewritten.

Keep ACL/definition checks and require matching default view-owner execution
contexts. Compare all four original population hashes and single-fund field rows
with the previous queries, including restricted roles and UTI recovery/replay.
There is no persistent cache, new schema, permission grant, policy replacement,
source-record edit or timeout/resource increase. All original preservation and
independent-source checks remain mandatory in the next fresh exact-image chain.

Local non-database checks and CI PostgreSQL proofs must be recorded separately.
The retained local synthetic PostgreSQL listener was unavailable; an isolated
native scratch initialization also failed at shared-memory allocation. Neither
Docker nor production PostgreSQL was restarted, repaired or reconfigured.

This repair is not deployment or certification. A new final image, fresh backup,
full restored-clone gate, actual source run, private/core reads, recovery, genuine
timer and whole-preservation closeout are still required. Do not increase limits
or skip a gate to fit between normal ingestion schedules.
