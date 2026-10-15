# MF-E09: private reported-distribution observations

## Approved finite result and status

On 2026-10-15 the user approved Option 1 after its trade-offs were explained:
store official reported distributions as additive, versioned evidence and make
them readable to authorised backend services. Complete history is **not** a
prerequisite for this observation feature; it remains necessary for any later
method that needs complete events. Coverage stays **unknown**, and adjusted IDCW
analytics stay **ineligible**. The original source-discovery policy/reports remain
unchanged. This explicitly revises the earlier E09 publication gate, not the
E10/E11 calculation or eligibility standards.

**Status: complete for the bounded reported-observation provider DEV pilot,
2026-10-15.** [Implementation PR #65](https://github.com/trillionlane/tlws-mfindia/pull/65)
is merged; runtime `a8f60f805dd1118fe666f9e6a47e37830ee709c3` is deployed and
certified privately. [Verified closeout index](../evidence/mf-e09-dev-dist-261015a.json)
records 38 sealed gate/compatibility receipts. This accepts reported observations,
not complete distribution history or adjusted analytics. A later documentation-only
merge does not redeploy or certify a different runtime SHA.
No UI, source correction/deletion, identity rewrite, Growth-return substitution,
whole-universe claim, or production operation is included.

Initial cohort: **101763, HDFC Flexi Cap Fund, Regular IDCW**, payout ISIN
`INF179K01582`, reinvestment ISIN `INF179K01590`. Requested selection window:
**2025-04-01 through 2026-03-31**. That is a filter, not proven event coverage.
Only one reported history row is selected by the retained April 2026 factsheet;
the March notice is retained separately as a conditional announcement. The older
history rows remain in the original bytes, not deleted or corrected.

## Data and policy

- Optional schema `mf_distributions`: immutable original PDF sources plus immutable
  observation snapshots; original namespaces and published results are untouched.
- Stored immutable policy `reported-idcw-observations` v1; policy, engine/parser,
  extractor, exact identity, original hashes/pages and requested window are sealed
  in the snapshot. Identical bytes/request/code replay identically; new evidence
  or code creates a new snapshot. No historical snapshot is overwritten.
- Reported decimal tokens are INR per unit, not rates. Cum-IDCW NAV and the notice's
  earlier reference NAV are not reinvestment prices. Announcement ceilings are
  not actual investor receipts. No new financial return formula is introduced.
- Ex/payment/reinvestment dates, reinvestment NAV, scheme levy and unit-event
  coverage remain explicitly null/unknown. No NAV-drop event inference, raw NAV
  forward filling or assumed zero charges. Existing analytics eligibility is not
  changed by any new row or route.
- Distinct historical values or a historical amount above a retained notice
  ceiling cause `conflicting_evidence`. Preserve both, do not choose ingestion
  order. Numerically equal decimal strings are corroboration, not a conflict.
- Every read checks current provider identity. Identity drift withholds the body
  while retaining the record. No captured evidence gives null observed dates,
  not zero distributions. A captured empty filtered selection still has unknown
  completeness. Read inventories are bounded at 100 snapshots and fail closed on
  overflow; exact snapshot pins prevent unstable event pagination/latest selection.

The current adapter intentionally requires the three reviewed HDFC PDF source
roles/layouts. It is not an all-AMC general extractor. Source permission calling
exercises are not being reintroduced. Originals stay private; the API returns
metadata/provenance, not downloadable PDFs or extracted whole-document text.

## Backend consumer contract (no UI)

New explicit factory: `mfdataindia.review.app:create_distribution_observations_app`
(29 route patterns). The previous 26-pattern factory and all its paths/components
remain frozen. No new configuration/gate variables in GitHub are needed.

1. `GET /api/distributions/v1/policy`
2. `GET /api/distributions/v1/funds/{code}/coverage`
3. `GET /api/distributions/v1/funds/{code}/observations?snapshot_id={sha256}`

Call coverage to enumerate retained snapshot IDs/windows, then use an explicit
snapshot ID. `state: observed` means observations are readable, **not** that all
events are known. Consumers must use `coverage_state: unknown`,
`complete_event_window_proven: false` and `analytics_eligible: false` unchanged.
Conflicting assertions remain readable and explicitly marked; identity-drifted
bodies are withheld. Missing/invalid pins are rejected; another fund's pin is 404.
Unknown fund is 404, known uncaptured fund has `state: not_captured`.

Private means Google IAM/internal Cloud Run plus the existing signed backend
assertion and `review:read` authority. The Insights server is the intended caller;
the provider does not accept direct public/browser requests. Persistent records
do not depend on login sessions. Existing admin mutation routes are unchanged;
there is **no new HTTP writer**, source-download route, or customer-edit function.
Responses use repeatable-read/read-only transactions and `Cache-Control: no-store`.

Dedicated existing roles: generator can append/read; reader/admin can read
snapshots and source metadata but cannot read original PDF bytes. Inherited
ingestion/default grants are revoked on new objects only. No source DML, DDL,
rewrite or elevated role membership is added.

## Controlled operations and release gates

`deploy_distribution_observations_dev.py install|append` requires the existing
sealed rollout manifest and one hash-bound batch request. All source object
paths are fixed to `official-distribution-rollouts/{rollout_id}/{role}.pdf`, in
the existing source bucket, with immutable generation, exact size and original
SHA-256. No arbitrary provider fetch happens at runtime. Original PDFs are
decoded in the unchanged bounded worker. Old 8 MiB acquisition policies remain
unchanged; this explicit source bundle permits the reviewed 16 MiB PDF bound.

Before live writes: exact-head tests and frozen contracts; self-review; push/CI;
merge; provenance-labelled immutable images; live runtime/IAM/schedule inventory;
fresh backup; **new**, isolated restored clone; complete original/prior-layer
preservation, including the E08 overlap; actual restricted-role checks and real
source append/replay plus private HTTP on the clone.

Then install atomically under the migration owner/source-operation lock, append
the bounded source-backed snapshot under the dedicated generator, replay, stage
a private candidate with no serving traffic, verify old/new routes against an
independently transcribed original-source reference, promote explicitly, exercise
recovery to the known accepted 26-route revision, restore and recertify. Recovery
traffic restoration must not depend on candidate lookup or cleanup succeeding.
No rollback deletes appended data, original PDFs, snapshots, backups or clones.

`certify_distribution_observations_dev.py [--recovery]` verifies the three actual
HTTP routes, assertion denial, exact-pin requirement, no new writer, replay and
retained records. Its certificate explicitly does **not** certify complete
history, adjusted analytics, all AMCs, consumer UI/RBAC or production. Candidate,
serving, recovery and restored evidence must be distinct.

Closeout must retain exact merged/runtime SHA, images, source/batch/snapshot/policy
IDs, backup/rehearsal evidence, actual permissions, private/public denial,
preservation and recovery certificates. Until those are verified the roadmap
must continue to say DEV acceptance pending. Do not call fixtures or a successful
job the deployed feature.

## Accepted DEV rollout and backend handover

- Fresh regional MVCC backup and a new fully restored PG18 operational clone
  passed source/prior-layer preservation, actual restricted-role behavior,
  original-source append/replay and private HTTP checks before live writes.
- Installation, append/replay, zero-traffic candidate, promoted, prior-revision
  recovery and restored private HTTP certificates passed. Each traffic stage
  rechecked the prior enhancement APIs; no test-only mutation/scenario records
  were added to satisfy compatibility. All three new routes, unsigned assertions,
  explicit snapshot pins, rejected writer requests and public denial were checked.
- Final export preserved all **14,391 source funds and 4,240,204 NAV rows**, including
  existing analytics, review, evidence, facts, portfolios and overlap. Additions:
  one immutable policy, three original PDFs and one observation snapshot. Live
  effective capabilities passed; temporary source-reader grants were removed.
  NAV/audit schedules remain unchanged.
- Actual traffic recovery to `tlws-mf-review-dev-dev-ovlp-261015a-overlap` and
  restoration to `tlws-mf-review-dev-dev-dist-261015a-distributions` passed. Final
  traffic is 100% on the latter, with no tags, internal ingress and only the
  intended Insights backend invoker. Neither recovery nor closeout deleted data.

Backend origin: `https://tlws-mf-review-dev-kazxufb4wa-el.a.run.app`. Use the
existing internal networking, Google IAM and signed backend assertion with
`review:read`. This is provider readiness for the authorised backend, not consumer
UI/end-user RBAC acceptance. The separate core v2 service was not redeployed or
recertified by this release.

1. Read `/api/distributions/v1/policy`.
2. Read `/api/distributions/v1/funds/101763/coverage` to enumerate retained snapshots.
3. Read `/api/distributions/v1/funds/101763/observations` with the explicit
   `snapshot_id` query parameter below. Do not select an implicit latest result.

Accepted snapshot:
`36fe90cea4bb5bc622184c1f2003ef221458dc624a89233f70563e1ee066eceb`.
Stored policy:
`9f56fce56365f7b014fdf7f4e718896b50b7545fe235eabaf0384624bc97581d`.
The frozen consumer contract is
`contracts/mfdataindia-distribution-observations-v1.json` (29 patterns, three new
GET patterns; old 26-pattern enhancement profile unchanged).

The retained window has one reported history observation: **record date
2026-03-05, INR 7.0000 per unit**, from page 121 of the April 2026 HDFC factsheet.
The conditional March notice is stored separately; neither its ceiling nor the
history amount is an actual net investor receipt. Coverage stays unknown,
freshness unverified, completeness unproven and analytics ineligible. Keep these
flags unchanged; do not infer zero payouts outside the reported dates or use
cum-IDCW/reference NAV as a reinvestment price.

The original blocked discovery report is unchanged. Original PDFs, both regional
backups, execution receipts and the restored clone are retained in the operator
ledger. Initial Docker-volume initialization ran out of container-disk space;
no data was pruned. A new host-backed clone completed the full rehearsal; both
attempts remain retained. No public/browser, UI, all-AMC or production acceptance.

## Still pending separately

Complete record-date listing and bounded coverage proof; applicable reinvestment
date/NAV, scheme-level levy/unit-event evidence; E10 deterministic adjusted-series
method and independent reference cases; E11 eligibility/consumer adoption. This
observation feature neither blocks collecting evidence nor pretends those inputs
exist. No UI work is authorised by this release.
