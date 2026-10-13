# DATA-03/04 — governed correction and consistent private reads

One provider PR combines finite NAV correction, conditional rollback, dependent
calculation publication and API consistency. No UI, identity merge, bulk history
deletion or automatic repair. The existing strict v2 and association contract
files do not change. Deployment, a real restored-backup rehearsal, approval of
an exact operational batch and external Insights recertification are separate
operational outcomes, not consequences of tests or a merge.

## Preconditions and boundaries

The DATA-02 priority investigation found zero differences in 38 sampled values.
There is **no justified operational correction batch yet**. Suspicious patterns
are not authority to invent replacement values or copy Growth into IDCW.
Continue independent case adjudication; preserve the original evidence.

Only original archived AMFI history bytes can supply replacement NAVs. A current
reviewed discrepancy decision and proposal must already exist in the append-only
DATA-02 ledger. The initial source-write ceiling remains five schemes and 100
existing observations. Never insert missing observations as remediation.

Approval requires a separately verified operational backup restore and the
DATA-02 scratch rehearsal. Hashes bind artifacts; they do not authenticate an
operator's claim that a backup was restored or a human approved a batch. The
restricted reviewer must independently inspect restore/job evidence and approve
the exact proposal. Synthetic tests are explicitly not operational restore proof.

The application must be deployed with the strict projections and recertified
before a real correction. Check actual serving image/SHA, private ingress/IAM,
all 19 reads, the existing association writer and the refreshed audit image.
Ordinary DEV deployment disables the existing association writer: follow its
existing approved reactivation/certification procedure, not a new gate variable.
Do not silently replace a scheduled audit image without its pause/deploy/certify
procedure. There is no correction scheduler, public write endpoint, new GitHub
environment variable or automatically provisioned correction identity.

## Data model and API rules

Migration 022 adds revision counters, calculation sidecars, approval/operation
ledgers, protected observation heads and append-only ingestion conflicts.
An owner-only immutable commit-obligation ledger and deferred constraint trigger
prevent even a direct writer call to `correct_nav_batch()` from committing an
incomplete correction. The same transaction must publish exact current
calculations, advance the dataset version once, reassess the complete population,
and insert a matching transaction-bound receipt. Apply and rollback are both
covered; ordinary ingestion does not create correction obligations.
Approvals, operation receipts and conflicts cannot be updated/deleted/truncated.
Original published `fund_facts` and the legacy risk profile are retained.

NAV INSERT/UPDATE/DELETE, inception edits and identity/family changes invalidate
affected source dependencies under the existing shared operation lock. Duplicate
counterparts are invalidated conservatively. A counter is not just `nav_last`:
an edit five years back is detected even when the latest date does not change.
Each new case and proposal seals `reviewed_source_revisions`, an exact map for
all case members. Proposal construction, scratch rehearsal and both SQL/Python
apply checks compare against these original reviewed revisions, not merely the
latest reassessment. Reassessment cannot renew an old review after historical
or counterpart drift. Historical artifacts without this binding remain evidence
but cannot become executable proposals; export and review a fresh case.
Privileged administrative bypasses (DDL, disabling triggers or direct TRUNCATE)
are not supported data operations. Runtime source mutation must use the guarded
ingestion/correction paths; do not grant runtime TRUNCATE or DDL authority beyond
the existing ingestion staging capability.

`current_fund_facts` serves our version-bound NAV metrics, not unversioned legacy
returns/risk values. Missing/stale computations are NULL in the existing v2
nullable fields. Beta remains NULL because benchmark-based current provenance
has not been established; its original published value is not destroyed.
`current_risk_profile` serves only the matching revision/methodology.

Raw NAV/returns/analytics remain directly readable with their methodology and
quality context. Rankings use one eligibility rule: Growth, current full-feed
presence, no redeemed/defunct evidence, current NAV freshness and a current,
supported, signal-free assessment. Unknown/stale assessments cannot establish
comparison readiness. Every API operation reads one repeatable-read READ ONLY
snapshot and returns `Cache-Control: no-store`. No new v2 enum or required field.
Consumer cache/runtime behavior still requires post-deployment certification.

## Rollout baseline and normal ingestion

`scripts/migrate.py` applies 022 and seeds dirty/missing calculations and quality
assessments before the existing deployment workflow promotes the read service.
The baseline is one locked transaction and never edits authoritative NAVs or
overwrites published facts. A failed baseline aborts deployment; re-running the
migrator retries dirty data even if migration 022 itself already committed.
The existing job timeout remains ten minutes. Measure this against a restored
operational-sized clone before live rollout; synthetic tests prove correctness,
not whole-DEV latency. Do not increase timeouts or promote past a failed baseline.

Ordinary `upsert_nav` quarantines protected conflicts, then recomputes dirty
funds/counterparts in the same transaction as accepted NAVs. The load report
reports `quarantined` separately from `unchanged`. Existing source fetch metadata
and incoming conflict values remain evidence for review. A matching incoming
value cannot rewrite protected provenance. New dates are accepted normally.
Conflicts are unresolved until independently reviewed, not silently ignored or
automatically accepted. An active correction cannot be replaced by another
unreviewed operation. Use conditional rollback if appropriate, then obtain a
fresh case/proposal/version approval; do not manually delete protection heads.

The old metrics job retains legacy fill-only storage behavior but always also
publishes current sidecars. `refresh_risk_profile.py` now uses the same publisher
and fixed 252-NAV profile methodology. It no longer advances the authoritative
dataset version for a derived-only refresh.

## Principals and artifact shapes

Optional writer `mfdata_nav_correction_writer` is **not created by this PR**.
After explicit provisioning, the migration administrator runs
`scripts/sql/reconcile_nav_correction_privileges.sql`. It removes memberships,
elevated attributes, table/column grants, MF ownership/CREATE and broad function
access. Source NAV DML is not granted. Only the owner-executed whole-batch function
is exposed to the writer; its row primitive is private. Derived-data writes and
immutable operation receipt INSERT are allowed. Runtime checks reject direct
source privileges and access to the row primitive. Verify actual Cloud SQL
effective permissions separately; PostgreSQL fixtures are not Cloud SQL proof.

`mfdata_remediation_review` appends reviewed approvals but cannot update source.
Ordinary API, ingestion, association and audit identities cannot approve/apply
corrections. Restore/default grant reconciliation preserves that isolation.
The auditor needs SELECT on `nav_source_revisions`, its seventh declared read
table; its only write table remains `nav_quality_assessments`.

Sealed approval (`sha256` is canonical-content hash):

```json
{
  "format": "mfdata-nav-correction-approval-v1",
  "status": "APPROVED_FINITE_BATCH",
  "proposal_sha256": "<exact reviewed proposal hash>",
  "restore_proof_sha256": "<independently verified restore proof hash>",
  "approver_reference": "<human approval record>",
  "reason": "<reviewed reason and apply/conditional rollback scope>",
  "sha256": "<canonical content hash>"
}
```

Restore proof: sealed `mfdata-nav-restore-proof-v1`, status
`RESTORE_AND_REHEARSAL_VERIFIED`, backup ID, uniquely named restored scratch
database, proposal hash, expected dataset version, independent verifier reference
and the sealed successful DATA-02 rehearsal. Rehearsal must have equal baseline/
restored hashes and true exact-replay/conditional-restoration checks.
Archive backup/restore execution evidence alongside it; do not issue this
certificate from unit test outputs or self-attest an unperformed restore.

## Operator commands

Use the appropriate identity's DSN through the existing DSN mechanism; never
retain it in artifacts. `--confirm-database` must match the connected target.

```sh
python scripts/correct_nav_data.py approve --confirm-database mfdataindia \
  --proposal-file proposal.json --approval-file approval.json \
  --restore-proof-file restore-proof.json --archive-directory evidence
python scripts/correct_nav_data.py apply --confirm-database mfdataindia \
  --proposal-file proposal.json --archive-directory evidence \
  --approval-sha256 EXACT_APPROVAL_HASH --expected-dataset-version CURRENT_VERSION \
  --receipt-output applied-receipt.json
python scripts/correct_nav_data.py rollback --confirm-database mfdataindia \
  --proposal-file proposal.json --archive-directory evidence \
  --approval-sha256 EXACT_APPROVAL_HASH --expected-dataset-version CURRENT_VERSION \
  --receipt-output rollback-receipt.json
```

Apply checks current dependencies and identity, reviewed approval, exact images,
decision freshness and the shared lock. Source updates, affected calculations,
dataset version advancement, full quality reassessment and receipt are one
transaction. Any failure rolls everything back. No result is emitted before
commit. A receipt-file export failure after commit is recovered by exact replay
of the durable receipt, not a second apply or an automatic reversal.

Rollback conditionally restores only the exact applied target images and owned
protection heads. Later observations at other keys stay intact; calculations
are recomputed from that complete latest series. A changed target/ownership
rejects the entire rollback, including earlier observations in the batch.
Version numbers remain monotonic. Rolled-back proposals cannot be reapplied;
obtain a new review/version. External Insights certification is always separately
recorded: operation receipts explicitly do not claim consumer certification.

## Acceptance evidence

Tests use fresh named scratch databases and dedicated non-elevated roles.
Required proofs include multi-observation apply/replay/rollback, all 19 private
read routes, source-vs-derived agreement, unchanged published/unrelated facts,
failures after source/calculation/audit/receipt stages, concurrent snapshots,
stale proposals, superseded decisions, protected conflicts, later target changes,
direct SQL commits without full publication, receipt/transaction mismatches,
and reassessment after non-target historical or counterpart changes,
later other-date preservation, restored default grants and per-source audit
skip rejection. Lint, both OpenAPI pins, dependency audit and non-root production
image/CLI checks must pass. UI/admin editing remains a separate future sprint.
