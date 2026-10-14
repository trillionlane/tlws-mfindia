from copy import deepcopy
from decimal import getcontext

import pytest

from mfdataindia.analytics import calendar


def sample(points, option="GROWTH"):
    return {
        "code": 1,
        "identity": {"option_type": option},
        "observations": [{"date": d, "nav": n} for d, n in points],
        "source_revision": 1,
        "dataset_version": 1,
        "context": {"methodology": {"comparison_eligible": True}},
    }


def calculated(points):
    return calendar.calculate(sample(points), calendar.policy())


def test_independent_endpoint_arithmetic_and_nonannualized_labels():
    value = calculated(
        [
            ("2022-12-30", "100"),
            ("2023-01-31", "110"),
            ("2023-02-28", "99"),
            ("2023-12-29", "125"),
            ("2024-01-15", "150"),
        ]
    )
    year = next(r for r in value["calendar_years"] if r["label"] == "2023")
    assert year["return_pct"] == "25.00000000"
    assert year["start_price_date"] == "2022-12-30" and year["end_price_date"] == "2023-12-29"
    assert (year["start_boundary_lag_days"], year["end_boundary_lag_days"]) == (1, 2)
    assert value["year_to_date"]["return_pct"] == "20.00000000"
    rows = {r["label"]: r for r in value["months"]}
    assert (
        rows["2023-01"]["return_pct"] == "10.00000000"
        and rows["2023-02"]["return_pct"] == "-10.00000000"
    )
    assert "2024-01" not in rows and not value["current_month_complete"]


def test_leap_month_closure_and_same_price_date_not_zero():
    value = calculated([("2024-01-31", "3"), ("2024-02-29", "4")])
    assert value["current_month_complete"]
    assert value["months"][-1]["label"] == "2024-02"
    assert value["months"][-1]["return_pct"] == "33.33333333"
    flat = calculated([("2024-01-31", "100"), ("2024-02-01", "100")])
    assert flat["months"][-1]["return_pct"] is None


def test_end_price_after_boundary_never_used_to_fill_missing_close():
    value = calculated([("2023-12-29", "100"), ("2024-01-20", "200"), ("2024-02-01", "400")])
    jan = next(r for r in value["months"] if r["label"] == "2024-01")
    assert jan["return_pct"] is None and jan["unavailable_reason"] == "end_boundary_uncovered"


def test_boundary_lag_seven_is_disclosed_eight_is_unavailable():
    good = calculated([("2023-12-24", "100"), ("2024-01-31", "120")])["months"][-1]
    assert good["return_pct"] == "20.00000000" and good["start_boundary_lag_days"] == 7
    bad = calculated([("2023-12-23", "100"), ("2024-01-31", "120")])["months"][-1]
    assert bad["return_pct"] is None and bad["unavailable_reason"] == "start_boundary_uncovered"


def test_interior_gap_is_not_daily_continuity_claim():
    value = calculated([("2022-12-31", "100"), ("2023-12-31", "120")])
    assert value["calendar_years"][-1]["return_pct"] == "20.00000000"
    assert "endpoint-based" in calendar.policy()["coverage"]


@pytest.mark.parametrize("nav", ["0", "-1", "NaN", "Infinity"])
def test_invalid_values_are_unavailable_without_editing_input(nav):
    source = sample([("2023-12-31", nav), ("2024-01-31", "100")])
    original = deepcopy(source)
    assert calendar.calculate(source, calendar.policy())["unavailable_reason"] == "invalid_nav"
    assert source == original


@pytest.mark.parametrize("option", ["IDCW", "DIVIDEND", "BONUS", "UNKNOWN"])
def test_no_distribution_or_option_guess(option):
    result = calendar.calculate(
        sample([("2023-12-31", "100"), ("2024-01-31", "110")], option), calendar.policy()
    )
    assert result["unavailable_reason"] == "unsupported_option" and result["months"] == []


def test_empty_short_and_unchanged_nav_are_distinct():
    assert calculated([])["unavailable_reason"] == "no_observations"
    assert calculated([("2024-02-01", "100")])["year_to_date"]["return_pct"] is None
    flat = calculated([("2023-12-31", "100"), ("2024-01-31", "100")])
    assert flat["months"][-1]["return_pct"] == "0.00000000"


def test_precision_replay_and_immutable_policy():
    source = sample([("2023-12-31", "3"), ("2024-01-31", "4")])
    copy = deepcopy(source)
    before = getcontext().prec
    try:
        getcontext().prec = 6
        a = calendar.derive(source, calendar.policy())
        getcontext().prec = 70
        assert calendar.derive(source, calendar.policy()) == a
        assert source == copy
    finally:
        getcontext().prec = before
    changed = calendar.policy()
    changed["precision"] = 20
    with pytest.raises(ValueError):
        calendar.calculate(source, changed)


@pytest.mark.parametrize(
    "points",
    [
        [("2024-01-01", "100"), ("2024-01-01", "101")],
        [("2024-01-02", "100"), ("2024-01-01", "101")],
    ],
)
def test_unsorted_or_duplicate_observations_are_rejected(points):
    with pytest.raises(ValueError):
        calculated(points)


def test_float_source_is_not_financial_truth():
    with pytest.raises(ValueError):
        calculated([("2023-12-31", 100.0), ("2024-01-31", "110")])
