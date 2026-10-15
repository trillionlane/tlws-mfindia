# Data and analytics representation boundary

User direction recorded 2026-10-15. This governs backend data, API semantics,
release evidence and later consumer handover. It does not authorize UI work.

## Evidence is not a product claim

Keep these levels separate: synthetic validation; independent source reference;
implemented code; CI; merged code; deployed runtime; bounded real-source provider
certification; consumer/business acceptance; whole-universe readiness. Each needs
its own evidence. A fixture, reference calculation, successful job, merged PR or
single certified pair is not evidence for the later levels.

Every handover must state the fund/cohort, source date, supported window, policy
version/hash, result/input lineage, exclusions and unavailable reasons. Report
what is implemented and what was actually exercised, including negative cases.
No test-only or diagnostic record may silently enter an accepted dataset or API
publication. Samples do not prove all-AMC, all-fund, current-month or production
coverage. Missing, censored, unresolved and not-assessed are not zero, safe or
complete. A human review does not make missing source evidence authoritative.

## Observed holdings overlap (MF-E08)

The current `reported-isin-weighted-overlap` v1 is a real, bounded metric on
accepted original portfolio disclosures. Its independent reference pair and
certification checks validate that scope, not a broader product capability.

- It sums minimum reported weights for matching eligible ISINs. Cash, unresolved
  identifiers, censored weights and derivative exposures have explicit exclusions.
- Eligible weight sums are not a data-completeness percentage. Do not normalize
  away exclusions, or interpret partial HHI as complete portfolio risk.
- Full economic overlap remains unavailable **by policy**, even if a later input
  has more exposure detail. It would need a separately reviewed, versioned method
  covering issuer/instrument relationships and supported look-through exposure.
- Show/report observed securities overlap, the actual disclosure date and the
  exclusions. Do not call it full economic overlap, a complete diversification
  score, risk certification or a recommendation.

## IDCW evidence (MF-E09)

Discovery observations are not accepted distribution events. Conditional
announcements and reported historical amounts stay separate. Record dates,
ex-dates, payment dates and applicable reinvestment dates/prices are distinct.
Do not convert cum-IDCW/pre-notice NAV into reinvestment NAV or generic levy
wording into a zero charge. A summary history does not automatically prove a
complete calendar window. An empty selection remains unknown, not no payout.

Only complete, identity-bound accepted evidence may support a later versioned
total-return policy and certified fund/window/metric combination. Raw NAV and old
results remain intact. E09 discovery cannot enable E10 calculations or E11 API
eligibility. Provider and consumer representation changes stay separate.

## Release review checklist

Before declaring completion, reconcile roadmap scope, actual source coverage,
stored policy, deployed contract and certificate observations. Identify any
field/route/scenario built only for validation. Do not label it a customer feature
without an independently supported business purpose and acceptance gate. If
evidence is incomplete, publish the limitation in the handover and keep the
affected capability unavailable. Never substitute mock data to pass the gate.
