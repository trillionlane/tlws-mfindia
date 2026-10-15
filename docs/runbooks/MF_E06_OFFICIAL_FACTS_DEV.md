# MF-E06 — verified dated official disclosures

## Finite release and boundaries

Status: implementation, not yet merged or deployed. This release completes a
bounded PDF identity/field bridge after the accepted MF-E05 metadata release.
It is not completion of all-AMC discovery or the whole fifteen-sprint goal.

Pilot: ABSL Large Cap Regular Growth 103174 and Regular IDCW 103173. Source
factsheet is September 2026 Empower, disclosure context August 31, 2026,
SHA-256 `fbc493eb04101b12eedd48a3493701eb7a90868b6696055724bfaf4fbefd75d9`.
Page 15 has explicit regular Growth/annual IDCW NAV labels, fund heading,
benchmark, manager block and regular BER. Its AUM lacks an explicit unit on
that detail page; the adapter does not assume crore. BER is not TER.

Identity support: issuer-authored Scheme Summary Document hosted at
`https://nsearchives.nseindia.com/trading_security/mf/pdf/Aditya_06042026165307_SchemeSummaryDocument.pdf`,
SHA-256 `f7be45a4810528c384eddbbd3538c1e78839bcbd32e5c9b97c587da00015d26f`,
page 28. Its ISIN and AMFI-code tables pair identical variant labels. This
document supplies identity evidence only, not newer manager/expense facts.
The exact regular Growth/IDCW suffix conventions are declared in policy;
direct options never fall back to regular. No reinvestment-ISIN certification.

Both relevant pages were extracted with the bounded existing PDF worker and
visually inspected. Original PDFs stay outside Git and HTTP. Acquisition/use
scope is operator-reported factual extraction for distributor support, not
independently verified licensing clearance or factsheet redistribution rights.

## Data and policy

New optional SQL 028 creates only `mf_official_facts.snapshots` and its sealing,
immutability and privilege boundary. Existing `mf`, evidence, policies/results,
NAVs and facts are never replaced. One new immutable facts policy is stored in
`mf_review.policies`. Source, identity, policy, extractor/engine hashes, original
page numbers, raw factual tokens, units and scope travel with every snapshot.

The entire requested batch publishes atomically under the shared source-operation
lock using a direct restricted generator. Exact replay adds no records. Changed
derivation for the same document/identity/policy conflicts rather than overwrites.
Same-date distinct source snapshots withhold the default result as conflicting;
callers can explicitly inspect a pinned retained snapshot. Changed current fund
identity withholds even pinned facts until a new valid mapping is published.

Dates are **disclosure context**, checked against the factsheet's explicit NAV
as-on date. They are not invented field-specific effective dates. Latest means
latest retained accepted disclosure, not latest market date or certified freshness.
This release does not affect lifecycle/quality gates, calculate TRI analytics,
unlock IDCW total returns, or replace original API fields.

## Private contract

Factory: `mfdataindia.review.app:create_official_facts_app` (18 route patterns).
All earlier factory/contracts remain unchanged, including evidence-only v1's
fixed `facts_available:false`. New private GET patterns:

| Route | Output |
| --- | --- |
| `/api/enrichment/v2/policy` | Immutable supported policy and hash |
| `/api/enrichment/v2/funds/{code}/facts` | Latest retained accepted snapshot or explicit not-captured/identity-changed/conflicting state |
| `/api/enrichment/v2/funds/{code}/snapshots` | Bounded keyset-paginated snapshot IDs |

`facts?snapshot_id=<hash>` pins an exact retained fund snapshot; cross-fund IDs
are 404. Pagination is a live ordered inventory, not a frozen database snapshot.
All routes require existing private invoker IAM and signed read/admin backend
assertion, and return `Cache-Control: no-store`. There is no HTTP import or raw
document endpoint. Consumer application wiring and end-user RBAC are not certified.

## Release drill

1. Reconcile exact merged code/image, current private traffic/IAM and unchanged
   six-hour NAV/audit schedules. Ordinary auto-deploy must remain disabled.
2. Fresh immutable regional backup, including prior evidence/facts baselines;
   restore into a newly created isolated operational clone, never over a retained
   database. Verify original and every prior enhancement layer against that backup.
3. Rehearse SQL 028, actual restricted logins, two-source import, deterministic
   publication/replay, API projection and immutability on the clone.
4. Apply the pinned optional installation through the migration identity;
   restricted generator appends only approved source evidence and two snapshots.
   Prior evidence records remain unchanged; identity summary remains evidence-only
   under old v1. Temporary exact-object reads must be removed even on failure.
5. Stage zero-traffic private candidate. Certify all three new route patterns,
   reviewed source-to-field expectations, pinned reads, private denial, no HTTP
   writes and all fifteen prior patterns (including retained calculators).
6. Promote verified candidate, repeat fixed-origin certification, restore the
   known prior revision and verify recovery (new paths 404, prior paths compatible),
   restore/re-certify new revision. Recovery traffic restoration precedes lookups;
   retain every revision, record and backup. No deletion as cleanup.
7. Final regional export: compare all source rows/structures/sequences and prior
   layers. Allow only the declared policy, new identity evidence and two snapshots.
   Commit evidence distinguishing local/CI, merge, deployment and real acceptance.

Commands `deploy_official_facts_dev.py install|publish` and
`certify_official_facts_dev.py [--recovery]` are fixed DEV workers. `--help` is
dormant. Selection and separately reviewed value expectations are sealed into
the exact execution environment, not new GitHub gate variables. Owner credentials
and consumer signing keys stay in existing GCP secret mounts, never on the laptop.

Acceptance needs two real variant snapshots, three supported fields per snapshot,
explicit AUM/TER unavailability, successful private checks and preservation/recovery
proof. An empty response, local fixture or green CI alone cannot close this sprint.
