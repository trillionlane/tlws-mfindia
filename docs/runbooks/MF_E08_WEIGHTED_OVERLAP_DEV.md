# MF-E08 — Observed weighted security overlap

## Finite backend result

The additive 26-pattern profile exposes a stored, reproducible weighted overlap
and concentration result for one explicitly pinned same-date pair. Existing
22-pattern factories, source portfolios, old analytics/Jaccard and strict MFData
read contracts stay unchanged. This is **observed reported-ISIN securities only**,
not full economic overlap or a diversification recommendation.

Implementation/CI, merge, image preparation, restored-clone rehearsal, DEV
deployment and live acceptance are distinct evidence levels. The sprint PR's
closeout records the exact runtime SHA/images, executions and certificate seals;
code or a green build alone is not live acceptance. No production or UI work.

## Immutable mathematical policy

Policy `reported-isin-weighted-overlap`, version 1, hash
`79d11ce237df116015d74a9e50df04855d4bffec0a0419835783c428b968b524`.
The full document is stored in `mf_review.policies` before generation.

- Two accepted complete portfolio snapshots with identical disclosure dates.
  No top-ten substitution, nearest-month matching or forward filling.
- Exact checksum-valid ISIN matches in equity, debt and government securities.
  Names do not join positions. Duplicate ISINs and conflicting asset classes are
  unsupported, not silently combined.
- Sum `min(left_weight, right_weight)` across shared eligible ISINs. Inputs and
  output are percentage points of each fund's original disclosed AUM. Do not
  rescale either portfolio to 100 or infer weights from rounded market values.
- Censored `<0.01%`, local/unknown identifiers, cash and signed receivables/payables
  remain explicit exclusions. Censored is not zero. Derivatives are not silently
  converted from margin to notional. E07's eligibility flag is never changed.
- Per-fund observed top-five/top-ten sums and observed HHI contribution
  `sum((weight/100)^2)` use the same eligible subset, without normalization.
  No eligible securities means unavailable/null concentration, not zero risk.
- Decimal precision 80, HALF_EVEN, percentage output eight places and HHI twelve.
  Aggregate unrounded contributions before final rounding. Summing rounded
  per-security display contributions may differ in their last place.
- Full economic overlap is **always unavailable/null under this policy**, even
  when an input carries E07's `overlap_eligible:true`. This policy does not
  reconcile futures, issuer look-through, cross-instrument exposure or all cash.
- The immutable result seals both snapshot IDs, policy hash and executable code
  hash, including decimal/ISIN/canonical-serialization helper code. Method changes
  generate new results; they do not replace old ones.

## Private consumer contract

Opt-in factory: `mfdataindia.review.app:create_overlap_app`.
OpenAPI: `contracts/mfdataindia-weighted-overlap-v1.json`.
Existing private service, IAM invoker and signed `review:read` backend assertion
are reused. No new API key, GitHub gate variable, public intake or browser access.
All responses are `Cache-Control: no-store`.

1. `GET /api/overlap/v1/policy` returns the installed deterministic policy.
2. `GET /api/overlap/v1/pairs/{left_code}/{right_code}` selects the current
   accepted snapshots. Reversed codes are canonicalized in ascending order.
   Optional `left_snapshot_id` and `right_snapshot_id` must be supplied together,
   bound to the route codes. These select explicit historical as-of inputs.
3. `GET /api/overlap/v1/results/{result_id}` retrieves retained historical result
   metadata. It is not a claim that those are the current or freshest portfolios.
4. `GET /api/overlap/v1/results/{result_id}/shared-holdings?limit=50&after_isin=...`
   pages the retained ISIN-sorted shared securities. Limit 1–100; pass returned
   `next_after_isin` verbatim; null means complete.

Pair responses distinguish `available` (a retained result), `not_published`
(accepted pair not generated for current engine/policy) and `inputs_unavailable`
(uncaptured, changed identity, conflicting sources or unequal dates). An available
result separately labels observed and full-economic metric availability. Do not
render a result-level available state as full overlap eligibility.

Current reads cannot reuse an older result after a newer or conflicting input.
Explicit pins retain old as-of results, but identity changes withhold even pinned
metadata/shared holdings. Wrong-fund pins or missing results are 404; one pin,
same-code pairs or malformed/beyond-bound query inputs are 422; stale historical
result identities are 409. No HTTP publication/mutation route exists.

Coverage exposes eligible counts/weight sums, each excluded source row and reason,
censored bound, derivative exclusion count and original overlap-eligibility flag.
Eligible weight sums are not a completeness score and may exceed 100 when the
original disclosure includes negative payables/publisher rounding. Shared rows
contain factual ISIN/names/class and weights, not raw source bytes or note text.

## Bounded real reference pair

ABSL Flexi Cap Regular Growth `103166` / ABSL Large Cap Regular Growth `103174`,
disclosure **2026-08-31**. Reuse E07's two retained complete portfolios; acquire no
replacement document and do not invent a newer observation.

- Left snapshot `4b6c34fd39ad239c963a40b5ba400e869b2dd017744075b1d221f5e49355964b`.
- Right snapshot `596bccb5d82e6f91630d01e57a8dad26c54d9a52fb0873cbe5a32ba86624ede4`.
- Independent original-XLS calculation: **43** shared eligible securities;
  **58.79870728** percentage points observed weighted overlap.
- Left: 82 eligible securities, 98.44517603 reported AUM percentage points.
  Right: 73 eligible securities, 99.31314255 percentage points.
- Oracle hash `5df20c9e70f5821ea03c4179fa93f8c7ba1dbda77a542fb65eb191e605616554`.
  Its separate reader verifies E07's full-row digests from the original unmodified
  ZIP/XLS and computes weights/ISIN matching independently of the production
  parser/engine. Workbook bytes are never edited, recalculated or committed.

These values are a source/engine reference, not by themselves a DEV certificate.
Both input portfolios remain ineligible for unrestricted E07 overlap. This sprint
does not certify current-month freshness, all-AMC coverage, IDCW or consumer UI.

## Preservation-first DEV drill

1. Push one bounded provider PR; adversarial self-review (not an independent
   reviewer), focused PostgreSQL tests, full suite, frozen-contract checks,
   dependency audit and exact-head CI. Merge only after findings are resolved.
2. Prepare provenance-labelled immutable app and PostgreSQL-18 tooling images for
   the merged SHA. Generic automatic DEV deployment remains disabled.
3. Read-only reconcile the actual private 22-route serving revision, IAM/runtime
   identities, enabled schedules, original portfolio pins and dataset. Bind one
   pair/hash and independent oracle/hash to the new rollout manifest.
4. Fresh regional MVCC backup of originals and **all** accepted prior sidecars.
   Restore only into a newly created isolated PostgreSQL-18 clone. Never restore
   over DEV or reuse/erase a retained clone.
5. Rehearse optional migration 030, actual restricted-role publication twice and
   exact replay, private API projections and effective privileges. Preserve every
   original/prior-layer row/structure/sequence. Allow exactly one new policy and
   one overlap result, no new sources or portfolio snapshots.
6. Install on DEV as the existing dedicated migration identity in one bounded
   transaction. Full before/after preservation captures and SHARE/advisory locks
   remain required. Installation 420 seconds; individual SQL 120; lock 5; job 600.
   Ordinary single-pair publication stays 180 seconds. Ingestion gets no access;
   generator append-only; reader/admin SELECT only; no runtime DDL or rewrites.
7. Generate/replay that exact pair as the dedicated generator. Verify clone/live
   result IDs agree. No source import, NAV update, identity change or review event.
8. Stage zero-traffic private 26-route revision. Certify old 22 patterns plus all
   four new patterns against real private HTTP and the independent source oracle,
   complete pagination, assertion/public denial, no-store, no HTTP writer/raw
   access and unchanged retained state. Verify exact image/SHA/factory/identity.
9. Promote and repeat fixed-origin checks. Actually restore known prior traffic
   **before** any candidate lookup; certify old 22 and new four returning 404.
   Restore the accepted revision, clear temporary tags and recertify. On failure,
   restore known prior traffic and fail closed. Never delete retained revisions.
10. Fresh after-backup compares all originals, prior-layer hashes and policy/event
    history. Confirm exactly one result and one policy, unchanged NAV/audit
    schedules, private ingress/invoker/roles and no temporary source-reader grants.
    Record the merged/deployed SHA, workflow and execution references, all receipt
    seals and backend handover in the sprint PR closeout. No rollout claim based
    solely on `PASS`, green CI or a successful command enqueue.

Existing backup archives, source files, result history and operational clones
remain retained. Do not enable a correction writer or destructive cleanup.
