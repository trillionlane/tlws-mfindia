"""Policy-bound observed-price anniversary windows; no source imputation."""

from __future__ import annotations

import calendar
import hashlib
import platform
from bisect import bisect_right
from copy import deepcopy
from datetime import date
from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext
from decimal import __libmpdec_version__
from pathlib import Path

from mfdataindia.review.policy import fingerprint

ENGINE_VERSION = "rolling-anniversary-nav-1"
_POLICY = {
    "id": "growth-anniversary-rolling-nav-returns",
    "version": 1,
    "engine_version": ENGINE_VERSION,
    "option": "GROWTH",
    "terms_years": [1, 3, 5],
    "schedule": "completed Gregorian calendar month ends; independent of chart selection",
    "analysis_date": "last captured NAV date; omit unfinished month; no wall clock",
    "start_target": "end calendar boundary minus term years; 29 February clamps to 28 February",
    "minimum_history": "schedule begins when end boundary reaches first observed NAV anniversary",
    "boundary_price": "last actual observation on or before target; never look ahead",
    "maximum_boundary_lag_calendar_days": 7,
    "formula": "((end_NAV/start_NAV) ** (365.2425/actual_elapsed_calendar_days) - 1) * 100",
    "day_count_year": "365.2425",
    "units": "percent annualized; actual observed start/end dates define elapsed days",
    "interior_coverage": "endpoint arithmetic only; disclose largest observed gap, NOT daily continuity certification",
    "minimum_summary_observations": 2,
    "summary_coverage": "all scheduled eligible-history endpoints must be covered; otherwise null summary with reason",
    "summary": "min/max/median/arithmetic mean of unrounded annualized returns; positive fraction excludes zero",
    "probability_scope": "descriptive share of this fund's covered monthly windows, not forecast or whole-market probability",
    "imputation": "none; no inserted NAV rows or inferred prices/distributions",
    "precision": 50,
    "rounding": "ROUND_HALF_EVEN",
    "output_decimal_places": 8,
    "decimal_exponent_limits": [-999999, 999999],
    "minimum_calendar_year": 1900,
    "maximum_years": 100,
    "maximum_observations": 30000,
    "maximum_scheduled_windows_per_term": 1200,
    "source": "exact retained source/dataset/identity/context snapshot",
    "eligibility": "Growth and current methodology eligibility, with at least one complete summary term",
    "undefined": "null with a reason; never infinity, NaN, fabricated zero or Growth-to-IDCW substitution",
    "limitations": [
        "Not an investor execution, cash-flow return, risk metric or daily rolling series",
        "No trading-calendar, tax, fee or distribution adjustment assertion",
        "Historical rolling summaries do not predict future outcomes",
    ],
}


def policy():
    return deepcopy(_POLICY)


def validate_policy(document):
    if fingerprint(document) != fingerprint(_POLICY):
        raise ValueError("unsupported rolling policy; publish a new tested version")
    return fingerprint(document)


def engine_identity():
    return {
        "version": ENGINE_VERSION,
        "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "decimal_library": __libmpdec_version__,
    }


def anniversary(day, years):
    year = day.year - years
    return date(year, day.month, min(day.day, calendar.monthrange(year, day.month)[1]))


def _decimal(value):
    rounded = value.quantize(Decimal("0.00000001"))
    return format(abs(rounded) if rounded == 0 else rounded, "f")


def calculate(snapshot, document):
    validate_policy(document)
    empty = {
        "analysis_date": None,
        "schedule": "completed_calendar_month_end",
        "terms": [],
        "unavailable_reason": None,
    }
    if snapshot["identity"]["option_type"] != "GROWTH":
        return {**empty, "unavailable_reason": "unsupported_option"}
    points = snapshot["observations"]
    if not points:
        return {**empty, "unavailable_reason": "no_observations"}
    if len(points) > document["maximum_observations"]:
        raise ValueError("rolling observation budget exceeded")
    days = [date.fromisoformat(p["date"]) for p in points]
    if any(b <= a for a, b in zip(days, days[1:])):
        raise ValueError("rolling observations must be unique and date ordered")
    if any(isinstance(p["nav"], (float, bool)) for p in points):
        raise ValueError("exact NAV text required")
    navs = [Decimal(p["nav"]) for p in points]
    if any(not n.is_finite() or n <= 0 for n in navs):
        return {**empty, "unavailable_reason": "invalid_nav"}
    if days[0].year < document["minimum_calendar_year"]:
        return {**empty, "unavailable_reason": "unsupported_calendar_date"}
    as_of = days[-1]
    if as_of.year - days[0].year + 1 > document["maximum_years"]:
        raise ValueError("rolling year budget exceeded")
    ends = [
        date(y, m, calendar.monthrange(y, m)[1])
        for y in range(days[0].year, as_of.year + 1)
        for m in range(1, 13)
    ]
    terms = []
    # New Context, not a copy of the caller's traps/exponent settings.
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999)):
        for years in document["terms_years"]:
            scheduled = [end for end in ends if end <= as_of and anniversary(end, years) >= days[0]]
            if len(scheduled) > document["maximum_scheduled_windows_per_term"]:
                raise ValueError("rolling window budget exceeded")
            windows, values = [], []
            for end in scheduled:
                start = anniversary(end, years)
                row = {
                    "end_boundary": end.isoformat(),
                    "start_boundary": start.isoformat(),
                    "start_price_date": None,
                    "end_price_date": None,
                    "start_boundary_lag_days": None,
                    "end_boundary_lag_days": None,
                    "elapsed_calendar_days": None,
                    "observations": None,
                    "largest_observed_gap_calendar_days": None,
                    "annualized_return_pct": None,
                    "unavailable_reason": None,
                }
                indices = []
                for prefix, target in (("start", start), ("end", end)):
                    index = bisect_right(days, target) - 1
                    if (
                        index < 0
                        or (target - days[index]).days
                        > document["maximum_boundary_lag_calendar_days"]
                    ):
                        row["unavailable_reason"] = prefix + "_boundary_uncovered"
                        break
                    row[prefix + "_price_date"] = days[index].isoformat()
                    row[prefix + "_boundary_lag_days"] = (target - days[index]).days
                    indices.append(index)
                if len(indices) == 2:
                    first, last = indices
                    elapsed = (days[last] - days[first]).days
                    row.update(
                        elapsed_calendar_days=elapsed,
                        observations=last - first + 1,
                        largest_observed_gap_calendar_days=max(
                            (
                                (b - a).days
                                for a, b in zip(days[first:last], days[first + 1 : last + 1])
                            ),
                            default=0,
                        ),
                    )
                    if elapsed <= 0:
                        row["unavailable_reason"] = "distinct_price_dates_required"
                    else:
                        try:
                            # Natural log/exponential operate at the stored precision;
                            # summary calculations retain this unrounded value.
                            value = (
                                (navs[last] / navs[first]).ln()
                                * (Decimal(document["day_count_year"]) / elapsed)
                            ).exp()
                            result = (value - 1) * 100
                            row["annualized_return_pct"] = _decimal(result)
                            values.append(result)
                        except DecimalException:
                            row["unavailable_reason"] = "decimal_range_unavailable"
                windows.append(row)
            reason = (
                "insufficient_covered_windows"
                if len(values) < document["minimum_summary_observations"]
                else "rolling_endpoint_coverage_incomplete"
                if len(values) != len(windows)
                else None
            )
            summary = {
                "scheduled_windows": len(windows),
                "available_windows": len(values),
                "unavailable_windows": len(windows) - len(values),
                "minimum_pct": None,
                "maximum_pct": None,
                "median_pct": None,
                "arithmetic_mean_pct": None,
                "positive_windows_pct": None,
                "unavailable_reason": reason,
            }
            if reason is None:
                ordered = sorted(values)
                size = len(ordered)
                median = (
                    ordered[size // 2]
                    if size % 2
                    else (ordered[size // 2 - 1] + ordered[size // 2]) / 2
                )
                try:
                    summary.update(
                        minimum_pct=_decimal(ordered[0]),
                        maximum_pct=_decimal(ordered[-1]),
                        median_pct=_decimal(median),
                        arithmetic_mean_pct=_decimal(sum(values) / size),
                        positive_windows_pct=_decimal(
                            Decimal(sum(v > 0 for v in values)) * 100 / size
                        ),
                    )
                except DecimalException:
                    summary.update(
                        minimum_pct=None,
                        maximum_pct=None,
                        median_pct=None,
                        arithmetic_mean_pct=None,
                        positive_windows_pct=None,
                        unavailable_reason="decimal_range_unavailable",
                    )
            terms.append({"years": years, "windows": windows, "summary": summary})
    return {**empty, "analysis_date": as_of.isoformat(), "terms": terms}


def derive(snapshot, document):
    policy_hash = validate_policy(document)
    engine = engine_identity()
    values = calculate(snapshot, document)
    available = any(t["summary"]["unavailable_reason"] is None for t in values["terms"])
    result = {
        "format": "rolling-nav-result-v1",
        "code": snapshot["code"],
        "input_hash": fingerprint(snapshot),
        "policy_hash": policy_hash,
        "engine_version": ENGINE_VERSION,
        "engine_fingerprint": fingerprint(engine),
        "engine": engine,
        "source_revision": snapshot["source_revision"],
        "dataset_version": snapshot["dataset_version"],
        "context": snapshot["context"],
        "eligible_at_capture": bool(snapshot["context"]["methodology"]["comparison_eligible"])
        and available,
        "first_date": snapshot["observations"][0]["date"] if snapshot["observations"] else None,
        "as_of": snapshot["observations"][-1]["date"] if snapshot["observations"] else None,
        "points": len(snapshot["observations"]),
        "rolling": values,
    }
    return {"result_id": fingerprint(result), **result}
