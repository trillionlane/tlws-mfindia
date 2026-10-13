# NAV-quality audit runbook (DATA-01)

Status: implementation contract. Merging the implementation does not provision,
deploy, execute, certify, schedule, or remediate data. Each DEV mutation below
is a distinct approval and evidence gate. Production is out of scope.

## Purpose and non-negotiable boundaries

The audit computes three detection signals over the complete AMFI NAV history
and stores the derived result in `mf.nav_quality_assessments`. A signal is a
review lead, not proof that a NAV is wrong.

The audit must never change `mf.nav_history`, scheme identity, fund-family
identity, lifecycle state, source metadata, or dataset summary. It does not
merge duplicate series, select a canonical scheme, suppress IDCW records,
rewrite ₹10 terminal observations, or delete anything from an authoritative
source table. All data remediation remains a later, separately approved sprint.

The runtime boundary is enforced twice:

- Cloud Run uses `tlws-mf-data-audit-dev@trillionlane-dev.iam.gserviceaccount.com`
  and only its audit DSN secret.
- PostgreSQL role `mfdata_audit` has `SELECT` on the declared audit inputs and
  DML only on `mf.nav_quality_assessments`. It cannot create roles/databases or
  schema objects, truncate tables, inherit `cloudsqlsuperuser`, or mutate a
  source table. Migration 020 removes assessment writes from `mfdata_ingest`.

## Invariants common to every operation

- Repository ID, `main` SHA, DEV project, Mumbai region, job name, immutable
  image digest, service account, one task, and zero retries must match exactly.
- The requested dataset version must be a positive integer and must still equal
  `mf.dataset_summary.dataset_version` inside the audit transaction.
- Refresh and audit take the same transaction-scoped PostgreSQL advisory lock.
  Contention fails immediately; neither operation waits and runs later against
  a different dataset.
- Audit uses a repeatable-read transaction and a 120-second database statement
  timeout. Apply commits all derived rows or none.
- Cloud Scheduler never retries a failed execution.
- Evidence is bounded JSON: exact counts plus a deterministic SHA-256 of the
  complete findings. Scheme-code lists are omitted from operational logs.

## Gate 0 — repository certification and ordinary deployment

Merge only after lint, the full PostgreSQL 18 test suite, dependency audit,
secret scan, image build, and image-content checks pass. Ordinary deployment
applies migration 020 but does not create, execute, or schedule the audit job.
Record the merged SHA and deployed image digest; later inputs must match both.

Hard stop: do not continue if the service's `DEPLOYMENT_GIT_SHA`, image digest,
or migration ledger differs from the reviewed merge.

## Gate 1 — provision the dormant boundary

Run the separately approved infrastructure provisioning step. It creates the
audit service account, built-in database user, audit DSN secret, Cloud SQL
client access, and deployer act-as grant. Cloud SQL's initial database-role
membership is removed immediately.

Dispatch `deploy-nav-audit-dev.yml` with the exact project, job, merged SHA, and
deployed digest. The workflow:

1. requires an absent or paused audit scheduler;
2. replays migration 020 under `mfdata_admin` for the late-created principal;
3. stages the audit job with the privilege verifier as its only command;
4. runs that verifier through the audit identity;
5. only after it passes, arms the unscheduled audit command and checks image,
   identity, default arguments, one task, and zero retries; and
6. deletes the one-shot job that carried the administrative DSN.

This gate does not assess or write NAV-quality rows.

## Gate 2 — exact dry-run

Read the current positive dataset version. Dispatch
`operate-nav-audit-dev.yml` in `dry-run` mode with the exact SHA, digest, and
dataset version. Leave the prior-result hash and approval reference empty.

Accept only a successful execution whose canonical JSON has:

- `status=NAV_INTEGRITY_AUDIT_DRY_RUN`;
- the requested dataset version;
- positive scheme and NAV-row counts;
- `assessments_written=0`; and
- a 64-character findings hash.

Retain the uploaded JSON artifact and its canonical result SHA-256. Any version
change, lock conflict, timeout, empty population, or malformed result stops the
run. Do not retry under the same evidence; inspect and obtain a fresh gate.

## Gate 3 — one approved apply

Obtain a fresh DATA-01 approval bound to the dry-run result. Dispatch `apply`
with the same SHA, digest, and dataset version, the exact dry-run result hash,
and that approval reference.

The operation recomputes the complete result and upserts exactly one derived
assessment row per scheme with NAV history. It also removes only obsolete rows
from the derived assessment table. Accept only
`status=NAV_INTEGRITY_AUDIT_OK` with `assessments_written=schemes_assessed`,
or `NAV_INTEGRITY_AUDIT_ALREADY_CURRENT` with zero writes when every assessment
matches the exact dataset, methodology and source revision. Both paths require
positive complete-population counts and the canonical findings hash matching
the preceding independent dry-run. A migration baseline may legitimately make
apply a no-op. A no-op is not certification: Gate 4 still performs the full
read-only source scan and exact persisted-evidence comparison.

There is no automatic repair or rerun. A process, SQL, validation, or evidence
failure rolls back the assessment transaction. Source tables remain protected
by database privileges even if application logic is defective.

## Gate 4 — read-only certification

Dispatch `certify` with the apply result hash and the same exact identifiers.
Certification performs a fresh full scan, writes zero rows, and compares every
persisted assessment field: methodology and dataset versions, observation and
distinct counts, date bounds, sorted signals, and complete evidence.

Accept only `status=NAV_INTEGRITY_AUDIT_CERTIFIED`, zero writes, the expected
dataset version, and a successful Cloud Run execution. Retain the canonical
certification result and SHA-256. A newer refresh invalidates this certificate;
repeat dry-run, approval, apply, and certification for that dataset rather than
pretending the older certificate covers it.

## Gate 5 — schedule activation

Schedule activation is separate from deployment and certification. Dispatch
`enable-nav-audit-schedule-dev.yml` with the exact certified SHA, image digest,
dataset version, certification-result hash, and literal schedule
`10 */6 * * *`.

The workflow independently locates the supplied certificate among recent
successful audit executions, verifies that its execution snapshot used the
exact immutable image digest and `DEPLOYMENT_GIT_SHA`, and re-hashes its
canonical JSON before creating or updating the scheduler. It then uses the audit
identity and shared refresh/audit lock for a read-only probe of the live
`mf.dataset_summary.dataset_version`;
activation fails if that version has advanced beyond the certificate. The
schedule uses `Asia/Kolkata`, invokes the audit at minute 10 after the refresh at
minute 0, and has zero retry attempts. It may be activated only while the job's
default remains the approved apply command.

## Failure, pause, and recovery

On any unexplained scheduled failure, use
`disable-nav-audit-schedule-dev.yml` to pause only the audit scheduler. Do not
delete the job, evidence, assessments, or source data. Existing assessments can
remain available: the API already marks them stale when their dataset version
lags the current dataset.

Diagnose from the exact Cloud Run execution and its bounded result. If a refresh
held the shared lock, leave the failed audit as evidence and wait for a newly
approved/manual or next scheduled execution; zero retries prevent surprise
duplicate runs. If certification differs, keep the schedule paused and inspect
the derived rows and code. Never correct source data as part of recovery.

Reactivation requires a current exact certificate and the normal activation
gate. An ordinary application deployment also requires the audit scheduler to
be absent or paused before replacing the audit image; the updated job remains
dormant until recertified and reactivated.
