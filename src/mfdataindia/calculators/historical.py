"""Observed Growth cash flows, exact rational units, bounded ACT/365 XIRR."""

import calendar
import hashlib
import platform
from bisect import bisect_left, bisect_right
from copy import deepcopy
from datetime import date
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext, __libmpdec_version__
from fractions import Fraction
from pathlib import Path

from mfdataindia.calculators import historical_schemas as schemas
from mfdataindia.review.policy import fingerprint

ENGINE_VERSION = "historical-growth-cashflow-1"
_POLICY = {
    "id": "historical-growth-observed-cashflows",
    "version": 1,
    "engine_version": ENGINE_VERSION,
    "option": "GROWTH",
    "eligibility": "current trusted signal-free assessment and current methodology eligibility; not retrospective eligibility inference",
    "source": "exact complete already-held NAV/identity/context/source/dataset snapshot; no writes or imported prices",
    "purchase_date": "first actual observation on or after requested cash-flow date, <=requested end; never use earlier price or look past end",
    "maximum_purchase_shift_calendar_days": 7,
    "sip_schedule": "day_of_month 1..31 in each Gregorian month, clamp to month end; include only target dates inside inclusive range",
    "funds_availability": "cash is available for that next-observation purchase; this is not an AMC execution/cutoff quote",
    "terminal_date": "last actual observation <=end, lag <=7 calendar days; requested end must not exceed captured last observation",
    "coverage": "all source observations between first purchase and terminal; any observed gap >7 days withholds the entire simulation; not a licensed trading-calendar completeness assertion",
    "imputation": "none; no NAV rows/forward fills fabricated or stored",
    "units_formula": "exact rational contribution_paise/(100*NAV_INR); sum exact rational units; no intermediate unit rounding",
    "terminal_formula": "sum_units*terminal_NAV_INR*100; round exact rational HALF_EVEN to one paise once",
    "period_gain": "(rounded_terminal_paise/invested_paise-1)*100; rounded monetary ledger basis",
    "units_display": "12 decimal places HALF_EVEN, display only; never fed back to calculation",
    "cashflows": "negative integer contributions on actual purchase dates and one rounded positive terminal amount on actual valuation date; aggregate same dates",
    "xirr_day_count": "ACT/365",
    "xirr_equation": "terminal-sum(contribution*(1+r)^((valuation_date-purchase_date).days/365))=0",
    "xirr_class": "require one sign change after date aggregation; multiple/nonstandard roots withheld, no Newton guess or fabricated root",
    "xirr_bracket": ["-0.999999999999", "1000000"],
    "xirr_solver": "monotone bisection up to 256 iterations; zero-rate exact special case; residual checked",
    "xirr_residual_tolerance": "max(0.000001 paise, invested_paise*1e-30)",
    "precision": 50,
    "decimal_exponent_limits": [-999999, 999999],
    "rounding": "ROUND_HALF_EVEN",
    "percent_decimal_places": 8,
    "maximum_calendar_months": 600,
    "maximum_observations": 30000,
    "maximum_input_paise": 10**14,
    "maximum_terminal_paise": 10**32,
    "persistence": "immutable executable policy only; no investor request, schedule or result persistence",
    "lineage": "normalized request + complete captured input hash + policy + code/runtime identity",
    "undefined": "explicit unavailable result/reason; no Infinity, NaN, fabricated zero, dropped instalment or Growth substitution for IDCW",
    "exclusions": [
        "IDCW/distribution events",
        "fees/tax/loads",
        "order cutoffs",
        "benchmark alpha",
        "investment advice",
        "current peer comparison",
    ],
}


def policy():
    return deepcopy(_POLICY)


def validate_policy(document):
    if fingerprint(document) != fingerprint(_POLICY):
        raise ValueError("unsupported historical calculator policy")
    return fingerprint(document)


def engine_identity():
    return {
        "version": ENGINE_VERSION,
        "code_hash": hashlib.sha256(
            Path(__file__).read_bytes() + Path(schemas.__file__).read_bytes()
        ).hexdigest(),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "decimal_library": __libmpdec_version__,
    }


def round_paise(value):
    whole, remainder = divmod(value.numerator, value.denominator)
    return whole + int(
        2 * remainder > value.denominator or 2 * remainder == value.denominator and whole % 2 == 1
    )


def decimal_fraction(value, places):
    return format(
        (Decimal(value.numerator) / value.denominator).quantize(Decimal(1).scaleb(-places)), "f"
    )


def xirr(cashflows):
    """Not a generic multi-root solver; explicit admissible class and bounds."""
    aggregated = {}
    for day, amount in cashflows:
        if not isinstance(day, date) or isinstance(amount, bool) or not isinstance(amount, int):
            raise ValueError("exact dated integer cash flows required")
        aggregated[day] = aggregated.get(day, 0) + amount
    flows = sorted((d, a) for d, a in aggregated.items() if a)

    def empty(reason):
        return {"xirr_pct": None, "xirr_unavailable_reason": reason, "xirr_residual_paise": None}

    if len(flows) < 2:
        return empty("no_elapsed_cash_flow_time")
    changes = sum((a > 0) != (b > 0) for (_, a), (_, b) in zip(flows, flows[1:]))
    if changes != 1 or flows[-1][1] <= 0 or any(a >= 0 for _, a in flows[:-1]):
        return empty("unsupported_or_multiple_cash_flow_sign_changes")
    end, terminal = flows[-1]
    invested = -sum(a for _, a in flows[:-1])
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999)):
        terms = [(-Decimal(a), Decimal((end - d).days) / 365) for d, a in flows[:-1]]

        def residual(rate):
            if rate == 0:
                return Decimal(terminal - invested)
            log = (1 + rate).ln()
            return Decimal(terminal) - sum(
                amount * (log * elapsed).exp() for amount, elapsed in terms
            )

        low, high = Decimal(_POLICY["xirr_bracket"][0]), Decimal(_POLICY["xirr_bracket"][1])
        if terminal == invested:
            answer = Decimal(0)
        else:
            if residual(low) < 0 or residual(high) > 0:
                return empty("xirr_root_outside_policy_bracket")
            for _ in range(256):
                mid = (low + high) / 2
                if mid == low or mid == high:
                    break
                value = residual(mid)
                if value == 0:
                    low = high = mid
                    break
                if value > 0:
                    low = mid
                else:
                    high = mid
            answer = (low + high) / 2
        error = residual(answer)
        if abs(error) > max(Decimal("0.000001"), Decimal(invested) * Decimal("1e-30")):
            return empty("xirr_residual_not_converged")
        pct = (answer * 100).quantize(Decimal("0.00000001"))
        return {
            "xirr_pct": format(abs(pct) if pct == 0 else pct, "f"),
            "xirr_unavailable_reason": None,
            "xirr_residual_paise": format(error, "f"),
        }


def calculate(snapshot, plan, document):
    def unavailable(reason):
        return {"available": False, "unavailable_reason": reason, "purchases": [], "summary": None}

    if snapshot["identity"]["option_type"] != "GROWTH":
        return unavailable("unsupported_option")
    quality = snapshot["context"].get("nav_quality", {})
    if "signals" not in quality or quality["signals"] is None:
        return unavailable("quality_evidence_unavailable")
    if quality["signals"]:
        return unavailable("quality_signalled")
    if (
        quality.get("assessment_status") != "current"
        or quality.get("assessed_dataset_version") != snapshot["dataset_version"]
    ):
        return unavailable("quality_not_current")
    if snapshot["context"]["methodology"]["comparison_eligible"] is not True:
        return unavailable("current_source_methodology_ineligible")
    points = snapshot["observations"]
    if not points:
        return unavailable("no_observations")
    if len(points) > document["maximum_observations"]:
        raise ValueError("historical observation budget exceeded")
    days = [date.fromisoformat(p["date"]) for p in points]
    if any(b <= a for a, b in zip(days, days[1:])) or any(not 1900 <= d.year <= 2100 for d in days):
        raise ValueError("bounded unique chronological NAV observations required")
    if any(isinstance(p["nav"], (bool, float)) for p in points):
        raise ValueError("exact NAV strings required")
    values = [Decimal(p["nav"]) for p in points]
    if any(not n.is_finite() or n <= 0 for n in values):
        return unavailable("invalid_nav")
    navs = [Fraction(n) for n in values]
    start, end = date.fromisoformat(plan.start_date), date.fromisoformat(plan.end_date)
    if end > days[-1]:
        return unavailable("end_after_last_observation")
    if plan.kind == "lumpsum":
        targets = [start]
        contribution = plan.initial_paise
    else:
        targets = []
        for offset in range((end.year - start.year) * 12 + end.month - start.month + 1):
            year, zero_month = divmod(start.year * 12 + start.month - 1 + offset, 12)
            month = zero_month + 1
            target = date(year, month, min(plan.day_of_month, calendar.monthrange(year, month)[1]))
            if start <= target <= end:
                targets.append(target)
        contribution = plan.monthly_paise
    if not targets:
        return unavailable("no_scheduled_contributions")
    purchases, flows, units, first = [], [], Fraction(0), None
    for target in targets:
        index = bisect_left(days, target)
        if index == len(days) or days[index] > end or (days[index] - target).days > 7:
            return unavailable("purchase_observation_uncovered")
        first = index if first is None else first
        bought = Fraction(contribution, 100) / navs[index]
        units += bought
        purchases.append(
            {
                "requested_date": target.isoformat(),
                "price_date": days[index].isoformat(),
                "shift_calendar_days": (days[index] - target).days,
                "nav_inr": format(values[index], "f"),
                "contribution_paise": contribution,
                "units_purchased_display": decimal_fraction(bought, 12),
            }
        )
        flows.append((days[index], -contribution))
    last = bisect_right(days, end) - 1
    if last < first or (end - days[last]).days > 7:
        return unavailable("terminal_observation_uncovered")
    gap = max(
        ((b - a).days for a, b in zip(days[first : last + 1], days[first + 1 : last + 1])),
        default=0,
    )
    if gap > 7:
        return unavailable("interior_observation_gap_exceeds_policy")
    terminal = round_paise(units * navs[last] * 100)
    if not 0 <= terminal <= document["maximum_terminal_paise"]:
        return unavailable("terminal_value_budget_exceeded")
    invested = contribution * len(targets)
    summary = {
        "invested_paise": invested,
        "terminal_paise": terminal,
        "net_gain_paise": terminal - invested,
        "period_gain_pct": decimal_fraction(Fraction(terminal - invested, invested) * 100, 8),
        "units_held_display": decimal_fraction(units, 12),
        "valuation_date": days[last].isoformat(),
        "valuation_nav_inr": format(values[last], "f"),
        "valuation_lag_calendar_days": (end - days[last]).days,
        "largest_observed_gap_calendar_days": gap,
        **xirr([*flows, (days[last], terminal)]),
    }
    return {
        "available": True,
        "unavailable_reason": None,
        "purchases": purchases,
        "summary": summary,
    }


def derive(payload, snapshot, document):
    ph = validate_policy(document)
    plan = schemas.REQUEST_ADAPTER.validate_python(payload)
    if plan.code != snapshot["code"]:
        raise ValueError("requested fund differs from captured fund")
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999)):
        outcome = calculate(snapshot, plan, document)
    result = {
        "format": "historical-growth-calculator-result-v1",
        "request": plan.model_dump(mode="json"),
        "input_hash": fingerprint(snapshot),
        "source_revision": snapshot["source_revision"],
        "dataset_version": snapshot["dataset_version"],
        "context": schemas.Context.model_validate(snapshot["context"]).model_dump(mode="json"),
        "policy_hash": ph,
        "engine": engine_identity(),
        "basis": "historical_observed_growth_nav_simulation_not_execution_or_forecast",
        "units": "INR_paise",
        "comparison_eligible": False,
        "personal_scenario_persisted": False,
        **outcome,
    }
    return {"result_id": fingerprint(result), **result}
