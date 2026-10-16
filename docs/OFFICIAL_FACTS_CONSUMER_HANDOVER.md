# Official facts process: backend consumer handover

## Scope and truth boundary

This is the finite FS-01–FS-05 process pilot, not an all-AMC data-completeness
claim. It covers one declared fund name, two exact Regular variants (ABSL
Large Cap Growth 103174 and Annual IDCW 103173), two original reporting periods
(2026-08-31 and 2026-09-30), and four immutable snapshots. Do not call those
four snapshots four funds. No UI or production rollout is included.

Implementation, CI, merge, image build, schema installation, source acquisition,
traffic activation, actual scheduled execution and certification are distinct
states. The rollout's sealed acceptance artifact and immutable Cloud Run
execution evidence establish DEV readiness, not this document by itself.

## Consume the existing current API first

The existing private core origin and 19 read routes remain unchanged. Its
current facts view can advance five supported scalar facts from complete,
identity-bound official monthly publications: benchmark, managers, Regular
base expense ratio, explicit scheme risk and explicit-unit fund AUM.

The current view is an overlay. No original NAV, fund, imported facts, source
identity, calculated analytics or historical disclosure is rewritten. Selection
is by the latest usable disclosure date per field, not fetch time. Missing or
conflicting later assertions retain the last usable value with dated warnings.
Consumers must not describe a retained older value as freshly disclosed.

BER is not TER. Fund AUM is not a plan AUM. Fund inception is not necessarily
plan inception. Publisher-reported ratios/returns are not our calculated
analytics. The unadjusted IDCW variant remains comparison-ineligible;
distribution-adjusted analytics remain disabled pending complete evidence.

## Private provenance, reporting and review APIs

The existing `tlws-mf-review-dev` service stays internal and IAM protected,
with the existing Insights runtime invoker. Use its configured private origin
and service-origin audience; never expose signing keys or credentials to a
browser. Requests also require the existing signed backend assertion.

| Contract | Purpose | Consumer requirement |
| --- | --- | --- |
| v3 policy and `/api/enrichment/v3/funds/{code}/facts` | Current five fields, source dates/IDs, availability, freshness, verification and revision | Respect each field's state/warnings; publication is not human verification |
| v4 policy and `/api/enrichment/v4/funds/{code}/facts` | Immutable 32-slot monthly reporting snapshot, optionally pinned by `snapshot_id` | Distinguish accepted/unavailable/unparsed fields; retain units/scope and publisher-reporting labels |
| v5 policy and `/api/enrichment/v5/status` | Acquisition outcomes, latest actual source check and run receipts | Owner bootstrap is not a fresh source fetch; failed check must not erase the last success |
| v6 `/api/enrichment/v6/funds/{code}/history` | Bounded retained snapshots and publication status | Follow declared immutable-ID cursor order; it is not recency order |
| v6 `/api/enrichment/v6/funds/{code}/changes` | Compare explicit `from_snapshot_id` and `to_snapshot_id` | Dates advancing alone are not a risk/value change |
| v6 `/api/enrichment/v6/funds/{code}/verification` | Review version, dependency hash, per-field status and events | Read immediately before a review and display stale review separately |
| v6 `/api/enrichment/v6/funds/{code}/reviews` | Evidence-bound human attestation | Signed `review:admin`, stable Idempotency-Key, exact version/dependencies/evidence and real human actor |

The 43-route OpenAPI contract preserves all earlier private paths and schemas.
The refresh receipt's `field_gaps` covers only the five supported current
scalar fields. An empty list is not whole-factsheet completeness: consult the
v4 per-field statuses and process inventory for all 32 reporting slots.
See `contracts/mfdataindia-fact-verification-v1.json` and
[the operational runbook](OFFICIAL_MONTHLY_FACTS_RUNBOOK.md) for exact shapes.
All responses are no-store. This release does not alter Insights or grant new
invokers. Consumer wiring and any future admin UI are separate work.

Review events never edit source values or resolve source conflicts. A review
of another field cannot renew an old review. New dependency evidence requires
revalidation even when the displayed value is unchanged. Exact idempotent
replay returns the original receipt, not renewed verification. No automated
source job may impersonate a human or mark a live review verified.

## Operations and acceptance

The checked-in `config/official-facts-dev.json` bounds the source job, cohort,
identity document and archive prefix. The dedicated existing generator can
create/read originals only under that private prefix; no overwrite/delete
privilege is added. The scheduler calls the fixed Cloud Run job through the
existing dedicated scheduler identity. Final daily timing is 09:00 Asia/Kolkata
(03:30 UTC). Existing six-hour NAV refresh/audit schedules are unchanged.

The FS-05 inventory tool produces actual counts, dates, accepted/unavailable
field reasons, current supported coverage, policy hashes, retained layer seals
and human-review state. It explicitly cannot certify cloud IAM, private HTTP,
scheduling or recovery from a database inventory alone. The companion core
certifier exercises all 19 real private read templates, Growth-first detail/
returns/analytics, IDCW exclusion and an actual explicitly unassessed
`signals: null` sample. Omitted signals and empty-array substitutions fail.

The restored-clone helper accepts only an explicitly guarded local scratch
target, exact backup bytes and the narrowly reproduced PostgreSQL UNION-alias
roundtrip. It replays pinned original DDL, never changes stored seals to accept
drift. Semantic differences fail. Full source and retained-layer comparison,
private HTTP and actual Cloud Run recovery are additional mandatory gates.

On failure restore known prior traffic independently of candidate lookups;
restore the captured current graph/configuration in a separate bounded step.
Pause only the new owned source schedule if necessary. Retain all originals,
receipts, backups and clones. Never restore over the live source database.

## Broader rollout prerequisites

Reuse this process, not an unbounded generic scraper. For each additional AMC/
layout/cohort register official discovery hosts and paths, exact issuer-to-AMFI
identity evidence, bounded decoder/parser policy, two original-period reference
oracles, per-field units/scope and unavailable reasons. Require atomic complete
cohort publication, immutable create-only archival, permission denials, stale/
conflict/replay tests, restored-clone preservation, private API checks, actual
scheduled acquisition and recovery before expanding authority.

Retain unparsed sections for later adapters rather than inventing values.
Full reported portfolios use their own source contracts; top ten holdings
are not full holdings or full economic overlap. No future expansion may infer
distribution/reinvestment completeness, synthesize missing NAVs, or replace
canonical calculations with publisher tables without a separately versioned
deterministic mathematical policy and approved analytics layer.
