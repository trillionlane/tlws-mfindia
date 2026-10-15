"""Synthetic independent arithmetic fixtures, NEVER real-source/DEV certificates."""

import json
from copy import deepcopy
from datetime import date, timedelta
from decimal import Context, Decimal, DefaultContext, Inexact, ROUND_DOWN, getcontext, localcontext
from fractions import Fraction
from pathlib import Path

import pytest

from mfdataindia.analytics import idcw_research as engine
from mfdataindia.review.policy import fingerprint

ROOT = Path(__file__).resolve().parents[1]
NAV_SOURCE, EVENT_SOURCE, MAPPING_SOURCE = "a" * 64, "b" * 64, "c" * 64


def sample(prices=None, payouts=None):
    prices = prices or [("2026-01-01", "100"), ("2026-01-02", "90"), ("2026-01-03", "99")]
    payouts = [("2026-01-02", "10", "90")] if payouts is None else payouts
    return {
        "format": "idcw-gross-research-input-v1",
        "code": 999001,
        "plan": "REGULAR",
        "option": "IDCW",
        "identity_hash": "d" * 64,
        "dataset_version": 1,
        "source_revision_hash": "e" * 64,
        "source_hashes": [NAV_SOURCE, EVENT_SOURCE, MAPPING_SOURCE],
        "observations": [
            {"date": day, "nav_inr": nav, "source_hash": NAV_SOURCE} for day, nav in prices
        ],
        "events": [
            {
                "effective_date": day,
                "distribution_basis": "gross_before_personal_tax",
                "reinvestment_basis": "explicit_same_day_ex_distribution_nav",
                "distribution_inr_per_unit": amount,
                "ex_distribution_nav_inr": nav,
                "distribution_source_hash": EVENT_SOURCE,
                "mapping_source_hash": MAPPING_SOURCE,
            }
            for day, amount, nav in payouts
        ],
        "unresolved_unit_event_dates": [],
        "source_conflicts": [],
    }


def derive(value=None):
    return engine.derive(sample() if value is None else value, engine.policy())


def render_fraction(value):
    with localcontext(Context(prec=100)):
        return format((Decimal(value.numerator) / value.denominator).quantize(Decimal("0.00000001")), "f")


def independent_units_ledger(request):
    # Exact rational cash/units oracle; no engine helpers or float approximations.
    units = Fraction(1)
    initial = Fraction(request["observations"][0]["nav_inr"])
    events = {e["effective_date"]: Fraction(e["distribution_inr_per_unit"]) for e in request["events"]}
    wealth = []
    for point in request["observations"]:
        nav = Fraction(point["nav_inr"])
        cash = units * events.get(point["date"], 0)
        units += cash / nav
        wealth.append(units * nav / initial)
    return wealth


def test_distribution_only_drop_does_not_create_research_loss():
    result = derive()
    assert result["calculation_status"] == "computed"
    assert [p["wealth_index"] for p in result["points"]] == ["100.00000000", "100.00000000", "110.00000000"]
    assert [p["period_return_pct"] for p in result["points"]] == [None, "0.00000000", "10.00000000"]
    assert result["summary"]["cumulative_return_pct"] == "10.00000000"
    assert result["summary"]["max_drawdown_pct"] == "0.00000000"
    assert result["summary"]["cagr_pct"] is None
    assert result["summary"]["cagr_unavailable_reason"] == "less_than_365_elapsed_days"


def test_no_events_is_raw_price_arithmetic_not_proof_of_no_distributions():
    result = derive(sample(payouts=[]))
    assert [p["wealth_index"] for p in result["points"]] == ["100.00000000", "90.00000000", "99.00000000"]
    assert result["summary"]["cumulative_return_pct"] == "-1.00000000"
    assert result["summary"]["max_drawdown_pct"] == "-10.00000000"
    assert result["coverage_status"] == "not_certified"
    assert result["analytics_eligible"] is False


def test_repeated_events_match_independent_exact_cash_units_oracle():
    prices = [("2026-01-01", "137.12345678"), ("2026-01-02", "131.34567890"),
              ("2026-01-05", "128.12345678"), ("2026-01-06", "130.98765432")]
    r = sample(prices, [("2026-01-02", "7.12345678", prices[1][1]),
                        ("2026-01-05", "6.12345678", prices[2][1])])
    result = derive(r)
    expected = independent_units_ledger(r)
    assert [p["wealth_index"] for p in result["points"]] == [render_fraction(w * 100) for w in expected]
    assert result["summary"]["cumulative_return_pct"] == render_fraction((expected[-1] - 1) * 100)
    peak, worst = Fraction(1), Fraction(0)
    for p, wealth in zip(result["points"], expected):
        peak = max(peak, wealth)
        drawdown = wealth / peak - 1
        worst = min(worst, drawdown)
        assert p["drawdown_pct"] == render_fraction(drawdown * 100)
    assert result["summary"]["max_drawdown_pct"] == render_fraction(worst * 100)
    assert result["summary"]["declared_events"] == 2


def test_real_market_loss_is_not_hidden_by_distribution_adjustment():
    r = sample([("2026-01-01", "100"), ("2026-01-02", "80"), ("2026-01-03", "72")],
               [("2026-01-02", "10", "80")])
    result = derive(r)
    assert result["summary"]["cumulative_return_pct"] == "-19.00000000"
    assert result["summary"]["max_drawdown_pct"] == "-19.00000000"
    assert result["points"][1]["period_return_pct"] == "-10.00000000"


@pytest.mark.parametrize("elapsed", [364, 365, 366, 730])
def test_cagr_elapsed_days_and_independent_high_precision_power(elapsed):
    start = date(2023, 1, 1)
    prices = [((start + timedelta(days=i)).isoformat(), "100" if i < elapsed else "121") for i in range(elapsed + 1)]
    summary = derive(sample(prices, []))["summary"]
    assert summary["elapsed_calendar_days"] == elapsed
    if elapsed < 365:
        assert summary["cagr_pct"] is None
    else:
        with localcontext(Context(prec=100)):
            answer = (Decimal("1.21") ** (Decimal("365.25") / elapsed) - 1) * 100
            expected = format(answer.quantize(Decimal("0.00000001")), "f")
        assert summary["cagr_pct"] == expected


def test_terminal_date_distribution_is_included_exactly_once():
    r = sample([("2026-01-01", "100"), ("2026-01-02", "90")])
    assert derive(r)["summary"]["cumulative_return_pct"] == "0.00000000"


@pytest.mark.parametrize("day", ["2026-01-01", "2025-12-31", "2026-01-04"])
def test_start_and_outside_events_rejected_not_double_counted(day):
    r = sample()
    r["events"][0]["effective_date"] = day
    with pytest.raises(ValueError, match="start exclusive"):
        derive(r)


@pytest.mark.parametrize("change,reason", [
    ("conflict", "source_conflicts"),
    ("units", "unsupported_unit_events"),
    ("missing", "event_date_nav_missing"),
    ("mismatch", "event_ex_nav_mismatch"),
    ("gap", "observed_nav_gap_exceeds_policy"),
])
def test_explicit_blockers_remove_all_numeric_outputs(change, reason):
    r = sample()
    if change == "conflict":
        r["source_conflicts"] = ["issuer sources conflict"]
    elif change == "units":
        r["unresolved_unit_event_dates"] = ["2026-01-02"]
    elif change == "missing":
        r["observations"].pop(1)
    elif change == "mismatch":
        r["events"][0]["ex_distribution_nav_inr"] = "89.99"
    else:
        r["observations"][-1]["date"] = "2026-01-10"
    result = derive(r)
    assert result["calculation_status"] == "unavailable"
    assert result["unavailable_reason"] == reason
    assert result["points"] == [] and result["summary"] is None
    assert result["analytics_eligible"] is False


def test_seven_day_gap_allowed_only_as_arithmetic_assumption():
    r = sample([("2026-01-01", "100"), ("2026-01-08", "90")], [])
    result = derive(r)
    assert result["calculation_status"] == "computed"
    assert result["summary"]["largest_observed_gap_calendar_days"] == 7
    assert result["summary"]["sampling"] == "supplied_observations_not_certified_daily_continuity"
    assert not result["analytics_eligible"]


@pytest.mark.parametrize("value", [0, 100.0, True, None, "0", "-1", "1e2", "NaN", "Infinity", "1.000000001", "1000000000", " 100", "١٠٠"])
@pytest.mark.parametrize("target", ["nav", "distribution", "event_nav"])
def test_invalid_and_inexact_numeric_inputs_fail(value, target):
    r = sample()
    if target == "nav":
        r["observations"][0]["nav_inr"] = value
    else:
        r["events"][0]["distribution_inr_per_unit" if target == "distribution" else "ex_distribution_nav_inr"] = value
    with pytest.raises(ValueError):
        derive(r)


@pytest.mark.parametrize("change", ["duplicates", "reversed", "duplicate_events", "reversed_events", "source_missing", "duplicate_source", "bad_hash", "bad_date", "bool_code", "float_version", "growth", "net_tax", "delayed", "override", "empty", "control", "unit_outside"])
def test_invalid_structure_or_wrong_semantics_rejected(change):
    r = sample()
    if change == "duplicates":
        r["observations"].append(r["observations"][-1])
    elif change == "reversed":
        r["observations"].reverse()
    elif change == "duplicate_events":
        r["events"].append(r["events"][0])
    elif change == "reversed_events":
        r["events"].insert(0, {**r["events"][0], "effective_date": "2026-01-03"})
    elif change == "source_missing":
        r["source_hashes"].remove(MAPPING_SOURCE)
    elif change == "duplicate_source":
        r["source_hashes"].append(NAV_SOURCE)
    elif change == "bad_hash":
        r["identity_hash"] = "x" * 64
    elif change == "bad_date":
        r["observations"][0]["date"] = "2026-1-01"
    elif change == "bool_code":
        r["code"] = True
    elif change == "float_version":
        r["dataset_version"] = 1.0
    elif change == "growth":
        r["option"] = "GROWTH"
    elif change == "net_tax":
        r["events"][0]["distribution_basis"] = "net_after_personal_tax"
    elif change == "delayed":
        r["events"][0]["reinvestment_basis"] = "next_available_nav"
    elif change == "override":
        r["coverage_complete"] = True
    elif change == "empty":
        r["observations"] = []
    elif change == "control":
        r["source_conflicts"] = ["line\ninjection"]
    else:
        r["unresolved_unit_event_dates"] = ["2025-01-01"]
    with pytest.raises(ValueError):
        derive(r)


def test_long_window_and_observation_event_budgets():
    r = sample()
    r["observations"][-1]["date"] = "2126-01-01"
    with pytest.raises(ValueError):
        derive(r)
    r = sample()
    r["observations"][-1]["date"] = "2099-01-01"
    r["observations"][0]["date"] = "1900-01-01"
    with pytest.raises(ValueError, match="window budget"):
        derive(r)
    for key, size in (("observations", 30001), ("events", 601), ("source_hashes", 1201)):
        r = sample()
        r[key] = [r[key][0]] * size
        with pytest.raises(ValueError):
            derive(r)


def test_policy_is_versioned_complete_stored_and_not_mutable():
    document = engine.policy()
    stored = json.loads((ROOT / "docs/policies/idcw-gross-research-total-return-v1.json").read_text())
    assert document == stored
    assert engine.validate_policy(stored) == fingerprint(document)
    document["exclusions"].clear()
    assert engine.policy()["exclusions"]
    with pytest.raises(ValueError, match="new tested version"):
        engine.derive(sample(), document)


def test_identical_replay_no_input_mutation_or_wall_clock_and_hash_closure():
    r = sample()
    original = deepcopy(r)
    a, b = derive(r), derive(r)
    assert a == b and r == original
    assert a["input_hash"] == fingerprint(original)
    assert a["result_hash"] == fingerprint({k: v for k, v in a.items() if k != "result_hash"})
    assert a["publication_status"] == "unqualified_engine_output"
    assert a["analytics_eligible"] is False
    assert a["coverage_status"] == "not_certified"
    assert a["points"][1]["raw_nav_inr"] == "90"


@pytest.mark.parametrize("field,value", [("identity_hash", "f" * 64), ("source_revision_hash", "f" * 64), ("dataset_version", 2), ("plan", "DIRECT"), ("code", 999002)])
def test_input_dependencies_change_lineage_without_overwriting_old_result(field, value):
    r = sample()
    old = derive(r)
    r[field] = value
    new = derive(r)
    assert old["input_hash"] != new["input_hash"]
    assert old["result_hash"] != new["result_hash"]
    assert old["declared_input"][field] != value
    assert new["points"] == old["points"]


def test_revision_of_event_price_or_distribution_changes_result_not_sources():
    r = sample()
    old = derive(r)
    r["events"][0]["distribution_inr_per_unit"] = "5"
    new = derive(r)
    assert new["result_hash"] != old["result_hash"]
    assert new["points"] != old["points"]
    assert r["observations"] == old["declared_input"]["observations"]


def test_mutated_model_lists_are_revalidated():
    model = engine.ResearchInput.model_validate(sample())
    model.events.append(model.events[0])
    with pytest.raises(ValueError):
        derive(model)


def test_output_is_independent_of_hostile_decimal_context():
    expected = derive()
    original = getcontext().copy()
    try:
        getcontext().prec = 2
        getcontext().rounding = ROUND_DOWN
        getcontext().Emax = 2
        getcontext().Emin = -2
        getcontext().traps[Inexact] = True
        assert derive() == expected
    finally:
        from decimal import setcontext

        setcontext(original)


def test_huge_compounded_result_withholds_instead_of_infinity_or_partial_series():
    start = date(2026, 1, 1)
    prices = [((start + timedelta(days=i)).isoformat(), "0.00000001") for i in range(60)]
    r = sample(prices, [(day, "999999999", nav) for day, nav in prices[1:]])
    result = derive(r)
    assert result["unavailable_reason"] == "decimal_range_unavailable"
    assert result["points"] == [] and result["summary"] is None


def test_global_default_context_does_not_change_replay_or_error_handling():
    expected = derive()
    original_traps = DefaultContext.traps.copy()
    original_clamp = DefaultContext.clamp
    try:
        DefaultContext.traps[Inexact] = True
        DefaultContext.clamp = 1
        assert derive() == expected
    finally:
        DefaultContext.traps = original_traps
        DefaultContext.clamp = original_clamp


def test_rescaling_all_prices_and_cash_leaves_return_and_wealth_unchanged():
    r = sample()
    original = derive(r)
    for point in r["observations"]:
        point["nav_inr"] = str(Decimal(point["nav_inr"]) * 10)
    for event in r["events"]:
        for field in ("distribution_inr_per_unit", "ex_distribution_nav_inr"):
            event[field] = str(Decimal(event[field]) * 10)
    rescaled = derive(r)
    assert rescaled["summary"] == original["summary"]
    for old, new in zip(original["points"], rescaled["points"]):
        for field in ("wealth_index", "period_return_pct", "drawdown_pct"):
            assert new[field] == old[field]
    assert rescaled["input_hash"] != original["input_hash"]


def test_incomplete_and_conflicting_inputs_never_accept_a_caller_publication_flag():
    for field in ("analytics_eligible", "coverage_status", "publication_status"):
        r = sample()
        r[field] = True
        with pytest.raises(ValueError):
            derive(r)
