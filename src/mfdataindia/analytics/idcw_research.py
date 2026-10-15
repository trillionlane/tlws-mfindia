"""Gross IDCW research arithmetic, NOT event admission or publication authority.

All hashes/identity/evidence in this interface are declared inputs. Calculation
does not authenticate them or certify a complete distribution/unit-event window.
No production caller or HTTP route uses this engine until separate admission,
publication and preservation-first certification are implemented.
"""

from __future__ import annotations

import hashlib
import platform
from copy import deepcopy
from datetime import date
from decimal import (
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    ROUND_HALF_EVEN,
    localcontext,
)
from decimal import __libmpdec_version__
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from mfdataindia.review.policy import fingerprint

ENGINE_VERSION = "idcw-gross-research-total-return-1"
_POLICY = {
    "id": "idcw-gross-research-total-return",
    "version": 1,
    "engine_version": ENGINE_VERSION,
    "option": "IDCW",
    "basis": "gross fund research, not investor execution or net cash-flow return",
    "distribution": "issuer-confirmed INR per pre-event unit, before personal tax; NAV already reflects scheme costs; never infer or add back a levy",
    "reinvestment": "hypothetical frictionless reinvestment at explicitly mapped same-day ex-distribution NAV; no actual allotment assumption",
    "event_date": "explicit ex-distribution valuation date; never select record/payment/next date by inference",
    "events_in_window": "start exclusive, end inclusive; first NAV is post any start-date distribution; input must omit start/outside events",
    "ordering": "one consolidated issuer event per date; all same-day distribution reinvested after ex-distribution valuation",
    "units": "q[0]=1; q[t]=q[t-1]*(1+D[t]/NAV[t]) on event dates, otherwise unchanged",
    "wealth_index": "100*q[t]*NAV[t]/NAV[0]; no raw NAV rewrite or stored imputation",
    "period_return": "((NAV[t]+D[t])/NAV[t-1]-1)*100; D=0 only within the DECLARED input event set, not proof of no payout",
    "cumulative_return": "(q[end]*NAV[end]/NAV[0]-1)*100",
    "cagr": "(q[end]*NAV[end]/NAV[0])^(365.25/elapsed_calendar_days)-1, in percent; at least 365 days",
    "max_drawdown": "min(wealth[t]/running_max(wealth)-1)*100 over supplied observations; includes initial zero drawdown",
    "coverage": "not certified by this engine; a short NAV gap or an empty event set does not establish completeness",
    "eligibility": "always false here; trusted per-fund/window admission, current quality/lifecycle and atomic publication required separately",
    "missing_or_conflicting": "no numeric output for explicit conflicts, unsupported unit events, event/NAV mismatch, missing event-date NAV or observed gap >7 days",
    "unit_events": "unsupported; never silently adjust a split, merger, bonus, face-value or unit-basis change",
    "precision": 50,
    "rounding": "ROUND_HALF_EVEN",
    "decimal_exponent_limits": [-999999, 999999],
    "decimal_traps": ["InvalidOperation", "DivisionByZero", "Overflow"],
    "output_decimal_places": 8,
    "maximum_observations": 30000,
    "maximum_events": 600,
    "maximum_window_calendar_days": 36600,
    "maximum_observed_gap_calendar_days": 7,
    "lineage": "complete normalized declared input + immutable policy hash + engine code and dependency hashes + runtime; no wall clock",
    "persistence": "immutable policy in git; pure arithmetic only, no source/result/database writes or publication",
    "exclusions": [
        "personal tax/TDS, investor stamp duty, loads, order cutoffs and actual units allotted",
        "delayed reinvestment or unreinvested cash ledger",
        "unit-event adjustment",
        "Sharpe, Sortino, alpha, beta, benchmark and rolling metrics",
        "coverage certification, current eligibility, API/UI activation and investment advice",
    ],
}

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Money = Annotated[str, Field(pattern=r"^[0-9]{1,9}(?:\.[0-9]{1,8})?$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Nav(Model):
    date: str = Field(min_length=10, max_length=10)
    nav_inr: Money
    source_hash: Hash


class Event(Model):
    effective_date: str = Field(min_length=10, max_length=10)
    distribution_basis: Literal["gross_before_personal_tax"]
    reinvestment_basis: Literal["explicit_same_day_ex_distribution_nav"]
    distribution_inr_per_unit: Money
    ex_distribution_nav_inr: Money
    distribution_source_hash: Hash
    mapping_source_hash: Hash


def _day(value):
    if not isinstance(value, str):
        raise ValueError("ISO date text required")
    day = date.fromisoformat(value)
    if day.isoformat() != value or not 1900 <= day.year <= 2100:
        raise ValueError("bounded canonical ISO date required")
    return day


class ResearchInput(Model):
    format: Literal["idcw-gross-research-input-v1"]
    code: StrictInt = Field(gt=0, le=2147483647)
    plan: Literal["REGULAR", "DIRECT"]
    option: Literal["IDCW"]
    identity_hash: Hash
    dataset_version: StrictInt = Field(gt=0, le=9223372036854775807)
    source_revision_hash: Hash
    source_hashes: list[Hash] = Field(min_length=1, max_length=1200)
    observations: list[Nav] = Field(min_length=2, max_length=30000)
    events: list[Event] = Field(max_length=600)
    # Explicit blockers, not declarations that their absence proves completeness.
    unresolved_unit_event_dates: list[str] = Field(max_length=600)
    source_conflicts: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        max_length=100
    )

    @field_validator("source_conflicts")
    @classmethod
    def safe_conflicts(cls, value):
        if any(any(ord(c) < 32 for c in item) for item in value):
            raise ValueError("control characters not accepted")
        return value

    @model_validator(mode="after")
    def bounded(self):
        days = [_day(p.date) for p in self.observations]
        if any(b <= a for a, b in zip(days, days[1:])):
            raise ValueError("unique chronological NAV observations required")
        if (days[-1] - days[0]).days > _POLICY["maximum_window_calendar_days"]:
            raise ValueError("research window budget exceeded")
        event_days = [_day(e.effective_date) for e in self.events]
        if any(b <= a for a, b in zip(event_days, event_days[1:])):
            raise ValueError("unique chronological consolidated events required")
        if any(not days[0] < d <= days[-1] for d in event_days):
            raise ValueError("events must be start exclusive and end inclusive")
        unit_days = [_day(d) for d in self.unresolved_unit_event_dates]
        if len(unit_days) != len(set(unit_days)) or any(
            not days[0] <= d <= days[-1] for d in unit_days
        ):
            raise ValueError("unique in-window unit-event dates required")
        sources = set(self.source_hashes)
        if len(sources) != len(self.source_hashes):
            raise ValueError("unique source hashes required")
        referenced = {p.source_hash for p in self.observations} | {
            h for e in self.events for h in (e.distribution_source_hash, e.mapping_source_hash)
        }
        if not referenced <= sources:
            raise ValueError("every observation/event must reference a declared source")
        values = [p.nav_inr for p in self.observations] + [
            v for e in self.events for v in (e.distribution_inr_per_unit, e.ex_distribution_nav_inr)
        ]
        if any(Decimal(v) <= 0 for v in values):
            raise ValueError("positive bounded exact prices/distributions required")
        return self


def policy():
    return deepcopy(_POLICY)


def validate_policy(document):
    if fingerprint(document) != fingerprint(_POLICY):
        raise ValueError("unsupported IDCW research policy; introduce a new tested version")
    return fingerprint(document)


def engine_identity():
    from mfdataindia.review import policy as canonical_policy

    return {
        "version": ENGINE_VERSION,
        "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "canonical_dependency_hash": hashlib.sha256(Path(canonical_policy.__file__).read_bytes()).hexdigest(),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "decimal_library": __libmpdec_version__,
        "pydantic": version("pydantic"),
    }


def _text(value):
    result = value.quantize(Decimal("0.00000001"))
    return format(abs(result) if result == 0 else result, "f")


def derive(raw, document):
    """Return hash-bound arithmetic only. Never sufficient for publication/admission.

    Revalidate even model instances: frozen models can contain mutable lists.
    Malformed inputs raise ValueError; factual/coverage blockers return no values.
    """
    policy_hash = validate_policy(document)
    request = ResearchInput.model_validate(
        raw.model_dump(mode="json") if isinstance(raw, ResearchInput) else raw
    )
    normalized = request.model_dump(mode="json")
    days = [_day(p.date) for p in request.observations]
    by_date = {p.date: p for p in request.observations}
    reason = None
    if request.source_conflicts:
        reason = "source_conflicts"
    elif request.unresolved_unit_event_dates:
        reason = "unsupported_unit_events"
    elif any(e.effective_date not in by_date for e in request.events):
        reason = "event_date_nav_missing"
    elif any(
        Decimal(e.ex_distribution_nav_inr) != Decimal(by_date[e.effective_date].nav_inr)
        for e in request.events
    ):
        reason = "event_ex_nav_mismatch"
    elif any((b - a).days > 7 for a, b in zip(days, days[1:])):
        reason = "observed_nav_gap_exceeds_policy"
    result = {
        "format": "idcw-gross-research-calculation-v1",
        "code": request.code,
        "plan": request.plan,
        "option": "IDCW",
        "start": days[0].isoformat(),
        "end": days[-1].isoformat(),
        "policy_hash": policy_hash,
        "policy": deepcopy(document),
        "input_hash": fingerprint(normalized),
        "declared_input": normalized,
        "engine": engine_identity(),
        "calculation_status": "unavailable" if reason else "computed",
        "unavailable_reason": reason,
        "publication_status": "unqualified_engine_output",
        "coverage_status": "not_certified",
        "analytics_eligible": False,
        "points": [],
        "summary": None,
    }
    if reason is None:
        try:
            result["points"], result["summary"] = _calculate(request, days)
        except DecimalException:
            result.update(
                calculation_status="unavailable",
                unavailable_reason="decimal_range_unavailable",
                points=[],
                summary=None,
            )
    result["result_hash"] = fingerprint(result)
    return result


def _calculate(request, days):
    # Fixed, independent Context (not copied caller precision/traps/exponents).
    with localcontext(Context(
        prec=50, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999,
        capitals=1, clamp=0, flags=[], traps=[InvalidOperation, DivisionByZero, Overflow],
    )):
        initial = Decimal(request.observations[0].nav_inr)
        events = {e.effective_date: e for e in request.events}
        units, peak, worst = Decimal(1), Decimal(1), Decimal(0)
        previous = initial
        points = []
        for index, point in enumerate(request.observations):
            nav = Decimal(point.nav_inr)
            event = events.get(point.date)
            amount = Decimal(event.distribution_inr_per_unit) if event else Decimal(0)
            if event:
                units *= 1 + amount / nav
            wealth = units * nav / initial
            peak = max(peak, wealth)
            drawdown = wealth / peak - 1
            worst = min(worst, drawdown)
            points.append(
                {
                    "date": point.date,
                    "raw_nav_inr": point.nav_inr,
                    "distribution_inr_per_unit": event.distribution_inr_per_unit if event else None,
                    "wealth_index": _text(wealth * 100),
                    "period_return_pct": _text(((nav + amount) / previous - 1) * 100) if index else None,
                    "drawdown_pct": _text(drawdown * 100),
                }
            )
            previous = nav
        elapsed = (days[-1] - days[0]).days
        annualized = (
            _text(((wealth.ln() * (Decimal("365.25") / elapsed)).exp() - 1) * 100)
            if elapsed >= 365
            else None
        )
        return points, {
            "cumulative_return_pct": _text((wealth - 1) * 100),
            "cagr_pct": annualized,
            "cagr_unavailable_reason": None if elapsed >= 365 else "less_than_365_elapsed_days",
            "max_drawdown_pct": _text(worst * 100),
            "elapsed_calendar_days": elapsed,
            "observations": len(points),
            "declared_events": len(events),
            "largest_observed_gap_calendar_days": max((b - a).days for a, b in zip(days, days[1:])),
            "sampling": "supplied_observations_not_certified_daily_continuity",
        }
