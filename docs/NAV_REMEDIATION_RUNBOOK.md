# NAV remediation investigation — DATA-02

DATA-02 delivers a certified case inventory, archived original source evidence,
an append-only review/proposal ledger, and scratch-only correction rehearsal.
There is no live `apply`, `rollback`, auto-repair, backfill, UI, API, or scheduler
action. Existing source NAVs, audit signals and API eligibility stay unchanged.
Implementation and operational adjudication are separate outcomes.

## Baseline and acceptance

DATA-01 certified dataset 8: 14,378 schemes and 4,231,732 NAV observations, with
findings SHA-256
`e63f5a452b8ba8274cd18113066ac5bb4c1063feed828912e474a06316a37684`.
The initial read-only DATA-02 export accounts for all 1,440 distinct flagged
schemes in 642 cases: 633 duplicate clusters, eight constant-series cases and
one terminal-reset case. The 1,435 duplicate flags count schemes, not pairs.
Signals overlap. Counts are baseline evidence, not assertions against later
datasets. All cases start `pending_review`; no conclusive decisions exist yet.
Priority is terminal reset, constant series, Growth-containing duplicate
clusters, then other duplicates. Growth presence does not establish lifecycle
or comparison eligibility.

Acceptance requires deterministic complete accounting, stale/unsupported/
incomplete audit rejection, exact Decimal proposals, immutable artifacts and
ledger rows, and PostgreSQL proofs of read-only export, replay, interrupted
transactions and conditional rollback. Synthetic tests do not constitute an
operational backup-restore certificate or authoritative case adjudication.

## Read-only inventory

Use a source principal with SELECT on assessments, funds, AMCs, variants,
dataset summary and NAV history. The existing auditor lacks AMCs SELECT; the
read-only API principal has the necessary reads. Do not widen the auditor.
Supply the source DSN through `MFDATAINDIA_DSN` or `--dsn`; never retain it in
artifacts, logs, or PRs.

```sh
python scripts/review_nav_remediation.py export-register \
  --expected-dataset-version 8 \
  --expected-findings-sha256 e63f5a452b8ba8274cd18113066ac5bb4c1063feed828912e474a06316a37684 \
  --output /private-review/case-register.json
```

Export uses a repeatable-read READ ONLY transaction, a 120-second statement
timeout, and the shared refresh/audit lock. It verifies the complete current
assessment population and reproduces the certificate's findings hash; duplicate
clusters must be complete and symmetric. A stale version, unsupported methodology,
hash mismatch or lock conflict rejects the operation. Obtain fresh evidence
before another attempt. Full code lists stay in the private output file; stdout
contains only its hash. Cases include identity, ISINs, plan/option, date bounds,
signals, counterpart evidence and certificate/manifest hashes. Preserve files in
a private versioned evidence location; the CLI refuses artifact overwrites.

## Evidence and human decisions

Retrieve bounded authoritative reports separately and preserve original HTTP
response bytes, final URL, successful status, retrieval time and requested
date window. Do not reserialize parsed rows as original bytes. Hashes prove
unchanged content, not the authenticity of an operator's acquisition claim.
Reviewers must verify provenance and coverage independently.

```sh
python scripts/review_nav_remediation.py archive-evidence \
  --raw-file /private-review/amfi-original.txt --source AMFI_NAV_HISTORY \
  --url 'https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?frmdt=01-Jan-2026&todt=02-Jan-2026&tp=1&mc=0' \
  --fetched-at '2026-01-03T00:00:00Z' --archive-directory /private-review/evidence
```

The command archives supplied bytes without network calls. Only exact AMFI
history records may supply replacement NAVs. Conflicting scheme/date records,
incomplete parses, floats, nonfinite values and rounding are rejected. Published
decimal text is used even though the legacy parser represents NAVs as floats.
AMC documents may support identity/event context using `AMC_OFFICIAL_DOCUMENT`,
`--official-host` and `--issuer-reference`. A reviewer must independently establish
that host; these documents cannot supply replacement NAVs under the AMFI-only
policy. Partial evidence does not establish a whole series as legitimate.

Explicit case dispositions:

- `verified_legitimate`: verified evidence explains the reviewed scope; preserve data.
- `confirmed_nav_discrepancy`: AMFI supports exact differing observations; propose a correction.
- `identity_review_required`: investigate family/ISIN/plan/option separately.
- `insufficient_evidence`: retain the signal and explain what is missing.

Equal histories do not establish duplicate identities. Constant values and
terminal 10.0000 observations alone establish neither errors nor redemption.

```sh
python scripts/review_nav_remediation.py prepare-decision \
  --register-file /private-review/case-register.json --case-id CASE_SHA256 \
  --disposition insufficient_evidence --reason 'Authoritative history not reviewed' \
  --reviewer-reference DATA-02-REVIEW-001 --output /private-review/decision.json
```

Conclusive statements require archived references through repeatable
`--evidence-file` arguments. A human decision does not approve source writes or
alter eligibility. Do not fabricate evidence or clear signals to reduce counts.

## Optional durable ledger

Migration 021 adds only investigation tables and append-only triggers. If the
restore administrator's default grants give API or ingestion access to new
tables, migration 021 explicitly revokes that access from all runtime roles on
the three review tables and trigger function, and rejects remaining effective
ledger privileges. Ordinary source-ingestion default grants remain intact.
If the optional `mfdata_remediation_review` principal exists, it strips inherited roles,
elevated attributes and prior schema/table grants before granting SELECT/INSERT
on the ledger only. No source writes are granted to any principal. Runtime
provisioning must separately reject source-table ownership and unexpected
effective grants. No cloud identity, secret, workflow variable or runtime is
provisioned here. A late-created review principal needs explicit reconciliation.

With the review DSN, separate from the source read DSN:

```sh
python scripts/review_nav_remediation.py record-register --register-file /private-review/case-register.json
python scripts/review_nav_remediation.py record-decision \
  --decision-file /private-review/decision.json --archive-directory /private-review/evidence
```

Snapshots are immutable/idempotent. Opinions form a serialized linear chain;
append a new decision with `--supersedes-sha256`, never UPDATE/DELETE. Stale
reviewers must refresh; cross-snapshot supersession is rejected. Older snapshots
remain historical evidence but cannot generate current correction proposals.
No authoritative table, audit assessment, API or UI is changed.

## Exact proposals; no live apply

`propose` requires the register, case, discrepancy decision, archived AMFI
reference, and expected live version. Its locked READ ONLY transaction produces
complete before-images and exact replacements for existing scheme/date keys.
Missing stored observations are reported separately, never inserted. It never
deletes history, swaps identities or copies Growth NAVs into IDCW. The first
pilot ceiling is five schemes and 100 changed observations; larger repairs
require a later reviewed batch implementation.

```sh
python scripts/review_nav_remediation.py propose \
  --register-file /private-review/case-register.json --case-id CASE_SHA256 \
  --decision-file /private-review/discrepancy-decision.json \
  --evidence-file /private-review/amfi-reference.json --archive-directory /private-review/evidence \
  --expected-dataset-version 8 --output /private-review/proposal.json
python scripts/review_nav_remediation.py record-proposal \
  --proposal-file /private-review/proposal.json --archive-directory /private-review/evidence
```

Recording requires the current recorded discrepancy decision and matching
evidence. Artifacts say `PROPOSED_NOT_APPROVED_OR_APPLIED`. No source-write
permission follows from recording a proposal.

## Scratch rehearsal and recovery proof

Restore a backup to an independently confirmed database named
`mfdata_remediation_rehearsal_<unique_name>`, using PostgreSQL 18-compatible
restore tools. Never rename an existing database or target DEV/production.
Match the proposed dataset version. This tool does not create/delete clone
resources; their resource scope must be separately established.

```sh
python scripts/review_nav_remediation.py rehearse \
  --scratch-dsn 'host=127.0.0.1 dbname=mfdata_remediation_rehearsal_example user=postgres' \
  --confirm-scratch-database mfdata_remediation_rehearsal_example \
  --proposal-file /private-review/proposal.json --archive-directory /private-review/evidence
```

Connected name must match the confirmation and prefix. Rehearsal validates
original archived bytes, version, unique keys and exact before-images. It trials
apply, exact replay, a version increment and conditional restoration; rollback
refuses to overwrite later changes. Every exit rolls back the whole transaction,
including `--simulate-failure-after 1`; there is no successful commit path.
Success verifies original row hashes and version in a new transaction. Tests
also cover advisory-lock contention and full synthetic source-state equality.
An operational restored-backup certificate must still record backup ID, restore
status, proposal hash and execution proof; synthetic tests are not that proof.

## Deferred DATA-03 / DATA-04

Combined implementation and operational prerequisites are documented in
[NAV_CORRECTION_RUNBOOK.md](NAV_CORRECTION_RUNBOOK.md). DATA-02's scratch-only
CLI remains unchanged; the separate correction operator requires recorded,
independently reviewed finite-batch approval and operational restore evidence.

Before actual corrections: review exact proposals, test a current backup, verify
a narrowly scoped writer, implement a durable applied/rollback ledger and
incoming-refresh conflict handling, and approve a finite batch. Recheck original
values under the shared lock and recertify data/APIs afterward.
Stored computation is currently fill-if-missing; corrected NAVs need targeted
provenance-aware recomputation. Ranking SQL filters Growth while detail
methodology applies additional lifecycle/freshness/quality checks. Consistent
eligibility and adjudication effects belong to the separate API sprint. UI work
remains separate.
