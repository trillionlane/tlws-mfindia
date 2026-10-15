# MF-E06 — verified dated official disclosures

## Finite release and boundaries

Status: **bounded provider DEV pilot accepted, 2026-10-15**, PR #58, runtime
`074b126ba6574a2188cb6ca738dccece0bb45e41`. [Release evidence](../evidence/mf-e06-dev-fact-261015a.json).
Fresh backup and actual restored-clone rehearsal, restricted installation,
two-source publication/replay, private candidate/fixed-origin certification,
actual automatic rollback/recovery/restoration and final preservation passed.
This release completes a
bounded PDF identity/field bridge after the accepted MF-E05 metadata release.
It is not completion of all-AMC discovery or the whole fifteen-sprint goal.

Final source comparison preserved **14,391 funds and 4,240,198 NAV rows**, all
prior review/calendar/rolling artifacts and every pre-existing evidence row.
Only one policy, one identity blob/document, two mappings and two disclosure
snapshots were appended. Clone/live snapshot IDs were byte-identical. The new
18-route private profile serves 100% untagged traffic; public denial is 404.
NAV/audit six-hour schedules remain unchanged. Original fields were not replaced.
The documentation-only closeout can be newer than runtime; do not redeploy
identical code just to match that documentation SHA.

One real recovery event occurred: an initial fixed-origin evidence job completed
successfully, but its structured certificate was not visible at the first log
lookup. The gate failed closed and the handler restored the known prior revision
before any candidate lookup. Read-only reconciliation later verified the exact
completed execution and single certificate. The prior fifteen patterns/new-path
404s were then certified, followed by restoration and eighteen-pattern private
recertification. No write job was rerun or evidence gate bypassed. The local
operator used bounded read-only log reconciliation; duplicates still fail closed.
Prior compatibility covers read projections, calculators, evidence metadata and
read-scope admin denials, not new positive review/tag/admin mutation acceptance.

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

## Backend consumer handover

Use the existing private review service origin
`https://tlws-mf-review-dev-kazxufb4wa-el.a.run.app`. Cloud Run identity-token
audience is that default origin, including when calling a revision tag. The
authorized invoker remains `trillion-insights-runtime-dev@trillionlane-dev.iam.gserviceaccount.com`.
The existing signed backend assertion has issuer/caller `trillion-insights`,
audience `mfdata-verified-review-dev` and `review:read` scope. Signing material
remains consumer-owned; do not put keys/tokens in a browser or client bundle.

Contract: `contracts/mfdataindia-official-facts-v1.json`. Read each fund's facts
and snapshot IDs through its own route, rather than inferring code/ID pairing
from independent batch CLI lists. Codes 103174/103173 have three accepted fields
each, dated August 31, 2026. AUM/TER are explicitly unavailable. Check response
state and each field's status, unit, scope, date basis and source lineage; missing
is not zero, BER is not TER, and latest retained is not certified current-month
freshness. No automatic merge into original provider fields. The v1 evidence
contract remains evidence-only and can still report an unsupported PDF mapping
under its older policy, while v2 facts expose the separately verified bridge.

This is a consumable provider backend contract, not deployed Insights client
wiring, end-user RBAC, UI work, all-AMC completeness or IDCW-adjusted analytics.
