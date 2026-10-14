from copy import deepcopy
from datetime import date
from decimal import Context, Decimal, Inexact, ROUND_DOWN, getcontext, localcontext

import pytest

from mfdataindia.analytics import rolling
from test_calendar_analytics import sample


def series():
    points = [("2022-01-31", "100")]
    for month in range(2, 13):
        import calendar

        points.append((date(2022, month, calendar.monthrange(2022, month)[1]).isoformat(), "100"))
    return points + [("2023-01-31", "110"), ("2023-02-28", "121"), ("2023-03-31", "133.1")]


def reference(ratio, days):
    # Independent high-precision Decimal POWER reference; engine uses ln/exp.
    with localcontext(Context(prec=100)):
        return (Decimal(ratio) ** (Decimal("365.2425") / days) - 1) * 100


def rendered(value):
    with localcontext(Context(prec=100)):
        return str(value.quantize(Decimal("0.00000001")))


def test_monthly_anniversary_series_and_independent_summary_reference():
    result = rolling.calculate(sample(series()), rolling.policy())
    term = result["terms"][0]
    expected = [reference(r, 365) for r in ("1.1", "1.21", "1.331")]
    assert [w["annualized_return_pct"] for w in term["windows"]] == [rendered(v) for v in expected]
    assert [w["end_boundary"] for w in term["windows"]] == [
        "2023-01-31",
        "2023-02-28",
        "2023-03-31",
    ]
    assert term["summary"]["median_pct"] == rendered(expected[1])
    with localcontext(Context(prec=100)):
        mean = sum(expected) / 3
    assert term["summary"]["arithmetic_mean_pct"] == rendered(mean)
    assert term["summary"]["positive_windows_pct"] == "100.00000000"
    assert term["summary"]["unavailable_reason"] is None
    assert [t["years"] for t in result["terms"]] == [1, 3, 5]
    assert all(
        t["summary"]["unavailable_reason"] == "insufficient_covered_windows"
        for t in result["terms"][1:]
    )


def test_leap_anniversary_clamps_and_actual_days_are_disclosed():
    points = [
        ("2023-02-28", "100"),
        ("2023-03-31", "100"),
        ("2024-02-29", "200"),
        ("2024-03-31", "200"),
    ]
    value = rolling.calculate(sample(points), rolling.policy())["terms"][0]["windows"][0]
    assert value["start_boundary"] == "2023-02-28"
    assert value["elapsed_calendar_days"] == 366
    assert value["annualized_return_pct"] == rendered(reference("2", 366))
    assert value["largest_observed_gap_calendar_days"] > 7
    assert "NOT daily continuity" in rolling.policy()["interior_coverage"]


def test_unfinished_month_never_schedules_a_future_close():
    points = series() + [("2023-04-15", "200")]
    value = rolling.calculate(sample(points), rolling.policy())
    assert value["analysis_date"] == "2023-04-15"
    assert value["terms"][0]["windows"][-1]["end_boundary"] == "2023-03-31"


def test_missing_endpoint_cannot_be_forward_filled_by_a_later_price():
    points = [p for p in series() if p[0] != "2022-02-28"]
    points.insert(1, ("2022-03-01", "150"))
    points.sort()
    result = rolling.derive(sample(points), rolling.policy())
    term = result["rolling"]["terms"][0]
    missing = term["windows"][1]
    assert missing["annualized_return_pct"] is None
    assert missing["unavailable_reason"] == "start_boundary_uncovered"
    assert term["summary"]["available_windows"] == 2
    assert term["summary"]["unavailable_reason"] == "rolling_endpoint_coverage_incomplete"
    assert term["summary"]["arithmetic_mean_pct"] is None and not result["eligible_at_capture"]


def test_zero_and_negative_windows_not_positive_forecast_probability():
    points = [(d, "100") for d, _ in series()]
    value = rolling.calculate(sample(points), rolling.policy())["terms"][0]
    assert value["summary"]["arithmetic_mean_pct"] == "0.00000000"
    assert value["summary"]["positive_windows_pct"] == "0.00000000"
    points[-1] = (points[-1][0], "50")
    value = rolling.calculate(sample(points), rolling.policy())["terms"][0]
    assert value["summary"]["minimum_pct"].startswith("-")
    assert value["summary"]["maximum_pct"] == "0.00000000"


def test_one_window_cannot_publish_a_distribution_summary():
    points = series()[:-2]
    result = rolling.derive(sample(points), rolling.policy())
    assert result["rolling"]["terms"][0]["summary"]["available_windows"] == 1
    assert result["rolling"]["terms"][0]["summary"]["minimum_pct"] is None
    assert not result["eligible_at_capture"]


def test_five_year_engine_uses_actual_days_not_a_trading_day_proxy():
    import calendar

    points = []
    for year in range(2015, 2022):
        for month in range(1, 13):
            day = date(year, month, calendar.monthrange(year, month)[1])
            if day > date(2021, 2, 28):
                break
            points.append((day.isoformat(), "128" if year == 2021 else "100"))
    # The 2021-02-28 target subtracts five years to 2016-02-28, not
    # 2016-02-29. Supply a real earlier observation, never a later leap close.
    points.append(("2016-02-26", "100"))
    points.sort()
    term = rolling.calculate(sample(points), rolling.policy())["terms"][2]
    jan = next(w for w in term["windows"] if w["end_boundary"] == "2021-01-31")
    assert jan["start_boundary"] == "2016-01-31"
    assert jan["elapsed_calendar_days"] == 1827
    assert jan["annualized_return_pct"] == rendered(reference("1.28", 1827))
    assert term["summary"]["unavailable_reason"] is None


@pytest.mark.parametrize("first_day,covered", [("2022-01-24", True), ("2022-01-23", False)])
def test_boundary_lag_and_actual_elapsed_time_are_not_hidden(first_day, covered):
    points = series()
    points[0] = (first_day, "100")
    first = rolling.calculate(sample(points), rolling.policy())["terms"][0]["windows"][0]
    if covered:
        assert first["start_boundary_lag_days"] == 7
        assert first["elapsed_calendar_days"] == 372
        assert first["annualized_return_pct"] == rendered(reference("1.1", 372))
    else:
        assert first["annualized_return_pct"] is None
        assert first["unavailable_reason"] == "start_boundary_uncovered"


@pytest.mark.parametrize("option", ["IDCW", "DIVIDEND", "BONUS", "UNKNOWN"])
def test_unsupported_options_never_borrow_growth_series(option):
    value = rolling.derive(sample(series(), option), rolling.policy())
    assert value["rolling"]["terms"] == []
    assert value["rolling"]["unavailable_reason"] == "unsupported_option"
    assert not value["eligible_at_capture"]


@pytest.mark.parametrize("nav", ["0", "-10", "NaN", "Infinity"])
def test_invalid_nav_is_reasoned_and_original_values_retained(nav):
    s = sample(series())
    s["observations"][0]["nav"] = nav
    before = deepcopy(s)
    assert rolling.calculate(s, rolling.policy())["unavailable_reason"] == "invalid_nav"
    assert s == before


def test_no_observations_is_distinct_from_short_history():
    assert (
        rolling.calculate(sample([]), rolling.policy())["unavailable_reason"] == "no_observations"
    )
    assert (
        rolling.calculate(sample([("2024-01-01", "100")]), rolling.policy())["terms"][0]["summary"][
            "scheduled_windows"
        ]
        == 0
    )


def test_runtime_decimal_context_does_not_change_deterministic_artifact():
    source = sample(series())
    before = deepcopy(source)
    expected = rolling.derive(source, rolling.policy())
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        context.Emax = 20
        assert rolling.derive(source, rolling.policy()) == expected
    assert source == before
    assert getcontext().prec == 28


@pytest.mark.parametrize(
    "mutation",
    [{"version": True}, {"precision": 20}, {"terms_years": [1]}, {"day_count_year": "365"}],
)
def test_policy_drift_requires_a_new_tested_version(mutation):
    document = rolling.policy()
    document.update(mutation)
    with pytest.raises(ValueError, match="unsupported rolling policy"):
        rolling.calculate(sample(series()), document)


def test_date_order_float_and_observation_budget_are_rejected():
    with pytest.raises(ValueError, match="date ordered"):
        rolling.calculate(sample([("2024-02-01", "100"), ("2024-01-01", "100")]), rolling.policy())
    with pytest.raises(ValueError, match="date ordered"):
        rolling.calculate(sample([("2024-01-01", "100"), ("2024-01-01", "100")]), rolling.policy())
    with pytest.raises(ValueError, match="exact NAV text"):
        rolling.calculate(sample([("2024-01-01", 100.0)]), rolling.policy())
    with pytest.raises(ValueError, match="observation budget"):
        rolling.calculate(sample([("2024-01-01", "100")] * 30001), rolling.policy())
