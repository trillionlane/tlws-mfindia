# MFDataIndia — Improvements / Backlog

Version-controlled backlog of planned improvements. Each item states the
*why*, the *what*, and how it fits the source-of-record strategy below.
`RESEARCH.md` holds the source research; `README.md` documents what ships
today. This file is the forward-looking "next" list.

## Additive product enhancement roadmap (decision, 2026-10-13)

**Status: implementation started for the initial backend bundle; remaining work
is planned.** The user-approved direction is to enhance the
existing product, not replace its base or rewrite its data. Existing stored
source data, identities, histories, published facts and calculation outputs must
be preserved. Enrichment, analytics and features are additive and versioned.
The separately approved implementation combines ENH-01, an initial ANA-01 slice
and DATA-05 into one provider PR. It does not authorize provisioning, deployment,
activation, source corrections or a production rollout. API/backend
and Insights UI retain separate ownership, contracts and PRs.

This decision supersedes the earlier DATA-06 source-correction pilot and DATA-07
live cleanup batches: both are removed from the active plan. The implemented
DATA-03/04 correction safeguards remain in place, but no correction writer is
to be provisioned or activated. The recorded DEV rollout at
`e7f5e67ee2977ac369e6bed9d364749015456d22` made no NAV corrections. Provider
read certification is not Insights strict-consumer or TDW application acceptance.
Older dated sections below are historical context; any overwrite, replacement,
column-drop or source-correction proposal is superseded by this preservation
decision. No existing runtime behavior has been changed by this roadmap.

### Layer ownership and boundaries

| Layer | Responsibility | Boundary |
| --- | --- | --- |
| Data — MFData | Retain source evidence and existing records; add versioned AMFI/AMC disclosures, benchmark observations, provenance and identifier mappings. | No destructive reload, source-history rewrite, identity merge, duplicate deletion or inferred replacement value. New differing source values are retained separately for review, not silently substituted. |
| Analytics — provider-owned canonical engine | Derive reproducible metrics from an exact input snapshot under a stored policy. Expose results and limitations through private APIs. | Append new results; retain old outputs. Do not overwrite published facts or let UI formulas silently redefine authoritative metrics. |
| Admin — MFData review API / Insights admin UI | Maintain human review status, evidence, annotations and versioned enrichment/override proposals. | No direct source-table edits. Any future approved override is a separately identified overlay with original values still retrievable; it is not an automatically activated correction. |
| Calculators — shared policy-bound engine / Insights experience | Produce reproducible projections and separately labelled historical simulations. | No mutation of fund data; declared assumptions, cash-flow timing and unavailable states. Choose the engine host during contract design; do not duplicate implementations across UI and services. |

Admin is a cross-cutting governance capability, not an extra source of financial
truth. Calculators use policies and, for historical mode, eligible data/analytics;
they do not need an admin UI to function. Insights owns presentation, including
the existing-shell admin grid. The retiring MFData UI is not an enhancement target.

### Preservation and publication contract

- Take an inventory and preservation baseline before each new framework or
  migration. Test on a restored clone first; compare all pre-existing records and
  values, not just row counts. Retain operational backup/restore evidence.
- New schemas use sidecars, append-only events or versioned snapshots. Do not
  truncate/reload existing holdings, overwrite non-NULL or NULL source fields,
  drop existing columns, replace old calculated outputs, or repurpose old IDs.
  Empty fields may be enriched through separately recorded facts/projections.
- Audit existing write paths, including normal ingestion and calculation
  sidecar upserts, against this new requirement. Existing immutability is not
  assumed. Flag incompatible paths before integrating new processors; this plan
  does not silently change existing schedulers or ingestion behavior.
- Policy/input changes create a new result version. Stage and validate the
  complete dependent result set before selecting it for an API projection.
  Make dataset, input and policy versions consistent within each read. Preserve
  the prior projection target for reversible publication without deleting data.
- Missing, stale, unassessed and invalid-for-calculation are distinct states.
  Keep `signals: null` for unassessed data. Human review status is separate from
  lifecycle, freshness and computed quality. Flags are leads, not proof of error.
- Extend least-privilege runtime identities and existing transaction/publication
  checks. Do not introduce unrestricted admin writes, a new public MFData API,
  per-gate GitHub variable proliferation or blanket automatic remediation.

### Stored deterministic policy requirement

Every mathematical derivation must resolve an immutable, stored policy version,
not hidden constants or a formula existing only in UI code or documentation.
Maintain source-controlled policy definitions and a persisted policy registry
with canonical-content hashes, validation and lifecycle state. Exact storage and
API shapes are deliverables of ENH-01, not already implemented capabilities.

Each policy specifies:

- Identity/version/hash, applicability and effective selection rules; engine
  code/build version and the exact input snapshot/source revisions.
- Formula/algorithm and metric units; simple versus log returns; cumulative
  versus annualized return; windows, calendar/day-count and observation rules.
- Sampling frequency, gap handling, imputation restrictions, eligibility,
  minimum coverage and behavior for unavailable/undefined results.
- Risk-free or target-rate assumptions, rate units and conversion conventions;
  any dated rates must be captured inputs, not ambient live values.
- Numeric precision, rounding stage/mode, computation-library/runtime versions,
  deterministic ordering and independent expected-result test fixtures.
- Calculator cash-flow timing, compounding convention, inflation, step-up,
  fees/taxes treatment and historical execution assumptions where applicable.

Given the same declared inputs, parameters, engine and policy versions, replay
must produce the same canonical financial result. Dates/rates must be explicit;
execution timestamps and operational metadata are not financial inputs. Persist
derived dataset results with policy/input lineage. Calculator results must carry
the same reproducibility envelope; saving personal scenarios is a separate
privacy/product choice, not mandatory storage of every interactive keystroke.
New methods never silently relabel or replace earlier results.

### Prioritized delivery plan

| Priority / sprint | Deliverable | Acceptance and dependencies |
| --- | --- | --- |
| P0 — ENH-01 Preservation and policy foundation | Inventory existing capabilities/write paths; restored-clone preservation tests; stored immutable policy registry, reproducibility envelope and canonical calculation contracts. | Existing values preserved; replay deterministic; conflicting write paths identified and resolved within an approved implementation scope before enhancement publication. No live source corrections. |
| P0 — ANA-01 Analytics verification | Audit/reuse existing return, CAGR, volatility, Sharpe, Sortino, drawdown, Calmar, monthly and rolling calculations; implement independently verified methods as new policy/result versions. | Reference fixtures cover boundaries, gaps, zero volatility, short histories and nulls. API/job/UI semantics agree; old results retained; no silent consumer contract switch. Depends on ENH-01. |
| P1 — DATA-05 Review backend/API | Per-fund human status, queue/detail/evidence/history, annotations and enrichment proposals. Define field policies, status transitions, identity binding, authorization, idempotency and optimistic version checks. | Review records persist independently of sessions and cannot mutate original data. Reuse existing evidence/ledger mechanisms where suitable. Can parallel ANA-01 after ENH-01 contracts. |
| P1 — UI-01 Insights admin grid | Existing Insights admin area: Excel-like list, search/filter, status column, field/source inspection, annotations, proposals and history. | Real admin-only backend integration; originals visible, no direct DB writes, no fake edit/save functionality. Separate consumer PR after DATA-05 contracts are agreed. |
| P1 — ENR-01 Official AMC enrichment | Append dated complete portfolio disclosures and factsheet enrichment, starting with a bounded high-value AMC cohort; identifier mapping, provenance, coverage and parser validation. | Archived source bytes, exact scheme/plan matching, full-versus-partial coverage, cash/liability/derivative-aware checks and as-of dates. Existing holdings/facts remain intact. Depends on preservation contract; acquisition design can parallel ANA-01. |
| P1 — ENR-02 Benchmark inputs | Acquire permitted authoritative TRI histories, map each fund to the appropriate dated benchmark and persist coverage/source evidence. | No fabricated TRI, guessed benchmark or undocumented price-index substitution. Assess access/licensing before acquisition. No calculated benchmark claims when required inputs are missing. |
| P1 — IDCW-01/02/03 Distribution-adjusted research pilot | Authoritative distribution-event enrichment, deterministic total-return engine and certified private API publication for a bounded HDFC cohort, starting with AMFI 101763. | Three sequential sprints can form one finite provider PR; evidence coverage is the first go/no-go gate. Raw NAV and existing outputs remain unchanged; eligibility is per fund and historical window. See [IDCW delivery strategy](#idcw-distribution-adjusted-research-delivery-strategy). Insights UI is a separate consumer PR. |
| P1 — ANA-02 Rich research analytics | Weighted security-level overlap alongside existing Jaccard; aligned benchmark comparisons, beta/alpha and suitable category-relative analytics. Publish policy-bound rolling analytics from the canonical engine. | Compatible holdings dates/fullness, identifier-based matching, aligned return windows and explicit limitations. Depends on ANA-01 and relevant ENR-01/02 coverage. Never call top-20 Jaccard full weighted overlap. |
| P2 — CALC-01 Core projections | Shared deterministic SIP and lumpsum engine with explicit nominal/effective rates, contribution timing, rounding and assumptions; separate Insights controls/results. | Independently verified schedules, zero/negative-rate cases and meaningful validation. Illustrative projection, not forecast or actual execution. Engine follows ENH-01; can precede richer enrichment. |
| P2 — CALC-02 Planning features | Step-up SIP, inflation-adjusted goal/reverse SIP and SWP with depletion timelines. | Reuse the same cash-flow/policy engine; test step boundaries, infeasible goals, partial final withdrawals and depleted corpus. Depends on CALC-01. |
| P2 — CALC-03 Historical simulations | Growth-first historical SIP/lumpsum simulations and money-weighted return/XIRR; extend step-up/SWP modes only after shared execution rules pass. | Published NAV observations, declared business-day/funds-availability assumptions, no look-ahead or invented allotment; cash-flow, coverage, XIRR failure/multiple-root and fee/tax rules explicit. Depends on analytics/data policies, not forward-fill of source history. |
| P2 — NEWS-01 Holdings-aware news relevance | Insights combines existing family association tags with dated security/issuer mappings and holdings coverage; retain evidence for links. | Ownership/exposure links distinguished from inferred relevance; stale holdings and confidence visible. Does not confer end-user write access or require a new public provider endpoint. Depends on ENR-01 and consumer integration. |

**Recommended approach:** finish operational closeout and deliver ENH-01 first,
then ANA-01. Start DATA-05 and bounded enrichment work in parallel once shared
contracts are stable; UI-01 remains a separate PR. Rich analytics follows proven
input coverage. Calculators and news are feature tracks, not prerequisites for
safe source reads or admin review. Each row requires a bounded implementation
plan; this table is not approval to execute all sprints in one release.

### Implementation tracking

Update this register in each implementation PR. Link the provider and consumer
PRs separately where applicable. Track code, merge, deployment and acceptance
as separate milestones; merging this roadmap does not complete any sprint.
Use explicit statuses such as planned, in progress, implemented, merged,
deployed and accepted, with evidence for each claimed milestone.
Apply the [representation boundary](DATA_REPRESENTATION_POLICY.md): validation
scaffolding and certified samples must not become unsupported product claims.

| Sprint | Status | Implementation PRs | Release / acceptance evidence |
| --- | --- | --- | --- |
| ENH-01 | Foundation accepted for bounded provider DEV scope | [Provider PR #33](https://github.com/trillionlane/tlws-mfindia/pull/33), rollout PRs #34–#37 | MF-E01 completed fresh archive, operational restored-clone preservation/replay and actual restricted-login checks. [Evidence](evidence/mf-e01-dev-review-20261014d.json). Not whole-dataset or consumer acceptance. |
| ANA-01 | Initial seven-metric slice accepted for the DEV cohort; monthly/rolling remains | [Provider PR #33](https://github.com/trillionlane/tlws-mfindia/pull/33), publication PRs #39–#40 | Independent fixtures and real four-fund generation/certification; MF-E02 certified new v13 artifacts after a normal cycle. Calendar/monthly and rolling are MF-E03/MF-E04. |
| DATA-05 | Initial review/proposal backend accepted for bounded private DEV scope | [Provider PR #33](https://github.com/trillionlane/tlws-mfindia/pull/33), rollout PRs #34–#37 | Actual six-route checks, diagnostic-only canary history, replay/concurrency and persistent source-bound records. No UI, human fund correctness approval, Insights RBAC acceptance or executable overrides. |
| DEV review rollout | Complete — private provider DEV acceptance | [Provider PR #34](https://github.com/trillionlane/tlws-mfindia/pull/34), rollout fixes #35–#37, [runbook](VERIFIED_FUND_REVIEW_RUNBOOK.md#preservation-first-dev-rollout) | Fresh archive/restored clone, restricted identities/schema, finite generation, six-route certification and live non-destructive recovery passed in MF-E01. |
| UI-01 | Planned | — | — |
| ENR-01 | Planned | — | — |
| ENR-02 | Planned | — | — |
| IDCW-01 | Bounded reported-observation DEV pilot accepted; full event coverage remains open | [PR #65](https://github.com/trillionlane/tlws-mfindia/pull/65), [handover](runbooks/MF_E09_REPORTED_OBSERVATIONS_DEV.md#accepted-dev-rollout-and-backend-handover) | HDFC 101763: three original PDFs, one reported observation and three private reads. Fresh backup/clone, replay, candidate/final/recovery/restored certification, live capabilities and preservation passed. Coverage remains unknown; complete-history and adjusted-analytics gates remain separate. |
| MF-E09B | Complete — bounded four-variant HDFC provider DEV pilot, 2026-10-15 | [PR #67](https://github.com/trillionlane/tlws-mfindia/pull/67), [backend handover](runbooks/MF_E09B_IDCW_EXPANSION_DEV.md#accepted-dev-rollout-and-backend-handover) | Runtime `532b125`; four private GET patterns (33 enhancement patterns total), four immutable snapshots and three retained originals. Fresh backup/new restored clone, independent per-plan reference, append/replay, candidate/final/recovery/restored HTTP, live privileges and final preservation passed. All 14,392 funds, 4,244,085 NAV rows and prior layers/v1 preserved. [42-receipt evidence](evidence/mf-e09b-dev-idcw-261015b.json). Coverage stays unknown; no adjusted analytics, UI, production or all-AMC claim. |
| IDCW-02 | Planned — policy-bound total-return engine | — | Depends on accepted complete-event/reinvestment/levy evidence, not merely IDCW-01 observation publication, and ENH-01/ANA-01 foundations; no adjustment engine implemented. |
| IDCW-W01 | Implemented offline source-readiness checkpoint; complete-window/analytics goal remains blocked | [One-fund window plan](runbooks/MF_IDCW_CERTIFIED_WINDOW_PLAN.md), [source-bound diagnostic](evidence/idcw-window-readiness-20261015.json) | 101763/FY2025–26: retained historical issuer export and query, prior snapshot, original AMFI event-date NAVs and dated SID/SAI. Exact replay, per-class reconciliation, source pins and explicit unresolved gaps. Not a coverage certificate, total-return engine, API, deployment or analytics unlock. |
| IDCW-03 | Planned — certified-window private API publication | — | Depends on IDCW-02 verification and preservation proof; existing IDCW restrictions remain in force. |
| ANA-02 | Planned | — | — |
| CALC-01 | Planned | — | — |
| CALC-02 | Planned | — | — |
| CALC-03 | Planned | — | — |
| NEWS-01 | Planned | — | — |

Initial backend bundle: see [verified fund review runbook](VERIFIED_FUND_REVIEW_RUNBOOK.md).
The opt-in schema is outside automatic migrations; current v2 reads, source
ingestion and source-write/correction paths are unchanged. Runtime authorization
and an operational rollout require separate gates. Wider field-edit workflows,
automatic enrichment execution and live policy selection are not implemented.

### Analytics and enrichment decisions

- Keep raw NAV observations unchanged. Optional carried-forward display or
  valuation values live in labelled derived projections, never in source
  history or as purported new observations/transaction prices. Do not inject
  weekend zero returns into a trading-day risk calculation.
- Define expected-feed completeness by snapshot scope, lifecycle, scheme
  calendar and publication deadline; alert on missing expected observations,
  not an assumed universal 100% population. A partial feed does not prove absence.
- Add configurable NAV-move review signals without treating a 20% move as an
  error or silently repairing/rejecting genuine market events. Validate ISIN
  format, checksum and mapping separately; optional identifier failures must not
  silently delete otherwise valid NAV evidence.
- Audit cumulative versus CAGR labels, exact anniversary/month-end windows,
  consistent periodic excess-return Sharpe/Sortino conventions, sampling and
  annualization. Risk-free Sharpe does not require an equity benchmark; beta,
  alpha and benchmark-relative comparisons do require aligned benchmark inputs.
- Standardize a disclosed versioned risk-free assumption initially. Defer live
  rate streaming; historical dated rates, if introduced, must be stored inputs.
- Preserve Growth-first eligibility and IDCW limitations. Defer adjusted IDCW
  total returns until authoritative distribution/unit-event data and policies
  exist. Do not infer payouts from drops, copy Growth history, or adjust fund NAV
  for a corporate action affecting only an underlying holding.
- Do not claim unverified 38,000-scheme coverage or generic 1st-7th disclosure
  availability. Measure coverage and use applicable official source deadlines.
- Defer all-calculator historical modes, tax-complete transaction simulation and
  live rates until bounded product requirements justify them. Retired aggregator
  scraping and a new MFData UI are not enhancement dependencies.

### IDCW distribution-adjusted research delivery strategy

**Status:** MF-E09's bounded reported-observation provider DEV pilot is accepted
([evidence](evidence/mf-e09-dev-dist-261015a.json)); complete-history certification
and IDCW analytics remain unavailable. The prior preservation-first DEV rollout is complete
for its bounded provider cohort; that does not satisfy IDCW source coverage. Reuse the
ENH-01 policy/lineage foundation and ANA-01 verified calculation conventions.
Authoritative TRI is not a prerequisite for fund-only total returns; any later
benchmark-relative claims still require ENR-02 inputs.

Scope the first release to a finite HDFC cohort starting with **AMFI 101763
(HDFC Flexi Cap Fund — Regular Plan — IDCW)**. Confirm exact identities and the
historical window during source discovery; do not promise all-AMC or full-history
coverage. The three sequential provider sprints may be delivered in one PR:

1. **IDCW-01 — Event evidence and coverage.** Evaluate permitted acquisition
   from [AMFI scheme distributions](https://www.amfiindia.com/otherdata/scheme-dividends)
   and [HDFC NAV/IDCW disclosures](https://www.hdfcfund.com/nav-and-idcw), using
   official notices/factsheets where needed. Append versioned event sidecars
   with exact scheme/plan/option mapping, original source bytes/hash, retrieval
   evidence, distribution amount and unit basis, record/ex-distribution/payment
   dates where disclosed, applicable reinvestment NAV, documented unit events
   and scheme-level levies. Do not assume record and payment dates are the
   valuation date. Resolve historical percentage-versus-currency amounts only
   using authoritative unit/face-value evidence. Retain conflicts and superseded
   evidence. Prove completeness for each proposed window; absence of an event
   record means unknown coverage unless authoritative evidence establishes no
   payout. **Accepted scope revision:** reported observations may be published
   privately with unknown completeness under MF-E09/MF-E09B. Stop adjusted-event
   acceptance and analytics publication when completeness or required adjustment
   evidence is unproven; access and exact identity remain mandatory for every
   observation append. Preserve the original blocked discovery report.
2. **IDCW-02 — Deterministic total-return engine.** Leave raw NAV, existing
   calculations and source-quality evidence untouched. Generate a separate
   reinvested wealth index and supported return/risk results under an immutable
   policy version/hash. Specify event ordering, reinvestment timing/prices,
   gross/net distribution and levy treatment, unit changes, missing observations,
   numerical precision/rounding and complete NAV/event/code lineage. Investor
   taxes and cash-payout investor simulations are separate features. Verify
   independent fixtures for no distributions, distribution-only NAV drops,
   repeated events, unit changes, gaps, conflicting evidence and undefined risk
   ratios. Identical declared inputs/policy must replay identically; changed
   events create new results rather than rewriting old ones. Never infer payouts
   from drops, borrow Growth history or treat underlying-stock corporate actions
   as fund-unit events.
3. **IDCW-03 — Certified-window private API publication.** Publish additive
   total-return fields/results separately from raw NAV changes, including
   methodology, policy/input versions, coverage bounds and unavailable reasons.
   Unlock only the verified fund/window/metric combinations; shorter certified
   windows do not unlock longer ones. Retain lifecycle, freshness and quality
   gates. Event-aware assessment must explain distribution effects without
   deleting raw signals or blanket-clearing unrelated defects; human review
   cannot bypass eligibility. Certify missing/partial/conflicting event cases,
   source preservation, atomic result/eligibility publication and safe fallback
   after input revisions. Preserve current contracts unless an explicit versioned
   consumer migration is agreed. DEV rollout and acceptance remain separate
   approval gates from code review/merge.

**Finite outcome:** evidence-backed IDCW analytics for the certified pilot
windows, with auditable unavailable reasons elsewhere. No source corrections,
blanket IDCW activation, inferred distributions or silent replacement of old
results. Subsequent Insights UI work presents raw NAV versus reinvested total
return and reason-specific limitations in a separate consumer PR; it does not
duplicate the provider calculation engine.

### Approved 15-sprint backend/data delivery sequence

User authorization on 2026-10-14 covers planning, implementation, push, review,
merge and scoped DEV rollout/certification for this sequence. Each sprint must
deliver its own finite accepted result before the next sprint begins. This is
not authorization for data deletion, wholesale model replacement, UI work,
production operations, unlicensed acquisition or weakened authentication.
Existing records, identifiers, facts, NAVs and published results remain intact;
all enhancements use additive versioned evidence/results and explicit APIs.
Do not activate the dormant NAV correction executor. Necessary recovery removes
feature exposure, not records, databases, schemas, retained revisions or backups.

The sequence below refines the roadmap above, rather than creating duplicate
enrichment/calculator projects. Scope each source-dependent release to a named
cohort and demonstrably complete date window. A source-discovery report or mock
fixture is useful progress but does not count as a consumed enriched dataset or
completed source-dependent feature. Where permissions, consumer trust, licensing
or evidence are unavailable, record the exact blocker and retain the gate; do not
substitute inferred inputs or mark the sprint complete.

| Sprint | Finite result and API/data boundary | Acceptance gate |
| --- | --- | --- |
| MF-E01 — Preservation-first DEV rollout | Execute PR #34's fresh snapshot/archive and operational restored-clone rehearsal; restricted principals/schema; separate private review service; bounded generation; six-route certification and non-destructive recovery verification. | Current merged SHA/image/policy/manifest pinned; fresh backup generation/hash and before/after preservation proof; effective runtime privileges, consumer-owned signer/trust and authorized execution arrangement; actual Cloud Run completion, private/public-denial checks and final provider certificate. No whole-DB or Insights RBAC claims. |
| MF-E02 — Refresh/audit publication coherence | Add a versioned certified research snapshot/publication boundary so a source refresh cannot silently relabel stale analytics or unnecessarily blank otherwise verified historical windows. | Preserve live source reads and legacy v2 semantics; publish new current-versus-last-certified context explicitly. Interrupted refresh/audit/publication and concurrent reads tested; no claim that old assessment validates new inputs. Observe a real refresh/audit cycle in DEV. |
| MF-E03 — Canonical calendar/monthly returns | Stored policy-bound completed-year, calendar YTD and month-end return artifacts with explicit boundary/date conventions and null reasons. | Independent arithmetic fixtures, leap-year/month-end/gap/short-history cases, deterministic replay and exact lineage; private additive read contract and real eligible Growth cohort certified. Existing monthly/yearly results retained. |
| MF-E04 — Canonical rolling analytics | Anniversary-window rolling return series and summaries from the provider engine, independent of any UI chart selection. | Declared endpoint schedule and start-observation rule; no look-ahead, invented NAV or unsupported annualization; minimum observation/coverage gates and private reads tested on real covered windows. |
| MF-E05 — Official enrichment evidence foundation | Append-only archived source manifest, exact variant/security mapping, parser policy and coverage ledger for a bounded official AMC cohort. | Access/retention permitted; source bytes/hash/as-of evidence; duplicate/revised/conflicting/partial documents retained and distinguished. No existing loaders redirected into source overwrites. Bounded import and private provenance/coverage reads certified. |
| MF-E06 — Dated official fund facts | Publish versioned official factsheet enrichments for the selected cohort: disclosed AUM, expenses, manager, benchmark name and other explicitly supported fields. | Field-level provenance, units, dates, exact variant/fund-level scope and missing/conflicting reasons. Originals remain readable; no silently borrowed sibling values. Real source-to-API samples certified. |
| MF-E07 — Complete official portfolios | Versioned full holdings snapshots with security identifiers, weights, cash/liability/derivative treatment and source date/fullness. | Named AMC/date cohort; parser reconciliation against complete disclosure totals and independent rows; no top-ten subset labelled full portfolio; inconsistent files retained as unaccepted evidence. Private snapshot/holdings/coverage reads certified. |
| MF-E08 — Weighted overlap analytics | Deterministic security-level weighted overlap and concentration for pairs with compatible accepted portfolios. | Versioned security-identity/date/weight policy; independent pair references, disjoint/identical/cash/partial/gap cases; unavailable rather than fabricated full overlap. Existing Jaccard retained separately; real pair API certificate. |
| MF-E09 — IDCW evidence pilot (IDCW-01) | User-approved revised result: append-only reported observations and private policy/coverage/exact-snapshot reads for HDFC AMFI 101763 over a bounded window. | Exact issuer/provider identity, retained original sources, deterministic policy/lineage/replay, explicit unknown coverage, retained conflicts, no analytics eligibility. Fresh backup/clone/private HTTP/recovery/preservation evidence required for DEV acceptance. Complete-event/reinvestment/levy/unit-event evidence remains an E10/E11 dependency, not falsely certified by this feature. |
| MF-E10 — IDCW total-return engine (IDCW-02) | Immutable-policy reinvested wealth index and supported return/risk artifacts for the verified event windows. | Independent reinvestment/event/unit-change fixtures; missing/conflicting evidence fails closed; gross/net/tax assumptions explicit; replay and preservation proof. No copying Growth returns or rewriting raw NAV. |
| MF-E11 — Certified IDCW API publication (IDCW-03) | Additive private total-return reads with per-fund/window/metric eligibility, coverage and unavailable reasons. | Source revision changes invalidate current publication; lifecycle/freshness/event-aware quality gates retained; no blanket signal clearing or review override. Real pilot certification and no legacy contract drift. |
| MF-E12 — Authoritative benchmark pilot | Versioned permitted TRI history, dated benchmark mapping and aligned benchmark-relative analytics for a finite mapped cohort. | License/access and exact TRI identity proven; no PRI substitution; aligned calendar/window/currency and versioned beta/alpha conventions independently tested. Missing history is unavailable. Real source and private API certificate required. |
| MF-E13 — Deterministic forward calculators | Private calculation APIs for lumpsum, regular/step-up SIP, inflation-adjusted goal/reverse SIP and SWP/depletion, using one policy-bound cash-flow engine. | Explicit effective/nominal rate, deposit/withdrawal timing, precision and rounding; cash-flow schedules and infeasible/zero/negative-rate/partial-final-withdrawal cases independently verified. Assumed projections, not forecasts; no source writes or personal scenario persistence. |
| MF-E14 — Historical Growth simulations | Bounded Growth-only historical SIP/lumpsum schedules and money-weighted return/XIRR with private reads/calculation contract. | Authoritative observations and explicit next-available-NAV/funds-availability policy; no look-ahead or source forward-fill. Short/gapped/ineligible histories, fees/tax exclusions, failed/multiple XIRR roots and exact cash flows tested. Real covered samples certified. IDCW investor simulation remains separate. |
| MF-E15 — Dataset coverage and consumer handover | Controlled bounded batches across the declared eligible universe; preservation/coverage inventory, all new API contracts, performance/security/freshness/recovery certification and backend consumer handover. | Record population/counts, completed batches, excluded funds/windows and exact reasons; no unsupported whole-market claims. Real endpoint probes, pagination/replay/load bounds and private authorization tested. Retain all old/new results; publish current-versus-historical state. Final certificate cannot hide unresolved source-dependent gaps. |

**Per-sprint loop:** start from reconciled merged main; freeze bounded acceptance
criteria; implement and run focused plus proportionate full validation; push a
dedicated PR; review the exact head in a separate adversarial pass (not a claim
of independent human approval); fix findings and revalidate; merge only green,
reviewed head; reconcile any automatic deployment; perform scoped DEV operations
and actual data/API acceptance; commit/link evidence and close out. Stop on any
unexpected source change, unconfirmed recovery, missing credential/permission,
licensing constraint or unresolved P1/P2. No bypass of repository protections,
Owner/Editor grants, provider control of Insights runtime, new per-gate variables
or automatic deletion as cleanup.

**Execution refinement — 2026-10-14:** MF-E01–04 are accepted. MF-E05–12's new
official-source imports require source-specific acquisition/use scope. The
operator has now reported telephone confirmations from UTI, SBI and Kotak that
their publicly available data may be downloaded and used for factual extraction
to support mutual-fund distributors. Record this as operator-reported oral
authorization, not an independently verified written licence. The operator has
subsequently directed all-AMC coverage, using AMFI's official factsheet directory
as the discovery route and official AMC sources where that directory points.
UTI/SBI/Kotak are not the coverage limit; do not require a calling exercise for
every AMC. Discover the current directory population instead of hardcoding 52
or claiming every document is hosted by AMFI. Call dates, representatives and
specific document coverage have not been supplied; do not invent those details
or impose a universal paid-feed/permission-letter gate.
The intended use remains factual extraction for distributor support. Retain
attribution, source dates/hashes and private source evidence; do not sell or
republish factsheets, copy narrative material or claim AMC authorship/endorsement.
All-AMC product scope is not evidence of AMFI-wide or third-party benchmark/TRI
permission. Record reported authorization separately from published source terms.
Flag explicit source-specific conflicts for resolution, retaining other safe
work rather than imposing a blanket stop. Use bounded acquisition batches,
exact scheme/document mapping and explicit missing/unsupported/conflicting
coverage before an import can be accepted. No bulk import is authorized by this
scope note alone where the required use conflicts with the applicable terms.
MF-E13's assumed forward calculators do not depend on those sources, and MF-E14
uses already-held Growth NAV snapshots. Execute MF-E13, then MF-E14 as the next
two independently reviewed/merged/certified finite releases; resume MF-E05–12
when their source gate is satisfied and close MF-E15 only after every deliverable
is accepted. This changes delivery order only, not the fifteen feature scopes,
source-access gates, preservation boundaries or overall completion definition.

**Initial execution record:** PR #34 merged at
`88eb8ad02cbddec09a25d7e1724cf1a91892fedb`; exact-merge verification passed and
automatic DEV deployment was skipped. MF-E01 preflight on 2026-10-14 found no
review trust/signer secrets or dedicated review identities. The Insights runtime's
explicit service-account-user policy permits its own deployer, not the MFData
deployer. Consumer-owned trust/signing bootstrap and a reviewed narrow execution
arrangement are prerequisites; do not grant the provider deployer control of the
Insights runtime to get around this. Image preparation is separate from rollout.

| Sprint | Current status | Implementation / evidence |
| --- | --- | --- |
| MF-E01 | Complete — bounded provider DEV acceptance, 2026-10-14 | PRs #34–#37; fresh immutable backup, operational restored-clone preservation/replay, restricted direct logins, private deployment, four-fund generation, candidate/final six-route certification and live non-destructive recovery/recertification. [Closeout evidence index](evidence/mf-e01-dev-review-20261014d.json). Not whole-DB or Insights RBAC acceptance. |
| MF-E02 | Complete — bounded provider DEV acceptance, 2026-10-14 | PR #39 plus candidate-audience fix #40, runtime checkpoint #41. Fresh archive/clone, compatible baseline, restricted additive installation, private candidate/final/recovery checks and the real 06:30/06:40 normal cycle passed. Old v12 artifacts remained dated/not current; new v13 artifacts were generated, certified and registered without replacing old rows. [Closeout evidence](evidence/mf-e02-dev-pub-cycle-20261014.json). |
| MF-E03 | Complete — bounded provider DEV acceptance, 2026-10-14 | PR #43; fresh immutable archive and operational restored-clone rehearsal, restricted additive installation, four calendar artifacts, original/new candidate and final private HTTP checks, certificate registration, recovery/restoration and whole-source/prior-review preservation passed. [Closeout evidence](evidence/mf-e03-dev-cal-20261014a.json). Not whole-dataset analytics or consumer RBAC acceptance. |
| MF-E04 | Complete — bounded provider DEV acceptance, 2026-10-14 | PR #45 plus dispatch fix #46; 833 CI tests passed with one documented skip. Fresh archive and operational clone, restricted installation/generation, original/new private checks, registration, recovery and regional whole-source/prior-layer preservation passed. [Closeout evidence](evidence/mf-e04-dev-roll-20261014b.json). AMFI 101762 has 48 one-year and 24 three-year windows; five-year real coverage remains unavailable. |
| MF-E05 | Private evidence API deployed/certified in DEV (#56); full enrichment acceptance still open | Fresh backup and operational clone, restricted SQL 027 installation, original ABSL PDF append/replay, private fifteen-route candidate/fixed/restored checks and real traffic recovery passed. Final export preserved all 14,391 source funds, 4,239,958 NAV rows and prior layers; only one policy/blob/document and two mappings added. Four new metadata route patterns are consumable privately. Stored v1 PDF mappings remain explicitly unsupported/unmapped: no accepted fields, whole-AMC or Insights end-user RBAC claim. Exact source identity/field acceptance and current all-AMC discovery remain next; do not repeat the completed metadata rollout. [DEV evidence index](evidence/mf-e05-dev-evi-261014a.json). |
| MF-E06 | Complete — bounded ABSL provider DEV pilot, 2026-10-15 | PR #58 runtime `074b126`; exact issuer AMFI/ISIN bridge for 103174/103173, three dated benchmark/manager/BER fields per variant, explicit AUM/TER unavailability and three private read patterns (18 total). Fresh archive/clone, exact replay/clone-live IDs, private candidate/fixed/restored checks and actual automatic recovery passed. Final export preserved 14,391 source funds, 4,240,198 NAV rows, prior analytics/review and every old evidence row. [Acceptance evidence](evidence/mf-e06-dev-fact-261015a.json), [backend handover](runbooks/MF_E06_OFFICIAL_FACTS_DEV.md#backend-consumer-handover). No all-AMC, current-month freshness, UI or IDCW-adjusted analytics claim. |
| MF-E07 | Complete — bounded provider DEV portfolio pilot, 2026-10-15 | #60 plus measured installation-budget repair #61; runtime `9e4d410`. Large Cap 103174 (77 holdings) and Flexi Cap 103166 (89), August 31 2026. Four new private patterns in a certified 22-pattern profile; old 18 contracts unchanged. Fresh backup/new operational clone, exact clone/live/replay IDs, independent full-row source oracle, private candidate/fixed/restored checks, actual recovery and live effective-role checks passed. All 14,391 source funds, 4,240,204 NAV rows and prior layers preserved; one policy/two sources/two snapshots appended. [Evidence](evidence/mf-e07-dev-port-261015b.json), [handover](runbooks/MF_E07_COMPLETE_PORTFOLIOS_DEV.md#backend-consumer-handover). Initial failed installation and read-only cohort-probe repair are documented. Not all-AMC/current-month, unrestricted overlap, IDCW, consumer UI/RBAC or production acceptance. |
| MF-E08 | Complete — bounded provider DEV overlap pilot, 2026-10-15 | #63 runtime `8d3773e`; [26-route policy/runbook](runbooks/MF_E08_WEIGHTED_OVERLAP_DEV.md), [DEV closeout and backend handover](https://github.com/trillionlane/tlws-mfindia/pull/63#issuecomment-6196783642). One same-date E07 pair; independent original-XLS reference is 43 shared securities and 58.79870728 percentage points. Fresh backup/clone, append/replay, candidate/fixed/restored certification, actual recovery, role checks and source/prior-layer preservation passed. Full economic overlap remains unavailable by policy; no all-AMC/current-month, consumer UI/business or production claim. |
| MF-E09 | Complete — bounded reported-observation provider DEV pilot, 2026-10-15 | #65 runtime `a8f60f8`; [handover and limits](runbooks/MF_E09_REPORTED_OBSERVATIONS_DEV.md#accepted-dev-rollout-and-backend-handover), [38-receipt evidence index](evidence/mf-e09-dev-dist-261015a.json). HDFC 101763, selection FY2025–26: three original PDFs/one reported observation, three new private GET patterns (29 total). Fresh backup/new restored clone, restricted installation, exact append/replay, independent source reference, candidate/final/recovery/restored HTTP, live capabilities and final preservation passed. All 14,391 source funds, 4,240,204 NAV rows and prior layers unchanged. Original discovery retained; coverage remains unknown and adjusted analytics unavailable. Complete window, reinvestment and levy/unit-event evidence remain separate gates. |
| MF-E09B | Complete — bounded four-variant HDFC expansion, 2026-10-15 | #67 runtime `532b125`; [handover](runbooks/MF_E09B_IDCW_EXPANSION_DEV.md#accepted-dev-rollout-and-backend-handover), [42-receipt index](evidence/mf-e09b-dev-idcw-261015b.json). Three original PDFs/four snapshots, selection FY2025–26; four private GET patterns and frozen old 29-pattern/v1 contracts. Fresh backup/new restored clone, restricted append/replay, independent per-plan reference, candidate/final/recovery/restored HTTP, live effective capabilities and final regional preservation passed. Existing 14,392 funds, 4,244,085 NAV rows and all prior layers/v1 unchanged. Coverage remains unknown; no adjusted analytics, consumer UI/RBAC, production or all-AMC readiness. |
| MF-E10–MF-E12 | Separate evidence/source dependencies — engines/publication not started | HDFC discovery and MF-E09B observation publication do not establish complete distribution-event coverage, reinvestment/levy/unit-event inputs or third-party TRI rights. [IDCW-W01](runbooks/MF_IDCW_CERTIFIED_WINDOW_PLAN.md) adds a source-bound offline readiness diagnostic and retained official query/rule/NAV evidence, not source acceptance or a completed E10/E11. Preserve original event/benchmark acceptance gates. |
| MF-E13 | Complete — private deterministic forward calculators | #48/#49 deployed and certified at runtime `ae504db`, dataset v14. Eight real private scenarios, all nine prior paths, actual rollback/restoration, public denial and whole-source preservation passed. One immutable policy added; no source rows, schema/grants or personal scenarios changed. Evidence: `docs/evidence/mf-e13-dev-fwd-20261014b.json`. |
| MF-E14 | Complete — private historical Growth SIP/lumpsum and XIRR | #51 deployed/certified at `957ba7d`, dataset v14. Real Growth one-purchase/12-instalment samples, withheld IDCW/quality/null controls, ten-route compatibility, actual recovery and whole-source preservation passed. Exactly one immutable policy added; no source/scenario writes or UI. Evidence: `docs/evidence/mf-e14-dev-hist-20261014a.json`. |
| MF-E15 | Planned — not started | Final universe/API/operations handover cannot hide unresolved source-dependent deliverables. |

The notes below are historical execution records, not the current status. The
successful `dev-review-20261014d` chain supersedes the stopped attempts without
replacing their evidence, keys, backups or restored clones. The certified runtime
is SHA `54383575ac75e31a5684843ca372f50dac46afe0`; a documentation-only closeout
merge does not redeploy it or certify a different runtime SHA.

Image preparation above completed successfully for PR #34's merged SHA; retained
digest is `sha256:4b43c051aa75a0b6b913d0d27d188a0b487487ed446145e5eb76d617318990b7`.
This is image provenance evidence only, not database or API certification. After
any new merge, reconcile checkout/image pins again before execution.
`scripts/preflight_verified_review_dev.py` confirmed the two missing secrets and
unverified consumer execution arrangement through metadata-only live reads;
28 hermetic cases passed locally. No source connection/payload access or rollout
mutation was performed by preflight. Narrow provisioning permissions and the
consumer-owned execution arrangement still require actual effective-permission
verification; project-level role inventory alone is not that proof.

MF-E01 continuation: an existing signed-in human project operator has actual
Insights-runtime `iam.serviceAccounts.actAs` and source-secret access permissions
observed through read-only permission probes. An explicit operator-mediated
executor is being implemented/reviewed; this avoids any new provider-deployer
control of Insights. Permission probes are diagnostic, not rollout acceptance.
No trust creation, backup, schema install or source operation is claimed yet.

The read-only operational probe exposed v1 preservation transport's off-region
`FETCH 1000` round-trip overhead. The task-owned probe was interrupted without
source writes. A batched-catalog/read-only binary stream replacement retains the
same v1 checksums and manifest; its full-manifest equivalence and preservation
regressions passed against isolated PostgreSQL. This is part of MF-E01 readiness,
not a reason to extend live source-lock bounds or claim rollout success.

A fresh diagnostic backup completed in 148.16 seconds: 48,661,228 bytes, SHA-256
`261a26f1c7a7b4320b78655b92f3c2a6086900d8142089a21aeebe34831968ff`,
including all 4,235,842 NAV rows. A separate read-only capture took 80.85 seconds
and matched every source preservation field from that backup. These local private
artifacts are not yet the manifest-bound rollout/archive/restore certificate.
The operator path therefore places the atomic installation beside Cloud SQL as
the existing migration workload, retaining the 180-second bound rather than
lengthening an off-region transaction. Private signing material is never mounted
in that installation job, and new credential-reader grants are temporary.

MF-E01 live attempts `dev-review-20261014a/b` stopped before archive/install:
the first had a sanitized pre-backup runtime failure; the second hit the unchanged
120-second read-only statement limit. Reconciliation found no review schema,
principals, credentials, service or archived rollout dump. Consumer trust/key
versions 1 were successfully created and remain retained; no key rotation is
planned. Regional backup/export is being added, not a relaxed timeout. The local
10 GiB container VM is full; no image/volume pruning or database interruption is
authorized. OCI metadata-only provenance was verified against the actual prepared
image without downloading layers. Operational clone data remains in the private
host-backed directory. These attempts do not complete MF-E01.

MF-E01 closeout: attempt `dev-review-20261014c` successfully exported/restored the
source and certified the clone, then stopped before live installation because
project IAM had not yet observed the newly created API identity. Cloud audit logs
confirmed the account-propagation error. The dedicated API/generator identities
were explicitly reconciled, with only `roles/cloudsql.client` at project level;
the provider deployer received actAs only on these two provider-owned identities,
not on Insights. Attempt `dev-review-20261014d` used a new backup and isolated
clone and completed every live gate. Installation preserved 4,235,842 NAV rows,
14,390 funds and the full pre-existing preservation inventory. Only the pinned
optional migration receipt was appended to the existing source migration ledger.

The private service is `tlws-mf-review-dev`, using its dedicated runtime identity
and internal ingress, with Insights as its sole explicit invoker. The four-fund
cohort is `[101762,101763,139889,154701]`; all five coverage controls were observed.
IDCW remains withheld, duplicate-series eligibility remains false and unassessed
quality retains explicit `signals: null`. Incomplete risk history returns declared
unavailable reasons; no observations or distributions were invented. The two
canary events are `in_review` plus a non-executable, no-op metadata proposal—not
human fund-correctness approval. No source correction executor, new scheduler,
old read-v2 service change or UI was activated.

The live recovery drill confirmed the retained dormant revision at 100% traffic,
revoked the sole invoker, verified external denial, then restored the exact
certified revision and recertified all six routes with unchanged result IDs.
No records, schemas, revisions, backups or local test/clone data were deleted.
MF-E02 is next: explicit current-versus-last-certified publication context, with
real refresh/audit observation and separate acceptance before MF-E03 begins.

**MF-E02 finite acceptance:** preserve the original nineteen-route read-v2 API and
six-route review-v1 contract; deploy the explicitly selected seven-route research
profile on the existing private review service, with no new gate flags. Add only
`mf_review.certifications` and one immutable publication policy; originals,
prior policies/results/review events and source migration receipts stay unchanged.
Only the migration owner may register a certificate, after read-only verification
of its actual immutable Cloud Run execution, source image, network, reader/signer
versions, completed status and service-origin certificate. Imported older evidence
is ordered by actual completion time, never import time. A green conformance
certificate is not a fund-correctness decision and does not override eligibility.

The publication read must never relabel an old input hash as current or allow
historical results in current comparisons. Reuse is limited to the exact retained
NAV window, stable identity (excluding refresh-only last-seen date), supported
metric policy/engine and non-invalidating quality evidence. Corrected/inserted/
removed historical points, identity changes, new signals, missing assessment or
unsupported methodology withhold numeric metrics; original artifacts remain
available for audit. No uncertified/legacy fallback or inferred NAV is allowed.

Before live installation: fresh private backup including prior-review fingerprints,
new isolated restored-clone rehearsal, bounded atomic source/prior-artifact
preservation checks and effective runtime privileges. Then stage the pinned private
profile at zero traffic; register actual dated evidence, certify the new read and
unchanged base routes, promote with prior-traffic/access recovery protection and
recertify. Observe the next normal DEV refresh/audit cycle through coherent live
publication responses; retain changed/current-versus-historical evidence and
refresh the bounded result/certification if necessary. Synthetic interleavings or
CI cannot substitute for that operational observation. No source corrections,
data deletion, scheduler changes or UI work are part of this sprint.

**MF-E02 runtime checkpoint — 2026-10-14:** the seven-route profile is privately
serving revision `tlws-mf-review-dev-dev-pub-20261014b-research`, pinned to source
SHA `8bb1f680dae5605563e7a0cc2e97f3ef254b2007`. The compatible six-route revision
of that same image is the retained recovery target. Original read-v2 service,
association writer and normal schedules were not redeployed or modified.

Fresh backup and operational restored-clone proof include all prior review rows
and event-sequence state. Live installation preserved the source/prior-review
fingerprints and appended only the new publication policy in its sidecar registry;
the source migration ledger stayed unchanged. Three actual dated provider
certificates were registered, selected by immutable completion time. The four-fund
control cohort remains `[101762,101763,139889,154701]`. Growth is eligible when
exact/current; IDCW, duplicate-signalled and unassessed controls remain ineligible.
Unassessed quality retains explicit `signals: null`; no signal was cleared.

Recovery actually restored the compatible revision and removed the sole invoker,
then restored the feature revision. Immediate recertification initially failed
with HTTP 403 / missing `run.routes.invoke` while the re-grant was propagating;
that failure and closed-state evidence remain retained. After exact-binding
reconciliation and propagation, a new read-only private execution passed with
unchanged result IDs. No data/schema/revision/backup deletion or restore over DEV
was used. A separate baseline evidence-observation failure was reconciled using
the original immutable successful execution, not by rerunning its mutations.

That initial checkpoint did not close MF-E02. The pre-cycle reference was dataset
v12, NAV as of 2026-10-13; current and retained input hashes agreed at that point.
The next normal refresh/audit cycle was still required to demonstrate live
current-versus-historical behavior and fresh certification where needed. No manual
refresh, audit, recomputation or source correction was dispatched to accelerate
the observation. MF-E03 implementation waited for the complete MF-E02 gate.

**MF-E02 closeout — 2026-10-14:** the normal refresh execution
`tlws-mf-data-nav-refresh-dev-94qxz` completed at 06:31:03 UTC and advanced the
dataset to v13. The scheduled audit `tlws-mf-data-nav-audit-dev-wtnhl` completed
at 06:40:49 UTC. Actual database observations and separate private HTTP jobs
before/after the audit verified that retained v12 artifacts did not become current
v13 evidence: Growth's unchanged historical window remained explicitly dated,
current comparisons were disabled, and the ineligible controls withheld metrics.
All existing review rows and sequence state remained unchanged through the cycle.

A fresh bounded manifest generated four v13 snapshots/results. Six-route
conformance, immutable-execution verification, owner-only certificate registration
and the seventh private read all passed. Only AMFI 101762 is current comparison
eligible; IDCW 101763, duplicate-signalled 139889 and unassessed 154701 remain
ineligible, including explicit `signals: null` for 154701. Its two new diagnostic
events are not a human review approval or executable source override. Old results,
snapshots, events and certificates remain retained.

The fresh pre/post-append comparison covered all 4,236,069 NAV rows, 14,390 funds,
other stored `mf` relations, structure/functions/triggers/sequences/ownership/ACLs
and all prior review artifacts. No source row, schema or serving configuration
changed during this bounded append/certification; no data was deleted. The local
wrapper's final checker omitted a required checksum argument after the cloud
steps passed. Its failure is retained; a new read-only comparison supplied the
argument and passed, without rerunning any mutating job. The runtime remains SHA
`8bb1f680dae5605563e7a0cc2e97f3ef254b2007`; documentation merges do not redeploy it.
See the [closeout index](evidence/mf-e02-dev-pub-cycle-20261014.json). MF-E03 is now
next; MF-E03–MF-E15 are not completed by this closeout.

**Future source-access gate:** HDFC's [published terms](https://www.hdfcfund.com/disclaimer),
checked on 2026-10-14, require written permission for commercial reuse. No such
permission evidence was supplied during MF-E02. Confirm existing approved rights
or a permitted authoritative supplier before its enrichment/distribution imports;
public web access and an old robots.txt research note are not permission proof.
This does not prevent policy-bound calculations from already-held NAV data.

### Operational / API closeout remains separate

- Observe the restored normal refresh and full audit cycle; activation probes
  are not scheduled-cycle evidence. Reconcile current state before future runs.
- Obtain fresh consumer-owned strict v2 Insights certification through a bounded
  reusable gate, distinguishing runtime SHA from schema provenance and never
  reusing consumed approvals/execution budgets.
- Association-tag writer restoration remains a separately approved operation.
  TDW runtime-identity checks do not substitute for consumer application testing.

## Source-of-record strategy (decision, 2026-10-08)

Historical strategy below. For future enhancements, the 2026-10-13 additive
roadmap above governs: no aggregator reactivation or mutation of retained source
records is authorized by these older proposals.

**Official sources only in prod:** AMFI (NAV + identity) and each AMC's own
disclosures (factsheet + monthly portfolio). Aggregators (**Scripbox /
Groww**) are **dev-only tools** — never scheduled or auto-triggered in prod.

- If an aggregator value is ever needed, the pattern is: **fetch in dev →
  produce a diff (only new/changed values) → insert just that diff into
  prod.** Fragile / ToS-sensitive scraping stays confined to dev.
- Initial prod load is a **pure DB migration** (`pg_dump`/`pg_restore`) of the
  existing dev dataset — prod is not rebuilt from source.

This backlog is what makes "official sources only" fully achievable.

---

## P0 — AMC monthly-portfolio parser (full holdings)  ⭐

**Why (the one real gap):** `fund_holdings` (228,185 rows; 3,724 funds; up to
~1,500 rows for index funds) is the *full* portfolio, but **today only the
Groww + Scripbox crawlers keep it current** (`groww_to_holdings` = full list;
`scripbox` = top-10). If those connectors stop running, holdings go **stale**
(not deleted) month over month, because portfolios turn over monthly.

**What the official source is:** each AMC's **monthly portfolio disclosure** —
the SEBI-mandated document listing *every* security (name, quantity, value,
weight). It is a separate "Portfolio / Monthly-Portfolio" download, distinct
from the monthly factsheet. We already have the **directory of all 53 AMCs'**
portfolio links in `research/amfi_amc_directory.json`; the parser does not
exist yet.

**The what:** build an AMC-portfolio parser (one class per AMC, same design as
the factsheet parser in `mfdataindia/ingest/amc_factsheets/`) that:
- downloads the AMC's latest monthly portfolio disclosure,
- parses the full per-scheme security list,
- **adds a versioned complete portfolio snapshot** for that scheme, retaining
  existing `fund_holdings` and all earlier snapshots (supersedes the earlier
  wholesale-replacement proposal under the 2026-10-13 preservation decision),
- records provenance (AMC, portfolio period, as-of date, source URL).

**Payoff:** full holdings become an **official source**. This is what lets us
retire Groww/Scripbox for holdings with no loss (see P3).

**Ordering:** P0 should land *before* we stop running the aggregator crawlers
in dev, so there's never a window where full holdings have no refresh path.

---

## P1 — Extend the AMC factsheet parser to the other AMCs

ABSL is done (POC: beta/Sharpe/std-dev; 17 fills, verified against a pre-run
pg_dump). The other 52 AMCs each have a **different factsheet layout**, so this
is incremental (parser class per AMC), not a one-off.

- **Start with the big-5** (SBI, ICICI Prudential, HDFC, Kotak, Nippon) —
  they cover the most funds.
- Same POC-then-fill discipline per AMC (parse → dry-run → verify against a
  pre-run `pg_dump` → fill).
- Expect a similar share of **published-NA** for debt funds (ABSL: 28 of 67).
- Factsheet fields available beyond risk metrics (AUM, base ER, inception,
  benchmark, manager, CAGR returns, exit load, riskometer, scheme rating,
  **turnover**, top-10, asset/sector/credit allocation, duration) — the v1
  field set fills only beta/Sharpe/std-dev; widen it per AMC as layouts allow.

## P2 — Aggregator-only fields

A small set of fields are not in a single-month factsheet and are currently
Groww-only: `registrar_agent`, `expense_ratio_history` (~84 funds each).
`portfolio_turnover` (48) may also be Groww-only today, but the factsheet
carries turnover too, so it is likely closable via P1. Decide per field:
(a) add official AMC enrichment with provenance, or (b) preserve the existing
field with explicit missing/stale coverage. Earlier dev-only fetch/diff-push and
column-drop options are superseded by the 2026-10-13 additive roadmap.

## P3 — Retire Groww/Scripbox

**Done (2026-10-08):**
- Connectors **archived** under `archived/aggregators/` (scripts, jobs,
  clients, loaders, test) so they cannot be accidentally triggered. The
  `make enrich-scripbox` / `enrich-groww` / `enrich-groww-all` targets are
  removed, and `bootstrap_local.py` no longer loads bundled Scripbox evidence.
- **Aggregator references purged from the DB** (migration 013, compliance —
  we do not hold/disseminate the aggregator's own identity/provenance):
  - dropped `fund_facts` cols: `scripbox_fund_id`, `groww_slug`,
    `groww_rating`, `groww_return_stats`, `groww_fetched_at`,
    `groww_source_mode`, `groww_inherited_from`, `raw_payload`, `fund_slug`,
    `fund_variant` (superseded by AMFI-derived `mf.fund_variants`)
  - `fund_facts.source` `SCRIPBOX`/`GROWW` → neutral `LEGACY` (3,943 rows)
  - dropped `mf.fund_opinions` (aggregator editorial artifact; not in API/UI)
  - purged 13,943 dead `GROWW`/`SCRIPBOX` `ingest_checkpoints` rows
  - stripped the `"source":"GROWW"` key from `holdings_analysis` jsonb;
    rebuilt `fund_facts`/`source_metadata`/`funds` CHECKs without aggregator
    names; recreated `v_fund_data_status` + `v_enrichment_coverage` cleanly
- **Our own fund identity** `mf.fund_family` (1,906 families): `tlws_mf_id`
  (deterministic UUIDv5 of `group_key`), `slug`, `tags` — for related-news
  retrieval and stable URLs. Built by `scripts/build_fund_family.py`
  (`make build-family`, idempotent), returned as `family` on
  `/api/funds/{code}`.
- Verified: zero `scripbox` strings in any `mf` table/column/value/constraint;
  `groww` remains only as a **fund house** (its 217 funds, holdings of its
  funds, its benchmark indices, manager bios) — legitimate data, kept.

**Remaining (depends on P0):** replace the aggregator *refresh* of
`fund_holdings` with the AMC monthly-portfolio parser, so the archived
crawlers are no longer the only thing keeping full holdings current.

---

## P1 — NAV-series integrity and scheme lifecycle (open, 2026-10-10)

**Why:** the partner research UI renders whatever `nav_history` holds, and a
triage of the populated database found series that are structurally impossible
plus a scheme lifecycle the schema does not model. Both are visible to a
distributor as a wrong chart, not as a warning.

Measured on the 14,368-scheme dev database:

- **Frozen series — 8 codes** have >250 NAV points and **exactly one distinct
  value**. `139891` / `139892` (Franklin India Liquid Fund, REGULAR GROWTH and
  IDCW) sit at precisely `10.0000` for 1,525 consecutive observations
  (2021-10-04 → 2026-10-07). They render a flat line, 0.00% returns, and can
  never move in `movers`. Suspected ingest vector: the next-day liquid/overnight
  NAV path PR #19 (`fa2e2cd`) just tightened — worth re-checking whether these
  rows predate that guard.
- **Duplicated series across options:** `139889` (GROWTH) and `139890` (IDCW)
  carry byte-identical series (13.2720 → 18.1002, same 1,525 dates). An IDCW
  option that never diverges from its Growth twin is not a real payout history.
- **One name, many funds:** `Franklin India Liquid Fund` is 13 AMFI codes under a
  single `fund_variants.group_key`, with latest NAVs from ₹10.00 to ₹6,339.87.
  The family view collapses them into one row whose representative is whichever
  variant the pick rule chose, so "the same fund" is one arbitrary code.
  **Corrected earlier flag:** `100538` at ₹6,339.87 is *legitimate* — 1,830
  distinct daily values accreting 4,820.63 → 6,339.87 is what a long-running
  liquid fund looks like. The anomalies are the frozen/duplicated rows above.
- **Maturity is not modelled:** FMP `151566` ends at `10.0000` on 2026-09-09
  after `12.5916` the previous session — a −20.58% single-day move that is
  actually the redemption at face value. It is still `is_active = true`,
  `is_defunct = false`, so it is served as a live fund and its "Growth of
  ₹10,000" chart ends in a cliff. **191 of 4,291** served codes have no NAV in
  30+ days (168 in 180+).
- **IDCW metrics:** 2,324 of the 4,291 served codes are IDCW options, and every
  return / CAGR / Sharpe / drawdown is computed NAV-to-NAV from post-payout NAV.
  A payout therefore reads as a loss (the FMP sawtooth). Needs a decision
  before code: build a payout-rebased series, restrict risk metrics to
  Growth/IDCW-Reinvestment variants, or disclose "NAV-based, excludes dividends"
  and suppress the maturity reset.
- **Buyability is stored but not surfaced:** `fund_facts.status` /
  `transaction_status` are populated (`ACT/ALL` 3,461 · blank 409 · `ACT/TSUSP`
  260 · `TER/ALL` 41 · `SUSPACT/SUSP` 31 · `INC` 7) but no endpoint exposes them
  as a first-class "can a client transact in this today" signal.

**The what:**
1. A NAV-integrity check in `scripts/refresh_daily.py` (and a one-off audit
   script): flag zero-variance series above a point threshold, and identical
   series across two option variants of one family.
2. Derive a maturity/closed signal for `CLOSE_ENDED` schemes (last NAV far
   behind the dataset reference, or a terminal NAV reset back to face value) and
   expose it in `/api/funds/{code}` so consumers can badge "matured" instead of
   charting it as a crash.
3. Expose `status` / `transaction_status` through the detail payload as an
   explicit buyability flag.
4. Decide the IDCW methodology question above, then apply it consistently to
   charts, returns, analytics and movers.

## UI: dark mode + front-page category movers (done, 2026-10-08)

- **Dark mode** on all three pages (index / fund / compare). The theme is
  applied before first paint by an inline head snippet (no flash); the topbar
  toggle persists to `localStorage` and defaults to the OS preference.
  Everything is driven by CSS custom properties — the dark palette is one
  `:root[data-theme="dark"]` block. Chart.js canvases bake colors in at build
  time, so `fund.js`/`compare.js` read `--chart-tick`/`--chart-grid`/
  `--chart-fill-border` at draw time and re-run their init on toggle
  (`window.__mfdThemeChanged`).
- **Front page** now leads with **Top movers by category**: one tile per broad
  family (Equity, Debt, Hybrid, Index, ETF, FoF, Solution) with a per-tile
  **Gainers/Losers toggle (default Gainers)** showing top-5 for the selected
  period (1D/1W/1M/3M/1Y), above the existing filters. Backed by
  `GET /api/movers/categories` — one window scan (same semantics as
  `/api/movers`), grouped in Python by `queries.fund_family()` which maps
  AMFI's mixed current/legacy `scheme_category` strings to families.
- **Variant collapsing:** plan/option variants of one scheme (GROWTH/IDCW, and
  multiple IDCW payout periodicities — each its own AMFI code) move
  identically and would stack the list (e.g. 3× "BANK OF INDIA ARBITRAGE
  FUND"). `category_movers` collapses them onto one row per
  `mf.fund_variants` family: the biggest absolute mover is the representative
  (GROWTH wins ties), with a `variants` count rendered as a "×N variants"
  badge. Tile fund counts are distinct schemes (Hybrid 444 → 189).
- Known data artifact (pre-existing, same in `/api/movers`): matured close-ended
  FMPs show their NAV reset-to-par at maturity as a large "loser" (e.g. an HDFC
  FMP that matured within the window).

## UI: fund-detail page reorganized into sections (done, 2026-10-08)

The fund page was one flat column of 15 equal-weight cards (~5,167px tall) with
no way to jump, and the most basic facts (AUM, expense ratio, benchmark,
manager, inception, risk) were buried ~75% down, *below* the SIP calculator and
two returns charts. Reorganized into **five labelled sections** with a **sticky
section nav** (all 15 components kept):

- **Overview** — a compact **key-facts strip** (AUM, expense ratio, benchmark,
  fund manager, inception, risk) moved to the top, then the NAV chart (hero)
  and the 1M–5Y returns chips.
- **Performance** — returns breakdown (calendar-year bars + month-by-year
  heatmap) and rolling returns.
- **Risk & Peers** — risk & behaviour (drawdown/volatility/Sharpe/Sortino/
  Calmar/win-rate), category standing, category risk-reward map, debt profile.
- **Holdings** — holdings analysis (asset-class + sector donuts) and top
  holdings.
- **Details & tools** — **collapsed by default** behind a toggle: the full
  24-field key-facts grid, the SIP/lumpsum projection calculator, expense-ratio
  history, and plan variants.

Mechanics: the nav is `position: sticky` under the topbar (offset 66px) and
highlights the section in view via a scroll-spy (`fund.js#setupFundNav`); nav
links jump using CSS `scroll-behavior: smooth` + `scroll-margin-top` on each
section (no click handler needed). Because the SIP + expense-ratio charts are
created while their section is `hidden` (0 width), `setupDetailsToggle` calls
`chart.resize()` on expand so they render at full size. Verified headlessly:
nav/sections/strip present, scroll-spy tracks Overview→Risk→Holdings, nav
click scrolls, Details collapse/expand resizes the charts, and dark mode is
clean. Default page height drops 5,167px → 3,853px (Details collapsed).

## API: batch fund lookup (done, 2026-10-08)

- `GET /api/funds/batch?ids=100033,INF209K01LV0,999999` — one comma-separated
  list mixing **AMFI scheme codes and ISINs**, max **50** per request (51+ →
  422, empty → 422). Numeric inputs are codes; anything else is upper-cased and
  matched against either ISIN column. Returns funds in request order,
  de-duplicated, each tagged with `matched_by` (every input that resolved to it)
  plus a `not_found` list of inputs that resolved to nothing. Per fund: identity
  + `isin_primary`, latest NAV + date, AUM / expense ratio / 5y return / sharpe
  / beta / risk level / manager / benchmark, `in_scope`, `is_defunct`.
- Registered **before** `/api/funds/{code}` — FastAPI matches in registration
  order and "batch" would 422 the int `{code}` parameter otherwise.

## API: variant-group facts fallback + family default (done, 2026-10-09)

**Why:** same-named schemes exist under multiple AMFI codes (re-issues), and
AMC factsheets attribute fund-level facts to only *some* of the codes in a
`fund_variants` group. E.g. "Franklin India Liquid Fund" has 13 codes; facts
live on `100538`/`100546`/`100547`/`100548` but not on the newer `139889`
tranche, so `/fund/139889` rendered a blank facts sheet even though the fund
itself was fully enriched. The fund list also showed all 13 rows.

- `GET /api/funds/{code}`: when the exact code has no usable fund-level facts
  (no row, or a row where AUM / expense ratio / benchmark / benchmark_name /
  manager / inception are all NULL), the facts are borrowed from a sibling
  restricted to re-issues of the **same plan/option/periodicity within the same
  AMC and scheme category** (lowest AMFI code among those). `group_key` alone
  is not trusted: it is a documented heuristic that can collide across AMCs,
  and cross-plan/option/periodicity borrowing would leak variant-specific
  facts. Only the **family-safe fund-level fields** (AUM, expense ratio,
  benchmark, manager, inception, classification, ratings) are borrowed; per-
  scheme fields (SIP, transaction status, exit load, published returns, risk
  ratios) always stay the code's own. The response gains `facts_source_code`
  (borrowed code, `null` when the code has its own facts).
- `GET /api/funds/batch`: same fallback per code, resolved in one batched
  window-function query (no N+1); each fund gains `facts_source_code`. The
  "has its own facts" test is one shared predicate (`_FACTS_IDENTITY_FIELDS`)
  across detail, batch, and both sibling SQL filters, so the endpoints agree.
  The batch borrow is restricted to the family-safe subset of its fact keys
  (`_BATCH_BORROW_FACTS_KEYS`), so `return_5year`/`sharpe_ratio`/`beta` —
  per-code NAV-series metrics — are never inherited; and `benchmark_name` is
  projected as `COALESCE(benchmark_name, benchmark)` (own row and borrowed
  row), so a raw-benchmark-only row is treated as complete AND still surfaces
  its benchmark.
- Fund list defaults to scheme-family grouping: the index page now uses
  `/api/fund-families` by default (one row per family, `+N variants` badge,
  representative = GROWTH/REGULAR lowest code). The family identity is
  `group_key` **plus AMC and scheme classification**, so same-named funds from
  different AMCs stay separate and `variant_count` never mixes AMCs. The
  "Group by scheme family" toggle and `?family=0` opt back into per-variant
  rows. `/api/funds` itself is unchanged — de-dup is served at the API level,
  not the UI.
- Fund detail shows a disclosure note in the Details section when facts are
  borrowed: "Fund-level facts are shown from sibling scheme #… (same fund
  family)".
- Verified: `/fund/139889` (REG/GROWTH) shows AUM ₹6,082 cr, ER 0.20%,
  benchmark, manager, inception, risk (borrowed from `100538`, a same
  plan/option re-issue) while keeping its **own** Sharpe and returns and
  **not** inheriting `100538`'s exit load / SIP; a Direct plan of the same
  family does **not** borrow Regular-plan facts; a Monthly IDCW does **not**
  borrow a Quarterly sibling's facts.
- Tests: `tests/test_variant_facts_fallback.py` — safe on populated DBs
  (900xxx code range, single always-rolled-back transaction, fails if the
  identifiers pre-exist; verified zero row-count change on the 14,368-scheme
  dev DB): exact-code unchanged, same-plan/option re-issue picked, Direct never
  borrows Regular (detail + batch), code-specific fields (SIP / exit load /
  returns) never inherited, all-NULL row treated as missing, sibling usable via
  expense_ratio alone, own per-code Sharpe survives the borrow, Monthly IDCW
  never borrows a Quarterly sibling (detail + batch), same-named funds from
  different AMCs stay separate families, batch never inherits per-scheme
  performance (Sharpe/return_5year/beta), a raw-benchmark-only row is treated
  as complete and surfaced via benchmark_name (COALESCE) in batch, no-group
  no-facts stays blank, rollback leaves no rows.

## API: plan scope — Regular is the served universe (done, 2026-10-10)

**Why:** this data feeds a distributor-facing product (TrillionInsights
partner research). Direct plans are a different commission model, not a product
choice, so a partner must never be *offered* one — yet the fund-detail sibling
navigation listed all three Direct variants of a scheme next to the Regular
ones, and any consumer that disabled `in_scope` (or typed a Direct code into the
typeahead) could surface them.

`mf.funds.in_scope` is a STORED generated column
(`plan_type = 'REGULAR' OR (plan_type = 'UNLABELLED' AND NOT is_etf AND
plan_source = 'NAME')`) that already excludes Direct, so **no existing response
changed** — but `in_scope` is a *curation* flag that `/api/funds` exposes as an
option and the sibling list does not apply at all. "We never show Direct" now
states itself instead of riding on an expression defined for another purpose.

- New policy in `queries.py`: `DEFAULT_PLAN_SCOPE = "regular"`, a closed
  `_PLAN_SCOPE_SQL` map of scope → SQL predicate template (`regular` → the
  served-plan rule, `direct` → `plan_type = 'DIRECT'`, `all` → `TRUE`),
  `plan_scope()` (normalises, raises `ValueError` on anything unknown) and
  `_plan_predicate()` (binds `{alias}` from call-site constants only, never from
  input). `RETAIL` / `INSTITUTIONAL` are excluded: a distributor sells neither.
- `plan` query parameter (default `regular`) on the discovery and navigation
  routes: `/api/funds`, `/api/fund-families`, `/api/funds/{code}` (scopes the
  `siblings` list), `/api/suggest`, `/api/movers`, `/api/movers/categories`.
  Resolved by `_plan_scope_or_422` **before** any DB work, so a typo is a 422
  rather than a silently widened or narrowed universe.
- Always scoped (no parameter, because widening them would be a footgun): peer
  sets in `/api/funds/{code}/peers` and `/risk-reward` (a percentile must not
  compare a Regular fund against Direct peers on the same book), and the facet
  counts `/api/amcs`, `/api/categories`, `/api/options` (a count must never
  promise rows the list will not return).
- Deliberately **not** filtered: explicit-identifier lookups —
  `/api/funds/{code}`, `/nav`, `/returns`, `/analytics`, `/api/funds/batch`,
  `/api/compare`, `/api/holdings-overlap`. A code is an identifier, and hiding
  one the caller named would be a lie; they all return `plan_type` so the
  consumer can badge it. The consequence is a useful one: opening a Direct code
  still works, and every hop it offers leads back into the served universe.
- **Typeahead ranking bug found and fixed while here:** the exact-match sort key
  was `(code = q OR isin_primary = q)`. `isin_primary` is NULL for schemes
  without an ISIN, `FALSE OR NULL` is NULL, and Postgres sorts NULL **first**
  under `DESC` — so all 19 live ISIN-less codes (every FMP / close-ended
  re-issue) jumped above far more relevant matches on every query. That is
  exactly why "hdf" offered *HDFC FMP 1269D March 2023* before *HDFC Aggressive
  Hybrid Fund*. Wrapped in `coalesce(..., false)`, plus a final
  `amfi_scheme_code` tie-break so same-named variants no longer come back in
  arbitrary order.
- Verified live against the 14,368-scheme dev database: `/api/suggest?q=hdf`
  now leads with HDFC Aggressive Hybrid Fund / Arbitrage / Balanced Advantage
  (all Regular); `/api/funds/151566` returns 3 siblings (was 6, incl. 3 Direct);
  `/api/funds/151567` (Direct) still resolves but offers only its Regular twins;
  `/api/funds?in_scope=false` returns 9,873 rows with zero Direct where
  `plan=all` returns 14,127; `plan=bogus` → 422. Totals for the served universe
  are identical before/after (4,291 funds, 1,821 families).
- Tests: `tests/test_plan_scope.py` (populated-DB safe: 9002xx range, AMC
  900002, one always-rolled-back transaction, fails if identifiers pre-exist) —
  default lists no Direct/Retail even with `in_scope=False`, `plan=all` widens,
  `plan=direct` narrows, blank-plan feed row is not pulled back in, family view
  is Regular-represented with `variant_count` from served variants only,
  siblings exclude Direct (and include it only when asked), a Direct page offers
  Regular twins, suggest never returns Direct and cannot resolve a Direct code,
  the ISIN-less scheme no longer ranks first, ordering is deterministic, movers
  and category movers honour the scope where `in_scope` alone would not, facet
  counts use the served scope, **populated-DB zero-change guard**, rollback
  leaves no rows. Pure policy + 422 tests in `tests/test_queries.py`;
  OpenAPI documentation of `plan` asserted in `tests/test_openapi_contract.py`.
- Contract: `contracts/mfdataindia-openapi-v1.json` re-exported (66 added lines
  = the `plan` parameter on six routes; no path or response-shape change, so
  consumers that do not send `plan` are unaffected).

**Review round 1 — [P1] the scope admitted plan-*unknown* rows (fixed same day).**
`regular` was written as `plan_type IN ('REGULAR', 'UNLABELLED')`, but
`UNLABELLED` is not one population. `resolve_plan` (`amfi_navall.py`) returns it
for three genuinely different reasons — `NAME` (legacy feed had *no* Plan column,
so the name is the only signal and unlabelled means "written without the word
Regular"), `COLUMN_BLANK` (the feed **has** a Plan column and left it empty: the
plan is unknown) and `COLUMN_UNRECOGNISED` (a Plan cell we cannot classify) — and
the rule additionally exempts ETFs. Measured on the live feed, the shortcut
admitted **10,050 rows where the authoritative rule admits 4,345**: 5,705
`UNLABELLED + COLUMN_BLANK`, 5,573 of them still live. It doubled the served
universe, and it surfaced exactly where this PR deliberately bypasses curation
(`/api/funds?in_scope=false`, sibling navigation) — which also explains why the
zero-change guard never caught it: every *default* response already filters
`in_scope`, so the column masked the predicate's own error.

Fix: `_SERVED_PLAN_SQL` reproduces the generated column verbatim. Three guards
keep the two definitions from drifting, because the scope intentionally cannot
read `in_scope`:

1. `test_regular_scope_admits_exactly_what_curation_admits` evaluates the emitted
   predicate against the column with `IS DISTINCT FROM` over every row visible in
   the test transaction — the synthetic group on CI, all 14,368 rows locally.
   This is the behavioural guard.
2. `test_regular_scope_predicate_matches_the_generated_column_text` reads the
   `GENERATED ALWAYS AS (…) STORED` expression out of `sql/001_core_schema.sql`
   and compares it with the test's own independent transcription of the rule.
3. The fixture now carries one row per UNLABELLED sub-case — `900205`
   COLUMN_BLANK, `900209` COLUMN_UNRECOGNISED, `900208` NAME-but-ETF — plus
   `900210` REGULAR-but-ETF to prove the carve-out was not over-applied. Listing,
   typeahead and sibling navigation now assert the exact served code set, in both
   `in_scope=True` and `in_scope=False` modes.

Negative control: reverting the predicate to the reviewed-against form fails
exactly three tests — the parametrised `in_scope=False` listing, sibling
navigation, and the equivalence guard — so the new assertions are proven to catch
the reported bug rather than merely passing alongside it.

## Schema: `apply_migrations` now equals a fresh compose boot (done, 2026-10-08)

- `DEFAULT_MIGRATIONS` was missing 008 and 010–013, so any DB built via
  `apply_migrations` (the integration-test path) lacked `mf.fund_family` and
  still carried the purged aggregator columns + `fund_opinions` — i.e.
  non-compliant. All current migrations (except superseded 007) are now core,
  so any such DB ends in exactly the same schema as a fresh compose boot
  (whose initdb runs every file in `sql/`).
- The stale `test_migrations_are_idempotent` (hardcoded 3-file list; a full
  re-apply is not a supported operation once the one-way purges 012/013 exist)
  was replaced with a test that a fresh apply yields the complete post-purge
  schema (fund_family present, every aggregator column/table gone, key views
  intact).

---

## Cross-cutting (not source-specific)

- **CI:** GitHub Actions running the unit suite on push (none today).
- **Monitoring:** data-freshness alert (weekday with `max(nav_date)` > 2 days
  old) + uptime check + structured logs (prod plan).
- **Backups:** nightly `pg_dump -Fc` off-box + periodic restore test (prod
  plan).

> Deployment specifics (host, Dockerfile, compose, TLS, schedulers, backups)
> live in the prod-deployment plan, not here — this file is the data /
> improvement roadmap.
