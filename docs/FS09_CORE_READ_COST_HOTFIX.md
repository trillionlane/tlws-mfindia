# FS-09 core facts read cost

## Observed gate failure

The SBI post-activation certification on 2026-10-17 failed with a
`ReadTimeout`. The actual core API returned fund 103215 after **42.785 s**,
above the existing 30-second client bound. The new private metadata reads
completed in under a second. The attempt was rejected and its captured prior
graph, source-job configuration and service traffic were restored. Source
records and failed-attempt evidence remain retained.

This is not permission to raise the timeout, skip core certification, deploy
UI changes or treat the failed activation as accepted.

## Execution-only change

The existing `current_values` view separately traverses the same selected
assertion graph for six scalar aliases. Its full-outer-join facade also
prevents a code lookup from reaching both branches efficiently.

The separately pinned `amc_book_core_projection_v1.sql.inc`:

- expands aliases from one selected-field traversal;
- expresses the facade as baseline LEFT JOIN overlay, UNION ALL overlay rows
  without a baseline, preserving the full-join result;
- preserves every column/type, baseline-only row, overlay-only row, null,
  selected value, manager order and numeric cast;
- leaves all source, review, publication and policy rows unchanged;
- updates the existing core facade and seals its exact view definitions in
  the existing owner-controlled activation transaction.

Only book source version 2 and later use the optimization. Earlier SQL source
bytes, schema migration hashes, registry hashes, deterministic selection
policies, grants, runtime identities and endpoint contracts are unchanged.
Recovery recognizes both historical and single-pass current-values definitions,
replays only pinned source, and checks the exact captured definition. It never
accepts a resealed approximation of a prior graph.

## Required evidence

Disposable PostgreSQL tests compare **all complete facade rows** and column
signatures before/after, including a real synthetic SBI publication, all
retained AMC rows and the unchanged original-table state. They exercise normal
activation, exact replay and idempotent recovery. Actual-source retained-clone
read-only measurements are supporting evidence, not a live certificate.

Before DEV acceptance: exact-head full CI, final immutable app/tools images,
fresh backup, restored-clone full preservation and actual-source rehearsal,
core detail/batch latency checks on that final image, private pre/post
activation certification, actual recovery/replay/reactivation, a genuine
timer-created source run, all 19 core routes and original/prior-layer closeout.
Retain the failed C rollout, backups and archives. Use a new frozen rollout
manifest/evidence root rather than overwriting its files.
