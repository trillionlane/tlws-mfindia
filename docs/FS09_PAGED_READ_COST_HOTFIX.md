# FS-09 — Paged and peer read-cost safeguard

The actual active-SBI DEV observation on 2026-10-17 returned correct `no-store`
responses but measured 11.205 seconds for a two-fund name page, 12.725 seconds
for a two-family name page and 5.617 seconds for risk/reward. Before SBI
activation the corresponding API-only certificate measured approximately
1.5/1.6/2.6 seconds. Source correctness alone is not a useful API readiness
claim when an expansion substantially increases small-request costs.

## Narrow implementation

- Keep the fund list's existing identity/sort pagination and restrict its
  outer current-facts lookup to scalar page codes with `LATERAL ... OFFSET 0`.
- For a family name sort (including the existing unknown-sort fallback), pick
  the representative and complete variant count under the existing scoped
  filters, then page identities before current facts/latest NAV. AUM, expense,
  return and NAV sorts keep their full-cohort ordering before pagination.
- For risk/reward, materialize the same current-category, served-plan,
  lifecycle/freshness/quality-eligible peer cohort. Fetch AUM only for those
  peers with scalar equality. Do not substitute stale raw facts for the current
  category and do not change any return/risk formula, ordering or eligibility.

No cache, schema, policy, source identity, original data, timeout, IAM, UI,
public contract or environment-gate changes. The scalar-bounded batch and
sibling safeguards from #88 remain intact. All SQL identifiers come from
fixed code; request values remain bound parameters.

## Actual-data proof, not deployment acceptance

Read-only private DEV comparisons ran against the unchanged active SBI policy
`151093a8798bb41803315b73bca944bb292adc661701173225173958d3b17302`.
The original and proposed calls used one repeatable-read/read-only snapshot.
No source rows, graph or live application image were changed by the proof.

Four complete list responses matched exactly, including counts, order,
representatives, values, nulls and dates: global first pages and SBI-filtered
second pages. Old calls took 10.815–12.222 seconds; proposed calls took
0.118–0.174 seconds. Executed as
`mf-sbi-paged-read-proof-261017a-ll9px`. The proof binds the two changed function
bodies before the later risk-only change; it is not an immutable-image claim.

Complete **ordered** risk responses for 103174, 103215, 100669 and missing code
999999999 also matched. The populated maps retained 30/36/36 points. Old calls
took 5.527–5.672 seconds; proposed calls took 0.674–0.765 seconds. The missing
code remained empty. Execution `mf-sbi-risk-read-proof-261017a-42b4b` binds the
final source and risk-fragment hashes. These timings are direct read-only
query comparisons, not end-to-end deployed HTTP measurements.

## Validation and delivery

SQL-scope tests preserve count isolation, bound values, name/fact/NAV sort
placement, representative choice, zero/null/date serialization and the exact
comparison-eligibility predicate. PostgreSQL regressions exercise multiple
families, representative-versus-sibling facts, every sort, later/empty pages,
missing facts and an IDCW-only representative filter. They use synthetic
identities inside the existing rollback-only fixture, never live source rows.

Require full exact-head CI and normal merge, full main CI, immutable image
verification and a private API-only candidate rollout **after** SBI source
preservation closeout. Certify all 19 core routes and the retained detail/batch
samples. Additionally require real private fund-list, family-list and
risk/reward samples below five seconds on candidate and serving revisions;
keep existing 30-second HTTP timeouts unchanged. Do not update source jobs,
review service, database state, IAM or NAV/audit schedules. Recover known prior
traffic before any optional candidate lookup on failure.

Kotak/UTI rollout must wait for this separate API acceptance. Correct source
coverage does not itself certify API performance, and this targeted safeguard
does not claim that every possible global fact-ranked request is subsecond.
