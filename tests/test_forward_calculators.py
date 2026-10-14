from copy import deepcopy
from decimal import Context, Decimal, Inexact, ROUND_DOWN, ROUND_HALF_EVEN, localcontext
from fractions import Fraction

import pytest
from pydantic import ValidationError

from mfdataindia.calculators import forward, schemas


def common(**changes):
    return {"months": 12, "annual_return_pct": "0", "rate_mode": "effective_annual", **changes}


def run(kind, **arguments):
    result = forward.derive({"kind": kind, **common(**arguments)}, forward.policy())
    schemas.Response.model_validate(result)
    for row in result["schedule"]:
        assert (
            row["closing_paise"]
            == row["opening_paise"]
            + row["contribution_paise"]
            + row["interest_paise"]
            - row["paid_withdrawal_paise"]
        )
    s = result["summary"]
    assert s["net_gain_paise"] == s["closing_paise"] + s["withdrawn_paise"] - s["invested_paise"]
    return result


def fraction_round(value):
    whole, remainder = divmod(value.numerator, value.denominator)
    twice = remainder * 2
    return whole + int(twice > value.denominator or twice == value.denominator and whole % 2 == 1)


def test_small_feasible_goal_is_not_rejected_by_speculative_maximum_balance_overflow():
    result = run("goal", months=600, annual_return_pct="100", rate_mode="nominal_annual_monthly",
                 cost_today_paise=1000, annual_inflation_pct="0", timing="end")
    assert result["summary"]["required_starting_monthly_paise"] == 1
    balance = 0
    for row in result["schedule"]:
        credit = fraction_round(Fraction(balance, 12))
        balance += credit + 1
        assert row["closing_paise"] == balance and row["interest_paise"] == credit
    assert 1000 <= balance <= forward.MAX_BALANCE
    assert all(row["contribution_paise"] == 1 for row in result["schedule"])
    with pytest.raises(forward.CalculationUnavailable, match="balance_budget"):
        run("sip", months=600, annual_return_pct="100", rate_mode="nominal_annual_monthly",
            monthly_paise=100000000000000, timing="end")


def test_lumpsum_nominal_rate_matches_independent_rational_monthly_schedule():
    result = run(
        "lumpsum", initial_paise=1234567, annual_return_pct="12", rate_mode="nominal_annual_monthly"
    )
    balance = 1234567
    for row in result["schedule"]:
        credit = fraction_round(Fraction(balance, 100))
        balance += credit
        assert row["interest_paise"] == credit and row["closing_paise"] == balance


def test_effective_and_nominal_rate_modes_are_not_silently_equivalent():
    actual = run("lumpsum", initial_paise=100000000, annual_return_pct="12")
    nominal = run(
        "lumpsum",
        initial_paise=100000000,
        annual_return_pct="12",
        rate_mode="nominal_annual_monthly",
    )
    with localcontext(Context(prec=100)):
        rate = Decimal("1.12") ** (Decimal(1) / 12) - 1
        balance = 100000000
        for row in actual["schedule"]:
            credit = int((balance * rate).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
            balance += credit
            assert row["interest_paise"] == credit
    assert actual["summary"]["closing_paise"] == balance
    assert nominal["summary"]["closing_paise"] > balance


def test_zero_rate_sip_timing_and_first_period_interest():
    begin = run("sip", monthly_paise=10000, timing="beginning")
    end = run("sip", monthly_paise=10000, timing="end")
    assert begin["summary"]["closing_paise"] == end["summary"]["closing_paise"] == 120000
    begin = run(
        "sip",
        months=1,
        monthly_paise=10000,
        timing="beginning",
        annual_return_pct="12",
        rate_mode="nominal_annual_monthly",
    )
    end = run(
        "sip",
        months=1,
        monthly_paise=10000,
        timing="end",
        annual_return_pct="12",
        rate_mode="nominal_annual_monthly",
    )
    assert begin["summary"]["closing_paise"] == 10100 and end["summary"]["closing_paise"] == 10000


def test_stepup_applies_at_anniversary_not_each_month():
    result = run(
        "stepup_sip",
        months=25,
        monthly_paise=10000,
        timing="end",
        step={"kind": "annual_percent", "value_pct": "50"},
    )
    amounts = [r["contribution_paise"] for r in result["schedule"]]
    assert amounts[:12] == [10000] * 12 and amounts[12:24] == [15000] * 12 and amounts[24] == 22500
    fixed = run(
        "stepup_sip",
        months=25,
        monthly_paise=10000,
        timing="end",
        step={"kind": "annual_fixed", "value_paise": 5000},
    )
    assert fixed["schedule"][-1]["contribution_paise"] == 20000


def test_swp_partial_final_withdrawal_and_depletion_are_exact():
    result = run("swp", months=4, initial_paise=2500, withdrawal_paise=1000, timing="end")
    assert [r["paid_withdrawal_paise"] for r in result["schedule"]] == [1000, 1000, 500, 0]
    assert result["summary"]["depletion_month"] == 3 and result["summary"]["closing_paise"] == 0
    assert result["summary"]["net_gain_paise"] == 0


def test_swp_timing_and_zero_withdrawal_are_explicit():
    begin = run(
        "swp",
        months=1,
        initial_paise=10000,
        withdrawal_paise=10000,
        timing="beginning",
        annual_return_pct="12",
        rate_mode="nominal_annual_monthly",
    )
    end = run(
        "swp",
        months=1,
        initial_paise=10000,
        withdrawal_paise=10000,
        timing="end",
        annual_return_pct="12",
        rate_mode="nominal_annual_monthly",
    )
    assert begin["summary"]["closing_paise"] == 0 and end["summary"]["closing_paise"] == 100
    zero = run("swp", initial_paise=2500, withdrawal_paise=0, timing="end")
    assert zero["summary"]["depletion_month"] is None and zero["summary"]["withdrawn_paise"] == 0


def test_negative_assumption_is_not_a_zero_or_positive_return():
    value = run("lumpsum", initial_paise=100000, annual_return_pct="-20")
    assert 0 < value["summary"]["closing_paise"] < 100000 and value["summary"]["net_gain_paise"] < 0


def test_reverse_goal_minimum_integer_sip_and_ceiling():
    value = run("goal", cost_today_paise=12001, annual_inflation_pct="0", timing="end")
    assert value["summary"]["required_starting_monthly_paise"] == 1001
    assert value["summary"]["closing_paise"] == 12012 and value["summary"]["goal_met"]
    assert value["summary"]["inflated_goal_paise"] == 12001


def test_inflation_annual_boundary_has_no_exp_log_extra_paise():
    value = run("goal", cost_today_paise=10000, annual_inflation_pct="12", timing="end")
    assert value["summary"]["inflated_goal_paise"] == 11200
    for years in (1, 2, 5, 10):
        value = run(
            "goal",
            months=12 * years,
            cost_today_paise=100000,
            annual_inflation_pct="10",
            timing="end",
        )
        rational = Fraction(100000) * Fraction(11, 10) ** years
        expected = -(-rational.numerator // rational.denominator)
        assert value["summary"]["inflated_goal_paise"] == expected


def test_reverse_stepup_minimality_and_zero_start_fixed_topups():
    value = run(
        "goal",
        months=24,
        cost_today_paise=36000,
        annual_inflation_pct="0",
        timing="end",
        step={"kind": "annual_percent", "value_pct": "100"},
    )
    assert value["summary"]["required_starting_monthly_paise"] == 1000
    zero = run(
        "goal",
        months=24,
        cost_today_paise=12000,
        annual_inflation_pct="0",
        timing="end",
        step={"kind": "annual_fixed", "value_paise": 1000},
    )
    assert zero["summary"]["required_starting_monthly_paise"] == 0


def test_infeasible_goal_and_growth_budget_fail_explicitly():
    with pytest.raises(forward.CalculationUnavailable, match="goal_infeasible"):
        run(
            "goal",
            months=1,
            cost_today_paise=100000000000000,
            annual_inflation_pct="50",
            timing="end",
        )
    with pytest.raises(forward.CalculationUnavailable, match="cashflow_budget"):
        run(
            "stepup_sip",
            months=600,
            monthly_paise=100000000000000,
            timing="end",
            step={"kind": "annual_percent", "value_pct": "100"},
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"months": True},
        {"months": 0},
        {"months": 601},
        {"initial_paise": True},
        {"initial_paise": 0},
        {"annual_return_pct": 10.0},
        {"annual_return_pct": "NaN"},
        {"annual_return_pct": "-100"},
        {"annual_return_pct": "101"},
        {"annual_return_pct": "1e1"},
        {"personal_name": "not allowed"},
    ],
)
def test_invalid_floats_nonfinite_personal_fields_and_budgets_are_rejected(changes):
    with pytest.raises(ValidationError):
        run("lumpsum", **{"initial_paise": 1000, **changes})


def test_policy_replay_normalization_and_decimal_context_are_deterministic():
    payload = {"kind": "lumpsum", "initial_paise": 1000000, **common(annual_return_pct="10.0000")}
    copy = deepcopy(payload)
    expected = forward.derive(payload, forward.policy())
    with localcontext() as c:
        c.prec = 6
        c.rounding = ROUND_DOWN
        c.traps[Inexact] = True
        c.Emax = 20
        assert forward.derive(payload, forward.policy()) == expected
    assert payload == copy
    assert forward.derive({**payload, "annual_return_pct": "10"}, forward.policy()) == expected
    changed = forward.policy()
    changed["version"] = True
    with pytest.raises(ValueError, match="unsupported forward policy"):
        forward.derive(payload, changed)
