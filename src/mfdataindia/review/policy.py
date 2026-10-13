"""Immutable executable policy. Decimal calculations never use ambient context."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from decimal import Decimal

ENGINE_VERSION = "verified-nav-1"


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


# A deliberately fixed initial policy: arbitrary registry JSON cannot execute
# formulas. Supporting another method requires code, fixtures and a new version.
_POLICY = {
    "id": "growth-observed-nav",
    "version": 1,
    "engine_version": ENGINE_VERSION,
    "option": "GROWTH",
    "return_basis": "observed_nav_change",
    "periodic_return": "simple: nav[t]/nav[t-1]-1",
    "cumulative_return": "nav[end]/nav[start]-1",
    "cagr": "(nav[end]/nav[start])^(365.25/elapsed_calendar_days)-1",
    "volatility": "sample_stdev(periodic_returns)*sqrt(252)",
    "sharpe": "mean(periodic_return-rf_periodic)/sample_stdev(periodic_return-rf_periodic)*sqrt(252)",
    "sortino": "mean(periodic_return-rf_periodic)/sqrt(mean(min(periodic_return-rf_periodic,0)^2))*sqrt(252)",
    "drawdown": "min(nav[t]/running_max(nav)-1)",
    "calmar": "cagr/abs(max_drawdown)",
    "window": "complete_captured_history",
    "day_count": "ACT/365.25",
    "sampling": "observed_business_days_only",
    "annualization_periods": 252,
    "risk_free_annual_effective": "0.065",
    "risk_free_periodic": "(1+rf_annual)^(1/252)-1",
    "risk_free_source": "fixed_assumption_not_live_or_historical_yield",
    "non_business_observations": "reject_risk_metrics_if_weekend_observed",
    "max_observation_gap_calendar_days": 7,
    "minimum_risk_observations": 253,
    "minimum_cagr_elapsed_days": 365,
    "holiday_calendar": "unavailable; gaps<=7 days accepted as observed-interval assumption",
    "imputation": "none",
    "precision": 50,
    "rounding": "ROUND_HALF_EVEN",
    "output_decimal_places": 8,
    "units": "returns/volatility/drawdown in percent; Sharpe/Sortino/Calmar dimensionless",
    "undefined": "null_with_reason; never zero or infinity",
    "eligibility": "Growth; active/current/supported-signal-free assessment for current research use",
    "runtime": "CPython>=3.11 Decimal correctly-rounded ln/exp; local precision 50",
    "limitations": ["NAV-based; no execution, fees, taxes or distribution reconstruction",
                    "252-period annualization is an assumption, not inferred from row count"],
}


def policy() -> dict:
    return deepcopy(_POLICY)


def validate_policy(document: dict) -> str:
    if canonical(document) != canonical(_POLICY):
        raise ValueError("unsupported or modified policy; introduce a new tested engine/policy version")
    return fingerprint(document)


def decimal_text(value: object) -> str:
    """Lossless canonical source numeric text; never accept floats."""
    if isinstance(value, float) or isinstance(value, bool):
        raise ValueError("source numbers must be Decimal or exact text")
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError("non-finite source number")
    return format(number, "f")
