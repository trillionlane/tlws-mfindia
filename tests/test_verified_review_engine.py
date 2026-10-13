"""Independent golden values, not comparison with the legacy implementation."""
import copy
import math
from datetime import date, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from mfdataindia.review.engine import calculate
from mfdataindia.review.policy import decimal_text, fingerprint, policy, validate_policy
from mfdataindia.review.store import derive


def sample(values, days=None, option="GROWTH"):
    if days is None:
        days = []
        day = date(2024, 1, 1)
        while len(days) < len(values):
            if day.weekday() < 5:
                days.append(day)
            day += timedelta(days=1)
    return {"code": 1, "source_revision": 1, "dataset_version": 1,
            "identity": {"option_type": option}, "facts": None,
            "observations": [{"date": day.isoformat(), "nav": str(value)} for day, value in zip(days, values)],
            "context": {"methodology": {"comparison_eligible": True}}}


def test_immutable_policy_and_exact_hash():
    p = policy()
    assert validate_policy(p) == fingerprint(p)
    p["annualization_periods"] = 365
    with pytest.raises(ValueError):
        validate_policy(p)
    p = policy()
    p["version"] = True
    with pytest.raises(ValueError):
        validate_policy(p)
    assert policy()["version"] == 1


def test_exact_source_and_no_float_conversion():
    assert decimal_text(Decimal("123.123456789012345678")) == "123.123456789012345678"
    for value in (1.1, True, "NaN", "Infinity"):
        with pytest.raises(ValueError):
            decimal_text(value)


def test_cagr_golden_exact_four_years():
    s = sample(["100", "146.41"], [date(2020, 1, 1), date(2024, 1, 1)])
    out = calculate(s, policy())
    assert out["metrics"]["cumulative_return_pct"] == "46.41000000"
    assert out["metrics"]["cagr_pct"] == "10.00000000"
    assert out["metrics"]["max_drawdown_pct"] == "0.00000000"
    assert out["metrics"]["calmar"] is None
    assert out["unavailable"]["calmar"] == "zero_drawdown"


def test_drawdown_peak_to_trough_not_endpoint_change():
    out = calculate(sample([100, 120, 90, 108]), policy())
    assert out["metrics"]["max_drawdown_pct"] == "-25.00000000"
    assert out["metrics"]["cumulative_return_pct"] == "8.00000000"
    assert out["metrics"]["cagr_pct"] is None


def test_risk_metrics_against_independent_fraction_float_reference():
    values = [100 if i % 2 == 0 else 110 for i in range(253)]
    out = calculate(sample(values), policy())["metrics"]
    rs = [float(Fraction(1, 10)), float(Fraction(-1, 11))] * 126
    rf = 1.065 ** (1 / 252) - 1
    mean = sum(rs) / len(rs)
    stdev = math.sqrt(sum((r-mean)**2 for r in rs) / (len(rs)-1))
    excess = [r-rf for r in rs]
    downside = math.sqrt(sum(min(r, 0)**2 for r in excess) / len(rs))
    assert float(out["annual_volatility_pct"]) == pytest.approx(stdev*math.sqrt(252)*100, abs=1e-7)
    assert float(out["sharpe"]) == pytest.approx((mean-rf)/stdev*math.sqrt(252), abs=1e-7)
    assert float(out["sortino"]) == pytest.approx((mean-rf)/downside*math.sqrt(252), abs=1e-7)


def test_constant_series_null_sharpe_not_infinity():
    out = calculate(sample([100]*253), policy())
    assert out["metrics"]["annual_volatility_pct"] == "0.00000000"
    assert out["metrics"]["sharpe"] is None
    assert out["unavailable"]["sharpe"] == "zero_volatility"
    assert float(out["metrics"]["sortino"]) == pytest.approx(-math.sqrt(252), abs=1e-7)


@pytest.mark.parametrize("option", ["IDCW", "BONUS", "UNKNOWN"])
def test_non_growth_unavailable(option):
    out = calculate(sample([100,110], option=option), policy())
    assert set(out["unavailable"].values()) == {"unsupported_option"}
    assert all(v is None for v in out["metrics"].values())


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity"])
def test_invalid_nav_not_bridged(value):
    out = calculate(sample([100,value,110]), policy())
    assert set(out["unavailable"].values()) == {"invalid_nav"}


def test_gap_and_weekend_risk_unavailable():
    s = sample([100]*253)
    s["observations"][-1]["date"] = "2026-01-01"
    assert calculate(s, policy())["unavailable"]["sharpe"] == "large_observation_gap"
    s = sample([100]*253)
    s["observations"][0]["date"] = "2023-12-31"
    assert calculate(s, policy())["unavailable"]["sharpe"] == "non_business_observation"


def test_duplicate_dates_rejected():
    s = sample([100,110])
    s["observations"][1]["date"] = s["observations"][0]["date"]
    with pytest.raises(ValueError, match="date ordered"):
        calculate(s, policy())


def test_replay_ignores_ambient_decimal_precision():
    s = sample([100,120,90,108])
    baseline = derive(s, policy())
    with localcontext() as context:
        context.prec = 6
        assert derive(s, policy()) == baseline
    changed = copy.deepcopy(s)
    changed["observations"][1]["nav"] = "121"
    assert derive(changed, policy())["result_id"] != baseline["result_id"]


def test_short_history_and_no_downside():
    assert calculate(sample([]), policy())["unavailable"]["sharpe"] == "insufficient_observations"
    values = [str(Decimal("100") * Decimal("1.01")**i) for i in range(253)]
    out = calculate(sample(values), policy())
    assert out["metrics"]["sortino"] is None
    assert out["unavailable"]["sortino"] == "zero_downside_deviation"
