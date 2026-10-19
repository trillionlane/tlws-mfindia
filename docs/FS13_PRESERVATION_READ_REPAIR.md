# FS13 preservation value-read repair

## Scope and failure

The controlled C rollout at merged `f944fcf` failed in its isolated restored
clone, before live installation, source execution or traffic changes. Execution
`mf-book-clone-dev-uti-gap-261019c-7mz5j` completed unsuccessfully at
2026-10-19 01:09:37 UTC. Its full four-original-query comparison passed for all
3,220 retained prior-AMC codes, and its isolated UTI publication retained 67
original archives. These partial observations are not rollout acceptance.

`book_projection.activate()` then called `capture_target()` under the unchanged
five-second statement / fifteen-second owner-transaction limits. The selected
cell read timed out. It materialized full assertion rows and computed source
member hashes, although the target certificate only used each selected value
row's SQL hash with the `sources` property removed.

The user explicitly approved a narrow preservation read-path optimization after
this failure. A/B/C attempts, originals and backups remain retained and cannot
be rerun or reused for a new rollout.

## Change and invariants

The target reader now selects directly from the installed
`mf_live_facts.selected_fields` view. It names the six value columns in a
`jsonb_build_object`, preserving exactly the old
`to_jsonb(selected_row)-'sources'` representation and SHA-256 hash in PostgreSQL.
Naming the required columns allows PostgreSQL to discard unused provenance
aggregates; Python does not reconstruct selection, numeric values or conflicts.

The reader validates the complete ordered column signature and fails closed on
extra, missing or reordered columns. Existing ACL, sealed-view, owner and core
facade checks remain mandatory. Every requested retained code is considered;
there is no sampling, truncation, new cache, table, role grant or fallback.

The full four-relation proof and its exact provenance-member multisets continue
using the unchanged `_rows` implementation. Source-period primary facts and TER
preservation are unchanged. There is no source parser, SQL migration, registered
policy, current-view definition, financial method, raw record, identity or
published-result change. Source5 / policy11 / SQL044 pins are unchanged.

The owner limits remain five-second statements, five-second lock acquisition
and fifteen-second transactions. Clone CPU, memory and task limits are unchanged.
No PostgreSQL configuration, driver or statistics tuning is part of this fix.

## Actual read-only diagnostic

The exact guarded candidate function, SHA-256
`d40c9c5324237ae39941fa609944ddddf8270fedbb994a0396e7a1a092937f0e`,
was checked in execution `mf-fs13-selected-read-final-261019b-94k96` against the
accepted DEV source4/policy8 state. The authenticated restricted reader used one
repeatable-read, read-only snapshot and all 1,373 retained UTI codes, not a sample.
All 242 selected-value cells matched the original implementation's SQL hashes;
both result sets sealed to
`1f5c24ccfdb25468374273adb0cc1c94bb348b794962a36066ad14cb8a2354f4`.

The guarded candidate took 4.213 seconds including its metadata/ACL checks and
completed under an unchanged five-second statement limit. The original read
took 4.371 seconds in that snapshot. These single measurements are not a
worst-case performance guarantee or a controlled cold-cache benchmark.
Source requests and data writes were zero, and the protected runtime/IAM/normal
job/schedule metadata matched before and after. The existing read-only
rehearsal bounds were used for the original comparison; owner transaction
bounds were not changed. No full writer transaction or activation was tested.

The earlier exploratory candidate, before the execution-context guard was
restored, is separately retained as `mf-fs13-selected-read-probe-261019a-t6jk8`;
it is not evidence for the final function's identity. Neither diagnostic reuses
a failed backup or counts as restored-clone/runtime acceptance.

## Validation and delivery

- Negative tests reject invalid population inputs, execution-context drift and
  column-signature drift.
- Disposable PostgreSQL tests compare all selected value hashes to the original
  row expression for every retained fixture fund under all restricted readers,
  before and after activation and recovery. They verify no original-data or
  evidence-ledger writes and unchanged owner transaction limits.
- The focused PostgreSQL CI gate includes the new regression alongside every
  retained UTI migration/publication/recovery regression. The whole suite,
  dependency audit, contracts, both images and all existing smokes still run.
- Any metadata-bound actual read-only diagnostic is evidence about its exact
  candidate/query and population, not publication or restored-clone acceptance.
- After exact-head review/CI and normal merge, require full main CI, image-only
  preparation and strict immutable source provenance for both images.
- A new safe-window rollout requires a wholly fresh backup and a new root,
  all four ORIGINAL queries over the entire retained population, all sixteen
  preservation/private/source/recovery/timer gates, coordinator exit zero and
  the sealed acceptance certificate, then the additional core-value witness and
  reviewed Git handover. Never reuse C or relax a gate to meet a window.

FS13 and EXP01 remain incomplete until actual DEV evidence is accepted.
Observations remain `needs_verification`; TER is not BER; adjusted IDCW analytics
remain disabled. This is neither all-AMC nor all-field completeness.
