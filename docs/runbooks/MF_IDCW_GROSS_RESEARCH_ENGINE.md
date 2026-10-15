# IDCW gross research total-return engine — version 1

## Approved refinement and finite deliverable

On 2026-10-16 the user approved proceeding without depending on a bespoke HDFC
response. The original source-readiness report remains unchanged and blocked.
It is evidence about the earlier scope, not a permanent bar on implementing
research mathematics. Public issuer/AMFI disclosures remain the acquisition
strategy; an issuer reply is optional corroboration, not a blanket prerequisite.

This delivery implements **actual, deterministic fund-research arithmetic** in
`mfdataindia.analytics.idcw_research`, with an immutable executable policy and a
[stored policy document](../policies/idcw-gross-research-total-return-v1.json).
It is not another source-readiness checker. It computes a separate reinvested
wealth index, observed-period returns, cumulative return, CAGR and observed
maximum drawdown for the declared input set. Independent exact-rational units
fixtures verify the arithmetic. Synthetic fixtures never certify a real fund.

It is an **internal pure calculation engine**, not a publicly consumable API,
database publication path or deployed analytics feature. Source admission and
current result selection remain unimplemented. No schema, IAM, API contract,
ingestion/scheduler, runtime gate, UI or existing calculation is changed.

## Research return versus investor outcome

The method assumes hypothetical, frictionless reinvestment of the gross
distribution per existing unit at an explicitly mapped, same-day ex-distribution
NAV. Personal TDS/tax, investor stamp duty, loads, order cutoffs, payment delays
and actual units allotted are excluded. Actual allotment records and personal
tax details are **not required for this research method**. They would be inputs
to a separate investor cash-flow/execution simulation, not this wealth index.

NAV already reflects scheme costs. Do not infer a levy from a price drop, add
back expenses, or subtract investor stamp duty from a gross research payout.
A separately quantified scheme levy is not a universal prerequisite if the
authoritative gross cash distribution and corresponding ex-distribution NAV are
established. If a published amount's gross/net or per-unit basis is ambiguous,
that event remains unsupported; naming it gross in a request does not resolve it.

### Exact method

For a window bounded by actual supplied observations, start with one unit:

```text
q[0] = 1
q[t] = q[t-1] * (1 + D[t] / NAV[t])    on an admitted event valuation date
q[t] = q[t-1]                         otherwise in the declared event set
wealth_index[t] = 100 * q[t] * NAV[t] / NAV[0]
period_return[t] = ((NAV[t] + D[t]) / NAV[t-1] - 1) * 100
cumulative_return = (wealth_index[end] / 100 - 1) * 100
CAGR = ((wealth_index[end]/100)^(365.25/elapsed_days) - 1) * 100
drawdown[t] = (wealth_index[t]/running_peak[t] - 1) * 100
maximum_drawdown = min(drawdown[t])
```

Only events strictly after the first observation and on/before the last are
accepted. Starting NAV is already post any event on its own date; adding that
distribution again would double count it. One consolidated issuer event per
date is supported; the engine rejects duplicate dates rather than adding them
without an authoritative consolidated basis. Event effective date must have an
actual supplied NAV equal to the explicitly declared ex-distribution NAV.
No record date, payment date or next available observation is automatically
treated as that price. No amount is inferred from NAV differences.

CAGR requires at least 365 elapsed calendar days and uses ACT/365.25, as stated
in this policy. Maximum drawdown describes the supplied observations only, not
an asserted complete daily series. Sharpe/Sortino/benchmark/rolling metrics,
delayed reinvestment and fund-unit corporate actions are not implemented.
Known unit actions withhold the entire result, not just the affected point.

Decimal arithmetic uses a fresh precision-50 HALF_EVEN context, not the caller's
context. Internal units/wealth retain that precision; eight-place output values
are display/serialization only, never fed back into calculations. Fixed bounds
and explicit unavailable results replace NaN, infinity and partial-series output.
No raw NAV row or imputed observation is generated or written.

## Inputs, outputs and trust boundary

`ResearchInput` requires exact decimal text, chronological unique NAV/event
dates, exact option/plan/code, declared identity/dataset/source revisions and
source hashes for every NAV, distribution and ex-NAV mapping. It accepts no
coverage/eligibility override. Explicit conflicts, unresolved unit actions,
missing event-date prices, mismatched prices or an observed gap exceeding seven
calendar days produce no numeric result. Seven-day gaps are only a conservative
arithmetic boundary, not proof that all trading observations/events are present.

The engine does **not authenticate declared source hashes, identity or facts**.
Even complete-looking or empty event sets always return:

```json
{
  "publication_status": "unqualified_engine_output",
  "coverage_status": "not_certified",
  "analytics_eligible": false
}
```

`calculation_status: computed` means only that arithmetic succeeded for the
declared inputs. It must never be used as an admission/certification flag. A
result contains the complete normalized declared input, policy, code/dependency
and runtime identity, input hash and result hash; no clock/random state. Source
revisions create new identities; no previous artifact is replaced. The API and
existing stores do not call this module or publish its outputs.

## What still blocks HDFC 101763 / FY2025–26

The retained official observations are useful, not discarded. They still do not
resolve the actual event's ex-distribution date/NAV mapping: March 5 AMFI NAV
79.808 matches the reported cum-IDCW NAV, while March 6 is 71.772. Neither price
can be selected as ex-NAV simply because it follows a payout or a drop. No
HDFC return is produced from these unresolved inputs. See the unchanged
[source checkpoint](MF_IDCW_CERTIFIED_WINDOW_PLAN.md#implemented-source-checkpoint--2026-10-15).

Next acquisition/admission work should use the bounded public historical
distribution query, dated notices, applicable scheme documents/addenda and
original NAV responses. Record source-defined date/amount/price conventions,
pagination/truncation checks, corrections and fund-unit-event review. A written
AMC assurance or personal allotment statement is not a prerequisite; a guessed
mapping or operator `coverage_complete: true` is not a substitute for evidence.
If public sources do not establish the essential facts for this window, keep
that window unavailable and pursue other supported windows/funds independently.

Before private publication: implement trusted per-window evidence admission;
current lifecycle/quality and source-revision invalidation; immutable sidecar
persistence and atomic result selection; restricted reads; and fresh backup,
restored-clone preservation, independent real-source reference, private route
certification and actual recovery. Existing IDCW restrictions stay unchanged
until those gates pass. Do not deploy a validation-only schema/profile.

## Verification

```sh
uv run pytest tests/test_idcw_research.py -q
uv run pytest -m 'not postgres' -q
uv run ruff check src scripts tests
```

The focused tests cover independent exact cash/units arithmetic, no payouts,
pure-distribution and genuine market losses, repeated/terminal events, annual
day counts, gaps, conflicts/unit actions, identity/revision changes, invalid
decimal/date/source/plan/basis inputs, bounds, hostile ambient decimal settings,
policy tampering, replay/hash closure and fail-closed numeric range handling.
Full PostgreSQL, contract and image CI remains a merge gate. None of these
synthetic checks establishes source completeness or live DEV acceptance.
