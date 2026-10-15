# MF-E09B: bounded multi-fund IDCW observation expansion

## Approved result and current gate

User approved planning, implementation, review and DEV deployment on 2026-10-15.
Status: implementation and validation; **not yet merged or accepted in DEV**.
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
