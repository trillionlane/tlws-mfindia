# Next three backend/data sprints — approved 2026-10-18

Follow the accepted FS-11 DEV closeout, not the superseded failed attempts.
Each sprint has a separate reviewed, fully tested PR, normal exact-head merge
and actual bounded DEV evidence before its successor publishes data. No UI,
PROD, raw correction/deletion, identity merge, broad IAM, new GitHub gate variables
or source/validation waiver. Retain every original, prior result and failed run.

## FS-12 — Source reliability and actionable coverage inventory

Deliver an operator-consumable, policy-versioned read-only inventory of every
installed AMC's last attempt/last success, missing recent reporting periods,
retained per-variant gaps, field-level unavailable reasons, identity-population
drift and source-authority expiry warnings. Primary variant gaps and individual
field gaps are different denominators. Unknown reasons remain visible.

Read existing sealed batch/run evidence under the restricted reader in one
repeatable-read, read-only transaction. Validate seals and whole-population
accounting before returning any report. No new database schema, source fetching,
publication, human verification, API contract change or serving-image switch is
needed for this diagnostic export. A successful inventory is not cloud health,
source correctness or full coverage certification.

Separately record actual immutable normal NAV/audit/source executions and their
application results; make ordinary bounded Kotak catalogue probes without
following challenges, cookies, retries or alternate-egress evasion. Retain source
evidence and distinguish failure from known-document use. The earlier future-ETF
NAV rejection remains an investigation: never widen the category exception,
change as-of or rerun ingestion merely to make it succeed.

Acceptance: deterministic fixtures and PostgreSQL restricted/read-only tests;
all existing contracts/policies untouched; exact-head/main full CI; final-image
actual DEV inventory, exact source/period/variant/field reconciliation, effective
identity and execution evidence, zero data writes and protected runtime unchanged.
Pure read-only diagnostics need no database restore or traffic promotion; any
later state-changing source delivery still requires a fresh backup/clone.

Operator tools (inside the exact approved image, never with a locally fetched
database secret):

```sh
python scripts/inventory_fact_source_operations_dev.py --as-of <explicit-UTC-instant>
python scripts/probe_fact_source_catalogues_dev.py --as-of <explicit-UTC-instant>
```

Both bind their result to `REVIEW_MANIFEST` and `DEPLOYMENT_GIT_SHA`, using the
existing lossless sealed-evidence transport. The inventory uses only the existing
restricted-reader DSN; the catalogue probe refuses any DB DSN. The probe makes
exactly two ordinary requests through the frozen no-redirect/no-retry transport.
Successful original catalogue bodies (including malformed bodies) are retained
in the sealed evidence; a transport failure is explicitly not a retained body.
It never downloads a candidate PDF or extends the known-document authority.
An exit-zero probe can still report unavailable discovery; it is not source
admission or permission to change the runtime source configuration.

## FS-13 — Evidence-backed gap closure in the current four AMCs

Use FS-12's real inventory to select a finite, high-value gap family. Inspect
official originals and exact issuer/AMFI identity edges first. Record before
counts and independent current/older-period reference values. A missing bridge
or contradictory identity is not permission to use name similarity or another
plan's data. Do not promise that every gap is resolvable.

Add a new parser/source-policy version, never edit the frozen accepted version.
Publish only supported new observations through the existing reversible current
projection. Preserve old observations and untouched AMC projections; TER stays
TER and missing/conflicting newer fields cannot erase an older usable value.

Acceptance: a measurable increase in supported facts/variants or newly evidenced
periods against exact official references; independent negative controls; full
CI; fresh backup, actual-source restored clone, restricted installation, actual
source run/archive proof, private/core reads, recovery/replay/reactivation, genuine
timer evidence and final original/prior-layer preservation. If required official
evidence is unavailable, retain that blocker rather than claiming gap closure.

## EXP-01 — Next source-ready AMC cohort

Select exact membership after ordinary official discovery, document-layout and
identity assessment. The directory's alphabetical wave assignments are planning
placeholders, not evidence of readiness. Normally at most four AMCs; split a
complex source into a finite sub-sprint instead of weakening gates.

Deliver two actual reporting periods where publicly available, exact scheme/
plan/option matching, supported dated facts and explicit gaps across each full
retained cohort. Reuse the existing job, private contracts and additive layers.
No arbitrary caller URLs, silent catalogue fallback or presumed whole-factsheet
completeness. Meet the same full source/preservation/recovery/timer/API gates as
FS-13 before declaring each named AMC accepted.

## Status

Planning/implementation of FS-12 is in progress. FS-13 and EXP-01 have not started;
membership, gap family and actual evidence must be recorded before publication.
The accepted source/review SHA and protected core SHA remain distinct. Keep normal
NAV/audit and daily source windows clear during controlled rollouts.
