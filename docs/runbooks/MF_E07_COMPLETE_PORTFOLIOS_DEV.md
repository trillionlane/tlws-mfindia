# MF-E07 — Complete official portfolio pilot

## Finite result and plan

Implementation in progress. This document is not a DEV acceptance certificate.
Deliver two distinct ABSL fund-level portfolios for August 31, 2026, linked
to existing Regular Growth identifiers: Large Cap 103174 / INF209K01BR9
and Flexi Cap 103166 / INF209K01AJ8. Not all-AMC coverage, current-month
freshness, overlap analytics, adjusted IDCW analytics or consumer UI wiring.

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
