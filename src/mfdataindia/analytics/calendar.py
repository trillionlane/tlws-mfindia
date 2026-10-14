"""Observed-price calendar returns, with reproducible dates and no imputation."""

from __future__ import annotations

import calendar
import hashlib
import platform
from bisect import bisect_right
from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN, localcontext, __libmpdec_version__
from pathlib import Path

from mfdataindia.review.policy import fingerprint

ENGINE_VERSION = "calendar-nav-1"
_POLICY = {
    "id": "growth-calendar-nav-returns",
    "version": 1,
    "engine_version": ENGINE_VERSION,
    "option": "GROWTH",
    "calendar": "Gregorian calendar, not fiscal or trading-day years",
    "formula": "(observed_end_nav / observed_start_nav - 1) * 100",
    "units": "percent cumulative; calendar-year, YTD and monthly returns are NOT annualized",
    "completed_year": "31 December of preceding year to 31 December of named year",
    "year_to_date": "31 December of preceding year to latest captured fund NAV date",
    "completed_month": "last calendar day of preceding month to last calendar day of named month",
    "analysis_date": "latest captured fund NAV date; no wall clock or caller date",
    "boundary_price": "last published observation on or before target boundary; never after",
    "maximum_boundary_lag_calendar_days": 7,
    "incomplete_period": "omit unfinished months/years; report analysis_date and current_month_complete",
    "coverage": "endpoint-based; interior gaps do not alter endpoint arithmetic and do not certify daily continuity",
    "missing_boundary": "null return with start_boundary_uncovered or end_boundary_uncovered",
    "same_observation": "null with distinct_price_dates_required, never a fabricated zero return",
    "imputation": "none; neither inserted source rows nor invented transaction prices",
    "precision": 50,
    "rounding": "ROUND_HALF_EVEN",
    "output_decimal_places": 8,
    "minimum_calendar_year": 1900,
    "maximum_years": 100,
    "maximum_observations": 30000,
    "source": "complete exact retained NAV snapshot with source/dataset/identity/context lineage",
    "eligibility": "Growth only; current research publication additionally requires lifecycle, freshness and quality eligibility",
    "undefined": "null with reason, never NaN or infinity",
    "limitations": [
        "NAV change, not an investor execution or cash-flow return",
        "No distribution reconstruction, fees/tax simulation or holiday-calendar assertion",
        "Boundary lags are disclosed; sparse interior history is not daily-coverage certification",
    ],
    "runtime": "CPython Decimal, local precision 50; runtime/library versions recorded in result",
}


def policy():
    return deepcopy(_POLICY)


def validate_policy(document):
    if fingerprint(document) != fingerprint(_POLICY):
        raise ValueError("unsupported calendar policy; publish a new tested version")
    return fingerprint(document)


def engine_identity():
    return {
        "version": ENGINE_VERSION,
        "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "decimal_library": __libmpdec_version__,
    }


def calculate(snapshot, document):
    validate_policy(document)
    empty = {
        "analysis_date": None,
        "current_month_complete": False,
        "calendar_years": [],
        "year_to_date": None,
        "months": [],
        "unavailable_reason": None,
    }
    if snapshot["identity"]["option_type"] != "GROWTH":
        return {**empty, "unavailable_reason": "unsupported_option"}
    points = snapshot["observations"]
    if not points:
        return {**empty, "unavailable_reason": "no_observations"}
    if len(points) > document["maximum_observations"]:
        raise ValueError("calendar observation budget exceeded")
    days = [date.fromisoformat(p["date"]) for p in points]
    if any(b <= a for a, b in zip(days, days[1:])):
        raise ValueError("calendar observations must be unique and date ordered")
    if any(isinstance(p["nav"], (float, bool)) for p in points):
        raise ValueError("exact NAV text required")
    navs = [Decimal(p["nav"]) for p in points]
    if any(not n.is_finite() or n <= 0 for n in navs):
        return {**empty, "unavailable_reason": "invalid_nav"}
    if days[0].year < document["minimum_calendar_year"]:
        return {**empty, "unavailable_reason": "unsupported_calendar_date"}
    as_of = days[-1]
    if as_of.year - days[0].year + 1 > document["maximum_years"]:
        raise ValueError("calendar year budget exceeded")
    with localcontext() as context:
        context.prec = document["precision"]
        context.rounding = ROUND_HALF_EVEN

        def window(kind, label, start, end):
            result = {
                "kind": kind,
                "label": label,
                "start_boundary": start.isoformat(),
                "end_boundary": end.isoformat(),
                "start_price_date": None,
                "end_price_date": None,
                "start_boundary_lag_days": None,
                "end_boundary_lag_days": None,
                "return_pct": None,
                "unavailable_reason": None,
            }
            indices = []
            for prefix, target in [("start", start), ("end", end)]:
                index = bisect_right(days, target) - 1
                if (
                    index < 0
                    or (target - days[index]).days > document["maximum_boundary_lag_calendar_days"]
                ):
                    result["unavailable_reason"] = prefix + "_boundary_uncovered"
                    return result
                result[prefix + "_price_date"] = days[index].isoformat()
                result[prefix + "_boundary_lag_days"] = (target - days[index]).days
                indices.append(index)
            first, last = indices
            if first == last:
                result["unavailable_reason"] = "distinct_price_dates_required"
                return result
            value = ((navs[last] / navs[first] - 1) * 100).quantize(Decimal("0.00000001"))
            result["return_pct"] = format(abs(value) if value == 0 else value, "f")
            return result

        years = []
        months = []
        for year in range(days[0].year, as_of.year + 1):
            end = date(year, 12, 31)
            if end <= as_of:
                years.append(window("calendar_year", str(year), date(year - 1, 12, 31), end))
            for month in range(1, 13):
                end = date(year, month, calendar.monthrange(year, month)[1])
                if end > as_of:
                    break
                prior_year, prior_month = (year - 1, 12) if month == 1 else (year, month - 1)
                start = date(
                    prior_year, prior_month, calendar.monthrange(prior_year, prior_month)[1]
                )
                months.append(window("month", f"{year:04d}-{month:02d}", start, end))
        ytd = window("year_to_date", str(as_of.year), date(as_of.year - 1, 12, 31), as_of)
    return {
        "analysis_date": as_of.isoformat(),
        "current_month_complete": as_of.day == calendar.monthrange(as_of.year, as_of.month)[1],
        "calendar_years": years,
        "year_to_date": ytd,
        "months": months,
        "unavailable_reason": None,
    }


def derive(snapshot, document):
    policy_hash = validate_policy(document)
    engine = engine_identity()
    values = calculate(snapshot, document)
    available = any(
        p["return_pct"] is not None
        for p in [
            *values["calendar_years"],
            *values["months"],
            *([values["year_to_date"]] if values["year_to_date"] else []),
        ]
    )
    result = {
        "format": "calendar-nav-result-v1",
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
        "calendar": values,
    }
    return {"result_id": fingerprint(result), **result}
