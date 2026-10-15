# MF-E07 — Complete official portfolio pilot

## Finite result and acceptance

Status: **bounded provider DEV pilot accepted, 2026-10-15**. Feature PR #60
and operational repair #61; certified runtime
`9e4d410c9da0c21902422c1a2302bd7d2291ab59`.
[Acceptance evidence](../evidence/mf-e07-dev-port-261015b.json) verifies 32
outer receipt seals and their nested producer certificates. Fresh regional
backup, actual restored-clone rehearsal, restricted installation, original
source imports, deterministic publication/replay, private candidate/fixed-origin
checks, actual old-revision recovery/new-revision restoration and final regional
preservation passed. The full 22-pattern profile serves 100% untagged traffic.

All **14,391 original funds and 4,240,204 NAV rows**, original structures and
sequences, prior review/calendar/rolling results, official evidence rows and
dated facts were preserved. Exactly one immutable policy, two source records
and two portfolio snapshots were added. No review events, original field
replacements, NAV corrections, UI or production changes. The six-hour NAV and
audit schedules remain enabled and unchanged. Live effective-privilege checks
also verified that ingestion cannot append to these tables, API roles cannot
read original bytes, and runtime roles cannot rewrite retained portfolios.

The first DEV installation at runtime build `b489d08` hit its 180-second
transaction deadline before acceptance. No import, publication or traffic change
followed. Read-only reconciliation verified rollback before a new attempt.
The installation-specific transaction budget
is 420 seconds, below the existing 600-second job deadline; each statement remains
bounded to 120 seconds and lock acquisition to five seconds. Source append and
publication keep their original 180-second transaction bound. No preservation,
privilege, source binding, atomicity or recovery gate is removed.
Read-only live reconciliation verified that `mf_portfolios` is absent and all
original source/prior-layer hashes still equal the fresh backup. Two complete
source captures plus existing capability checks took 164.7 seconds before the
new schema installation/verification overhead, explaining the insufficient
180-second installation budget. This measurement did not append any records.
The repaired live installation completed in about 188 seconds. The new attempt
used renewed SHA-bound images, a fresh backup and a different restored clone;
the failed attempt and first clone remain retained.

One initial candidate read-only compatibility probe included the new ABSL
portfolio codes in the legacy analytics cohort. Those funds have no previously
published legacy analytics result, so it correctly failed `NotFound`. The gate
restored the known prior traffic and cleared the candidate tag. A read-only
clone probe reproduced the cause; the original four-fund legacy certification
cohort was restored, while the two-fund full-row portfolio oracle stayed
unchanged. No analytics were generated to satisfy this probe. All subsequent
candidate/fixed/recovery/restored gates passed. This was an operator input
repair, not another runtime or source-data change.

Delivered: two distinct ABSL fund-level portfolios for August 31, 2026, linked
to existing Regular Growth identifiers: Large Cap 103174 / INF209K01BR9
and Flexi Cap 103166 / INF209K01AJ8. Not all-AMC coverage, current-month
freshness, overlap analytics, adjusted IDCW analytics or consumer UI wiring.

## Accepted release drill

1. Acquire the issuer's full monthly disclosure archive, preserve original
   bytes and listing URL, and verify exact issuer code/ISIN identities.
2. Implement deterministic bounded decoding, constituent/section/AUM
   reconciliation, signed cash/liabilities, explicit non-ISIN identifiers and
   separate derivative-note positions. Retain inconsistent originals unaccepted.
3. Add optional restricted append-only source/snapshot tables and four private
   read patterns. No source-table, prior-facts or old endpoint replacement.
4. Run focused and full PostgreSQL tests, frozen contracts, audit/image checks;
   push one feature PR, perform a separate adversarial self-review, repair,
   require exact-head green CI and merge under the existing approval.
5. Fresh regional MVCC backup and new restored operational clone; rehearse
   restricted install, original imports, bounded publication and exact replay.
6. Install/publish in DEV, stage a zero-traffic private 22-pattern profile,
   certify source-to-API full pagination plus all eighteen prior patterns,
   promote/recheck, restore old traffic/recheck new-path absence, restore new
   traffic/recertify. On failure restore known prior traffic before lookups.
7. Final regional backup verifies every original row/structure/sequence and
   prior enhancement layer. Append only one policy, two source records and two
   portfolio snapshots. Retain all archives, clones and revisions. Record real
   evidence and private consumer handover before declaring completion.

## Official evidence and semantics

Official discovery: https://mutualfund.adityabirlacapital.com/forms-and-downloads/portfolio
The page's public monthly accordion API lists the August archive under a retired
`abcscprod.azureedge.net` hostname. The exact same media path was retrieved from
the issuer's own HTTPS origin, not through an authentication workaround:
https://mutualfund.adityabirlacapital.com/-/media/bsl/files/resources/monthly-portfolio/2026/monthly-portfolio-31082026_abslmf.zip
Archive SHA-256: `30a2b953a6d518b3653ff7b5f08f8ffa48591c0841d9abc5572b784b91f5f47e`.
One original `31082026_ABSLMF_MonthlyDisclosure.xls`; selected sheets
`BSLFEF` and `BSLEQTY`. Original bytes remain private, outside Git/HTTP.

Issuer-authored scheme summary hosted at
https://nsearchives.nseindia.com/trading_security/mf/pdf/Aditya_06042026165307_SchemeSummaryDocument.pdf
SHA-256 `f7be45a4810528c384eddbbd3538c1e78839bcbd32e5c9b97c587da00015d26f`,
pages 28 and 25 respectively. Summary supplies identity only, not newer holdings.
Reported user authority covers factual extraction for distributor support, not
factsheet/narrative redistribution or independently certified legal permission.

The decoded original has 77 Large Cap and 89 Flexi Cap constituent rows.
Reported AUM totals are INR lakh 2,971,081.71 and 2,904,424.43 respectively.
Every section and grand total must reconcile with a policy-bound displayed-value
rounding allowance, not a blanket 95–105% tolerance or rescaling to 100%.
Underlying XLS numeric tokens are retained as shortest-roundtrip strings; all
subsequent arithmetic is Decimal with immutable precision/rounding policy.
Publisher-cached values are read, never recalculated or macros executed.
Raw note text is retained privately in the immutable snapshot for audit, not
returned in API metadata. APIs expose only the supported typed factual fields.

`$0.00%` means less than 0.01%, not zero. Non-ISIN issuer-local identifiers
remain explicit, including zero-valued unlisted legacy positions. Signed net
payables are not clamped. Derivative collateral is not notional exposure and
separate futures notes do not get summed into AUM holdings. Large Cap discloses
a non-hedging long future; Flexi Cap has a local unlisted identifier. Therefore
neither pilot is automatically eligible for unrestricted full-security overlap.
That eligibility requires MF-E08 policy, not relabeling them as pure equities.

## Storage and private API

Optional SQL 029 adds only `mf_portfolios.sources` and `snapshots` with source,
identity and publication seals and append-only triggers. Inherited ingest/default
privileges are explicitly revoked. Only the existing restricted generator can
append; existing restricted readers can read metadata/snapshots, not source bytes.
No HTTP ingestion, source document, correction or administrator mutation route.
Malformed/unsupported documents can remain archived without accepted snapshots.

Explicit factory `mfdataindia.review.app:create_portfolios_app`, 22 patterns.
Old factories/contracts remain unchanged. Existing internal ingress, invoker IAM,
signed backend assertions and `review:read`/`review:admin` scope controls remain.
No new GitHub environment flags or consumer credentials.

- `GET /api/portfolios/v1/policy`
- `GET /api/portfolios/v1/funds/{code}/portfolio`: metadata/coverage state;
  optional exact `snapshot_id`. Latest retained is not certified freshness.
- `GET /api/portfolios/v1/funds/{code}/snapshots`: bounded live keyset inventory.
- `GET /api/portfolios/v1/funds/{code}/snapshots/{snapshot_id}/holdings`:
  pinned, bounded pagination by `after_row`, preserving source row order.

All responses are `Cache-Control: no-store`. A missing snapshot is not empty
holdings or zero AUM. Same-date distinct retained sources withhold current
selection as conflicting; pinned originals remain inspectable. Changed current
identity withholds even pinned portfolios and rejects holdings pagination.

## Certification gate

Separate reviewed source expectations bind every constituent's source row,
name, original identifier and displayed market value, plus count, total,
derivative count, archive/identity hashes and as-of date. Full pinned pagination
must reproduce the entire accepted list without loss/duplication. Empty
responses, fixture success or green CI cannot close the sprint. Verify all
prior private read/calculator patterns as well; no new positive admin/tag-write
or consumer end-user RBAC certification is implied.

## Backend consumer handover

Use the existing private origin
`https://tlws-mf-review-dev-kazxufb4wa-el.a.run.app` from the authorized
`trillion-insights-runtime-dev@trillionlane-dev.iam.gserviceaccount.com` workload.
Cloud Run ID-token audience is that default origin. The separate signed backend
assertion keeps issuer/caller `trillion-insights`, audience
`mfdata-verified-review-dev` and `review:read` scope. Existing consumer signing
material stays in its secret store, never in a browser, source repository or
client bundle. Internal ingress and invoker IAM remain required.

Contract: `contracts/mfdataindia-complete-portfolios-v1.json`. This new namespace
is independent of MFData's existing strict v2 read contract; it does not restore
temporary v1 fallback or change the original nineteen-read service.

1. Read `/api/portfolios/v1/policy` and the selected fund's `/portfolio` metadata.
   Require `state: available`, inspect as-of date, scope, policy, source hashes,
   `statement_complete`, `overlap_eligible` and limitation fields. Missing is not
   an empty/zero portfolio. Latest retained does not certify current-month freshness.
2. Pin that exact snapshot ID and follow `/snapshots/{snapshot_id}/holdings`
   using `limit` (1–100) and returned `next_after_row` as `after_row`. Stop only
   when the next cursor is null. A pinned immutable snapshot prevents mixed-month
   pages; changed current identity rejects pagination until revalidation.
3. Use `/snapshots` for bounded retained history. It is a live keyset inventory,
   not a frozen enumeration. Cross-fund snapshot IDs are 404.

Accepted per-fund IDs (explicit code pairing, not positional batch-list inference):

| Code | Fund | Holdings | Snapshot ID |
| --- | --- | --- | --- |
| 103166 | ABSL Flexi Cap Regular Growth | 89 | `4b6c34fd39ad239c963a40b5ba400e869b2dd017744075b1d221f5e49355964b` |
| 103174 | ABSL Large Cap Regular Growth | 77 | `596bccb5d82e6f91630d01e57a8dad26c54d9a52fb0873cbe5a32ba86624ede4` |

Both disclose August 31, 2026 fund-shared portfolios; no automatic sibling or
direct-plan identity fallback. Market values and grand totals explicitly use
INR lakh decimal strings. Published percentage fractions are converted under the
stored policy, not rescaled to 100. Censored weights have null values and an
exclusive upper bound, not zero. Keep signed payables and zero-valued holdings.
Issuer-local identifiers are not certified ISINs. Derivative collateral is not
notional exposure; separate positions with missing contract identity/quantity
retain explicit nulls. Neither real pilot is automatically eligible for
unrestricted full-security overlap. MF-E08 must define that separate policy.

This handover certifies provider backend availability/source fidelity and
preservation, not deployed Insights client wiring, end-user RBAC, all-AMC
coverage, UI, adjusted IDCW metrics, or production. Original facts/holdings/NAVs
are not automatically replaced with these sidecars. A later documentation-only
merge may be newer than runtime; do not redeploy identical runtime merely to
match that documentation SHA.
