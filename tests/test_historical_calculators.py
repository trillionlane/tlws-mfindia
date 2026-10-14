from copy import deepcopy
from datetime import date, timedelta
from decimal import Context, Decimal, Inexact, ROUND_DOWN, localcontext
from fractions import Fraction

import pytest
from pydantic import ValidationError
from mfdataindia.calculators import historical as engine, historical_schemas as schemas


def sample(start="2023-01-02", end="2024-01-02", price=None):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    observations = []
    day = first
    while day <= last:
        if day.weekday() < 5:
            observations.append({"date": day.isoformat(), "nav": price(day) if price else "100"})
        day += timedelta(days=1)
    return {
        "code": 1,
        "source_revision": 1,
        "dataset_version": 1,
        "identity": {"option_type": "GROWTH"},
        "observations": observations,
        "context": {
            "lifecycle": {"state": "active", "evidence": "amfi_current_feed"},
            "nav_freshness": {
                "status": "current",
                "latest_nav_date": end,
                "dataset_as_of": end,
                "lag_days": 0,
            },
            "nav_quality": {
                "assessment_status": "current",
                "assessed_dataset_version": 1,
                "signals": [],
            },
            "methodology": {
                "basis": "nav_change",
                "distribution_adjustment": "not_applicable",
                "comparison_eligible": True,
                "limitations": [],
            },
        },
    }


def run(snapshot=None, **changes):
    source = sample() if snapshot is None else snapshot
    payload = {
        "kind": "lumpsum",
        "code": 1,
        "start_date": "2023-01-02",
        "end_date": "2024-01-02",
        "initial_paise": 1000000,
        **changes,
    }
    if payload["kind"] == "sip":
        payload.pop("initial_paise")
    result = engine.derive(payload, source, engine.policy())
    schemas.Response.model_validate(result)
    return result


def test_one_actual_year_lumpsum_and_independent_xirr():
    source = sample(price=lambda d: "110" if d == date(2024, 1, 2) else "100")
    result = run(source)
    assert result["available"]
    s = result["summary"]
    assert (s["invested_paise"], s["terminal_paise"], s["net_gain_paise"]) == (
        1000000,
        1100000,
        100000,
    )
    assert s["period_gain_pct"] == "10.00000000" and s["xirr_pct"] == "10.00000000"
    assert abs(Decimal(s["xirr_residual_paise"])) < Decimal("0.000001")
    assert result["comparison_eligible"] is False and result["personal_scenario_persisted"] is False


def test_leap_horizon_uses_actual_365_day_count_reference():
    source = sample("2023-03-01", "2024-03-01", lambda d: "121" if d == date(2024, 3, 1) else "100")
    result = run(source, start_date="2023-03-01", end_date="2024-03-01")
    with localcontext(Context(prec=100)):
        expected = ((Decimal("1.21") ** (Decimal(365) / 366) - 1) * 100).quantize(
            Decimal("0.00000001")
        )
    assert result["summary"]["xirr_pct"] == str(expected)


def test_month_end_clamp_next_observation_and_no_source_fill():
    source = sample("2024-01-01", "2024-04-02", lambda d: "4" if d == date(2024, 4, 1) else "3")
    before = deepcopy(source)
    result = run(
        source,
        kind="sip",
        monthly_paise=1,
        day_of_month=31,
        start_date="2024-01-01",
        end_date="2024-04-01",
    )
    assert result["available"]
    assert [p["requested_date"] for p in result["purchases"]] == [
        "2024-01-31",
        "2024-02-29",
        "2024-03-31",
    ]
    assert [p["price_date"] for p in result["purchases"]] == [
        "2024-01-31",
        "2024-02-29",
        "2024-04-01",
    ]
    assert result["purchases"][-1]["shift_calendar_days"] == 1
    units = Fraction(1, 300) * 2 + Fraction(1, 400)
    assert engine.round_paise(units * 4 * 100) == result["summary"]["terminal_paise"] == 4
    assert source == before


def test_missing_final_instalment_is_not_silently_dropped_or_priced_after_end():
    result = run(
        sample("2024-01-01", "2024-04-02"),
        kind="sip",
        monthly_paise=100,
        day_of_month=31,
        start_date="2024-01-01",
        end_date="2024-03-31",
    )
    assert (
        not result["available"] and result["unavailable_reason"] == "purchase_observation_uncovered"
    )
    assert result["purchases"] == [] and result["summary"] is None


def test_weekend_lumpsum_uses_next_actual_nav_and_actual_cash_flow_day():
    source = sample("2023-10-13", "2023-11-17")
    result = run(source, start_date="2023-10-15", end_date="2023-11-16")
    assert result["purchases"][0]["price_date"] == "2023-10-16"
    assert result["purchases"][0]["shift_calendar_days"] == 1
    assert result["summary"]["xirr_pct"] == "0.00000000"


def test_dense_coverage_and_terminal_lag_are_disclosed():
    source = sample("2023-01-02", "2024-01-08")
    result = run(source, end_date="2024-01-07")
    assert result["summary"]["valuation_date"] == "2024-01-05"
    assert result["summary"]["valuation_lag_calendar_days"] == 2
    assert result["summary"]["largest_observed_gap_calendar_days"] == 3


def test_interior_gap_and_future_end_are_unavailable_not_fake_zero():
    source = sample()
    source["observations"] = [
        p for p in source["observations"] if not "2023-06-01" <= p["date"] <= "2023-06-15"
    ]
    result = run(source)
    assert result["unavailable_reason"] == "interior_observation_gap_exceeds_policy"
    assert run(end_date="2024-01-03")["unavailable_reason"] == "end_after_last_observation"


def test_same_day_has_money_value_but_no_annualized_cash_flow_return():
    result = run(start_date="2024-01-02")
    assert result["available"] and result["summary"]["net_gain_paise"] == 0
    assert result["summary"]["xirr_pct"] is None
    assert result["summary"]["xirr_unavailable_reason"] == "no_elapsed_cash_flow_time"


def test_repeating_unit_ratios_are_not_rounded_before_terminal_value():
    source = sample("2023-01-02", "2023-01-03", lambda d: "3" if d.day == 2 else "1.5")
    result = run(source, initial_paise=3, start_date="2023-01-02", end_date="2023-01-03")
    assert result["summary"]["terminal_paise"] == 2  # exact 1.5 paise -> HALF_EVEN
    assert engine.round_paise(Fraction(5, 2)) == 2
    assert engine.round_paise(Fraction(-3, 2)) == -2


@pytest.mark.parametrize(
    "change,reason",
    [
        ("idcw", "unsupported_option"),
        ("null", "quality_evidence_unavailable"),
        ("absent", "quality_evidence_unavailable"),
        ("stale", "quality_not_current"),
        ("signalled", "quality_signalled"),
        ("ineligible", "current_source_methodology_ineligible"),
    ],
)
def test_idcw_and_quality_controls_withhold_entire_simulation(change, reason):
    source = sample()
    q = source["context"]["nav_quality"]
    if change == "idcw":
        source["identity"]["option_type"] = "IDCW"
    if change == "null":
        q.update(signals=None, assessment_status="not_assessed")
    if change == "absent":
        q.pop("signals")
    if change == "stale":
        q["assessed_dataset_version"] = 0
    if change == "signalled":
        q["signals"] = ["duplicate_variant_series"]
    if change == "ineligible":
        source["context"]["methodology"]["comparison_eligible"] = False
    result = run(source)
    assert result["unavailable_reason"] == reason and result["summary"] is None


def test_xirr_positive_negative_zero_multiple_and_out_of_bracket_cases():
    a, b = date(2023, 1, 2), date(2024, 1, 2)
    assert engine.xirr([(a, -100), (b, 100)])["xirr_pct"] == "0.00000000"
    assert engine.xirr([(a, -100), (b, 50)])["xirr_pct"] == "-50.00000000"
    assert (
        engine.xirr([(a, -100), (date(2023, 6, 1), 200), (b, -100)])["xirr_unavailable_reason"]
        == "unsupported_or_multiple_cash_flow_sign_changes"
    )
    assert (
        engine.xirr([(a, -100), (a + timedelta(days=1), 200)])["xirr_unavailable_reason"]
        == "xirr_root_outside_policy_bracket"
    )
    assert engine.xirr([(a, -100), (b, 0)])["xirr_pct"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"code": True},
        {"initial_paise": 1.0},
        {"initial_paise": True},
        {"customer_name": "not allowed"},
        {"start_date": "2023-02-30"},
        {"end_date": "2022-01-01"},
        {"start_date": "1900-01-01", "end_date": "2024-01-02"},
        {"start_date": "2023-01-02T00:00:00Z"},
    ],
)
def test_invalid_dates_money_personal_fields_and_budgets_rejected(changes):
    with pytest.raises(ValidationError):
        run(**changes)


def test_replay_and_full_source_dependency_hash_ignore_ambient_decimal_context():
    source, payload = (
        sample(),
        {
            "kind": "lumpsum",
            "code": 1,
            "start_date": "2023-01-02",
            "end_date": "2024-01-02",
            "initial_paise": 100,
        },
    )
    before = deepcopy(source)
    expected = engine.derive(payload, source, engine.policy())
    with localcontext() as ctx:
        ctx.prec = 6
        ctx.rounding = ROUND_DOWN
        ctx.traps[Inexact] = True
        assert engine.derive(payload, source, engine.policy()) == expected
    assert source == before
    source["source_revision"] += 1
    assert engine.derive(payload, source, engine.policy())["result_id"] != expected["result_id"]
    altered = engine.policy()
    altered["version"] = True
    with pytest.raises(ValueError, match="unsupported historical"):
        engine.derive(payload, source, altered)
