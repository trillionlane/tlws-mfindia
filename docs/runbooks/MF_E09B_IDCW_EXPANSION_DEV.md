# MF-E09B: bounded multi-fund IDCW observation expansion

## Approved result and current gate

User approved planning, implementation, review and DEV deployment on 2026-10-15.
Status: **bounded provider DEV rollout accepted, 2026-10-15**. Code [PR #67](https://github.com/trillionlane/tlws-mfindia/pull/67)
merged at `532b125bcb3efc4388155a95cd26e4587109be93`; that exact SHA is deployed.
See [accepted rollout and handover](#accepted-dev-rollout-and-backend-handover)
and the [verified evidence index](../evidence/mf-e09b-dev-idcw-261015b.json).
No UI, production, source correction/deletion, identity rewrite, blanket IDCW
analytics unlock, or whole-AMC/universe claim is included.

Finite initial cohort: HDFC Flexi Cap Regular **101763** and Direct **118954**;
HDFC Large Cap Regular **102001** and Direct **119017**. Selection window:
**2025-04-01 through 2026-03-31**. Current provider identity must exactly equal
the reviewed issuer bridge at both append and read time. Each source table's
Regular and Direct columns are parsed separately; no plan substitution.

## Why an additive versioned profile

The accepted v1 adapter, schema, snapshot and three private routes deliberately
support only 101763. Changing that policy/parser would change its replay lineage.
Keep every v1 input, snapshot, hash and contract unchanged. Add a v2 policy,
optional append-only `mf_idcw` sidecar and explicit private 33-pattern factory
`mfdataindia.review.app:create_idcw_catalog_app`. All old 29 paths/components
remain frozen. No new GitHub configuration/gate variables are required.

This is an incremental observation layer, not a replacement for the base model
or a new analytics engine. The current reviewed adapter registration covers only
these four identities and the explicit HDFC history/summary layouts. More schemes
or AMCs require independently reviewed evidence/adapters, not fuzzy name matching.

## Sprint plan and acceptance

1. **Verify original evidence and fix the cohort.** Retain the April 2026 issuer
   factsheet (cover and page 121), Flexi Cap issuer summary (page 1), and Large Cap
   issuer summary (page 1), with URLs, retrieval times, hashes and exact pages.
   Verify payout/reinvestment ISINs and AMFI/plan labels against the provider.
   Independently transcribe expected per-plan rows from the rendered originals.
2. **Implement deterministic observation expansion.** Versioned adapter/policy,
   immutable sources and per-fund snapshots, complete-batch prevalidation and an
   all-or-nothing four-fund transaction, deduplicated retained originals, exact
   replay, conflict retention and current-identity drift withholding. Preserve
   recipient-class tokens separately; no invented reinvestment/levy data.
3. **Expose private reads and honest inventory.** Policy, bounded IDCW inventory,
   per-fund coverage and explicit snapshot reads. Inventory distinguishes
   `unsupported`, supported `not_captured`, `observed`, `conflicting_evidence`,
   `identity_changed` and bounded overflow. Unknown/missing evidence never means
   zero distributions. Applicable original v1 assertions participate in v2
   same-fund conflicts; v1 records and policy remain intact.
4. **Review and verify.** Parser/layout/identity/source failure tests, distinct
   plan and recipient-class reference cases, SQL-boundary overclaim rejection,
   inherited runtime/default-grant tests, all-or-nothing failure after earlier
   batch inserts, immutable originals, inventory pagination, missing/unsupported
   controls, exact-pin and cross-fund rejection, old contract equality and
   independent per-plan certification false-PASS regressions. Run focused and
   full PostgreSQL suites, audit/secret scan, all frozen contracts and image
   checks; self-review explicitly, push/PR, exact-head green CI, then merge.
5. **Preservation-first private DEV rollout.** Build immutable exact merged-SHA
   app/PG18 tooling images. Verify runtime, IAM, identities and schedules. Take a
   fresh regional MVCC backup, restore it into a **new isolated** clone and prove
   source/prior-layer preservation including all v1 observations. Rehearse real
   originals, exact append/replay, restricted direct roles and private HTTP.
   Install under owner only, append under restricted generator, replay, and
   stage a no-traffic private candidate. Certify new and prior routes against
   actual private HTTP and the independent source reference before promotion.
6. **Recover and close out.** Restore known previous traffic independently of
   candidate lookup, certify old APIs/new-route absence, restore and recertify
   the candidate. Verify live effective database capabilities and final private
   IAM/traffic; remove only temporary narrowly scoped source-reader grants.
   Final regional export must prove existing source rows and all prior layers
   unchanged, with only the declared policy, source and snapshot additions.
   Keep originals, both backups, clone and execution receipts. No deletion or
   restoring over the source database. Record exact runtime/policy/snapshot IDs,
   backend contract and limitations in closeout evidence. No readiness claim
   before these operational gates pass.

## Consumer contract (backend only)

- `GET /api/distributions/v2/policy`
- `GET /api/distributions/v2/inventory?after_code=0&limit=50`
- `GET /api/distributions/v2/funds/{code}/coverage`
- `GET /api/distributions/v2/funds/{code}/observations?snapshot_id={sha256}`

Frozen superset: `contracts/mfdataindia-idcw-catalog-v2.json`. Inventory uses
numeric keyset pagination, maximum 100 items, and no implicit latest snapshot.
`snapshot_count` counts v2 snapshots only (legacy v1 IDs are listed in fund
coverage); it is explicitly null when identity/overflow withholding hides that
inventory, never a misleading zero.
Enumerate retained snapshots through fund coverage, then pin the exact snapshot.
The inventory is the provider's IDCW population, not proof of event completeness,
active lifecycle, current-month freshness or investment eligibility.

All reads are authenticated backend-only, using existing internal Cloud Run IAM
and signed `review:read` assertions. No public/browser API, new HTTP writer,
original-PDF download endpoint or session-dependent persistence. `no-store` and
consistent read-only snapshots apply. The existing low-privilege generator can
append; reader/admin can read observation and source metadata, not original
bytes; ingestion cannot insert. Runtime roles receive no source DML/DDL rights.

## Evidence limits and next work

The retained factsheet reports one selected row per variant in this window.
HDFC Flexi Cap: record date 2026-03-05; Large Cap: 2026-02-12. This adapter captures
historical observations, not conditional notices. Existing v1 notice evidence is
preserved separately, not silently dropped or copied to another plan/fund.
Reported amounts/cum-IDCW NAV are not net receipts/reinvestment NAVs.

Every snapshot and response remains coverage **unknown** and adjusted analytics
**ineligible**. Reinvestment dates/prices, scheme levies, unit events and complete
record-date listing remain unproven. Additional evidence for the original pilot
is a distinct dependency before E10/E11; do not manufacture an adjusted series
to make this expansion pass. Next issuer batches (UTI/SBI/Kotak) follow usable
official evidence and reviewed adapters, not an all-AMC promise. No UI changes.

## Accepted DEV rollout and backend handover

All operational gates above passed. The credential-free [42-receipt index](../evidence/mf-e09b-dev-idcw-261015b.json)
has canonical hash `f0568602ea5f39aea13a8c653c41dcae25cf5a811c855da071d214683f7cf258`.
The [PR closeout](https://github.com/trillionlane/tlws-mfindia/pull/67#issuecomment-6201687955)
records the exercised gates and limitations. This documentation-only closeout
does not deploy another SHA or imply independent reviewer approval.

### Release and verification

- Exact-head [CI 38795144038](https://github.com/trillionlane/tlws-mfindia/actions/runs/38795144038):
  **1,244 passed, one controlled skip**; contracts, audit, credential scan and
  application/restore/review-tools images passed. Merged-main CI 38796708719
  and immutable image preparation 38796746751 also passed.
- Self-review found and fixed a misleading zero inventory count for withheld
  evidence. Final affected local suites: **98 passed** (34 new catalog cases).
  A full local run before that final adjustment passed 1,239 with one skip.
  The final native repeat passed 1,225 with 20 safety skips: the existing guard
  refused to erase retained synthetic funds in the reused isolated test DB.
  No wipe override was used; fresh CI exercised those cases. These are not
  independently reviewed results or claims that skipped tests executed.
- App image: `asia-south1-docker.pkg.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia@sha256:487bceb90cea4bea171e2721bb01d55cccee7c5e59fe503312677a19e7a7b6d6`.
- PG18 tooling: `asia-south1-docker.pkg.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia-review-tools@sha256:1b505faa40fb89507091fc1509f9480f7bb61895fe5b75dbcea36d8dcef3db9e`.

### Exercised operational gates

Fresh regional backup and a **new**, fully restored isolated PG18 clone proved
source/prior-layer/v1 preservation, real restricted-role behavior and original-PDF
append/replay against an independently transcribed per-plan reference. Fifteen
actual in-process private clone responses were checked before live writes.

Owner-only optional installation and restricted-generator live append/replay
produced the same four snapshot IDs as the clone. Three original PDFs were
retained. Candidate, promoted, previous-revision recovery and restored private
HTTP certificates passed for the new routes and prior API compatibility.
Compatibility used explicitly bound read-only legacy/ABSL cohorts; writer
authority remained exactly the four-HDFC batch. No synthetic source record,
test-only result or personal calculator scenario was inserted into DEV to pass.

Actual recovery restored traffic to
`tlws-mf-review-dev-dev-dist-261015a-distributions`, verified prior APIs and
new-route absence, then restored and recertified
`tlws-mf-review-dev-dev-idcw-261015b-idcw`. Final traffic is **100%** on that
33-pattern revision, with no tags, internal ingress and only the intended
`trillion-insights-runtime-dev@trillionlane-dev.iam.gserviceaccount.com` invoker.
Unsigned assertions, missing/cross-fund snapshot pins, oversized inventory and
HTTP writers were rejected; public access was denied.

Live effective privileges confirmed only the generator can append. Ingestion,
reader and admin cannot append or rewrite observations or read original PDF
bytes. Temporary source-reader grants were removed. Runtime project roles and
the six-hour NAV/audit schedules were unchanged. The final regional export
preserved **14,392 source funds, 4,244,085 NAV rows**, all prior layers and all v1
sources/snapshots. Only **one policy, three sources and four v2 snapshots** were
added, with zero source-row changes and zero review events. Original PDFs, both
backups, the restored clone and immutable receipts remain retained.

### Backend consumption and exact pins

Private service: `https://tlws-mf-review-dev-kazxufb4wa-el.a.run.app`.
Use existing internal networking, Google IAM and signed backend assertions with
scope `review:read`. No public/browser calls. The four GET patterns documented
above are live; consume the frozen 33-pattern contract
`contracts/mfdataindia-idcw-catalog-v2.json`. The old 29-pattern contract and v1
parser/policy/schema/snapshot remain frozen. The separate core v2 service was
not redeployed or recertified in this sprint; this is not a consumer UI rollout
or a v1 fallback.

| AMFI code | Variant | Explicit v2 snapshot ID |
| --- | --- | --- |
| 101763 | HDFC Flexi Cap Regular IDCW | `9dd0520525891277bda573f581861ccfbfe8e7574873a6467a321dce7ac43415` |
| 118954 | HDFC Flexi Cap Direct IDCW | `d832e2766b4a045de5212256762c00a69eb2feaa513172fa9054ca0493f95276` |
| 102001 | HDFC Large Cap Regular IDCW | `d4b3fa7740abe68c29cbf50552656bda8249590d7d016ea3bb622770eb517193` |
| 119017 | HDFC Large Cap Direct IDCW | `a3cc8d95525c244ec5214b017b08d3b1e2f0e0377d18f0d4d27c61c3a7944411` |

Stored policy: `aadacdd45f0ca5822cc558dcd1e85e21864fd2040d8657e9524f719ac7f82326`.
Engine: `3760d0b05089d1a1711f52cee023152fba1dfd84cb09bc7e43a7d9f013faa423`.
Manifest: `58adf633bb9a01490c5fc2e51de6b6ffb9b82d24d12d06ab8e7097fe5460910b`.

The selection remains FY2025–26, with one reported row per variant, **not**
proven complete event history. Coverage stays **unknown**, freshness is not
certified and adjusted analytics remain **ineligible**. No all-AMC/current-month,
consumer UI/end-user RBAC or production acceptance. E10/E11 remain dependent on
complete event, reinvestment and levy/unit-event evidence. Original blocked
discovery evidence and old results remain unchanged.
