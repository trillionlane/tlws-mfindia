"""Deterministic hypothetical monthly cash flows; never observed-fund analytics."""

from copy import deepcopy
from decimal import Context, Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, localcontext
from decimal import __libmpdec_version__
import hashlib
from fractions import Fraction
import platform
from pathlib import Path

from mfdataindia.calculators import schemas
from mfdataindia.review.policy import fingerprint

ENGINE_VERSION = "forward-monthly-paise-1"
MAX_CASHFLOW = 10**18
MAX_BALANCE = 10**32
_POLICY = {
    "id": "assumed-monthly-cashflow-planning",
    "version": 1,
    "engine_version": ENGINE_VERSION,
    "basis": "hypothetical assumptions; not forecast, fund NAV performance or an execution quote",
    "money": "integer INR paise; no floats or personally identifiable fields",
    "months": "integer periods 1..600, not date/trading-day inference",
    "effective_rate": "monthly=(1+annual_percent/100)^(1/12)-1",
    "nominal_rate": "monthly=annual_percent/1200 as an exact rational; explicitly nominal with monthly compounding; interest ties rounded from the exact ratio",
    "cashflow_timing": "caller chooses beginning or end; lumpsum exists before month one",
    "recurrence": "beginning cashflow -> interest credit -> end cashflow",
    "interest_rounding": "ROUND_HALF_EVEN to one paise EACH month before next month; not unrounded closed-form FV",
    "stepup": "annual boundary before months 13/25/etc; base*(1+g/100)^floor((month-1)/12) rounded HALF_EVEN or base+annual_fixed*year",
    "swp": "paid=min(requested,available integer corpus); partial final payment disclosed; zero corpus stays zero",
    "inflated_goal": "ceil(today_cost*(1+inflation_percent/100)^(months/12)) in paise",
    "reverse_goal": "lowest nonnegative integer starting SIP meeting rounded-engine target; bounded exponential upper bracket then monotone integer binary search; no speculative full-budget projection; infeasible returns explicit error",
    "positive_and_negative_returns": "annual percent -99..100; no zero-rate division; no rate guarantees",
    "precision": 50,
    "rounding": "ROUND_HALF_EVEN",
    "decimal_exponent_limits": [-999999, 999999],
    "maximum_input_money_paise": 10**14,
    "maximum_monthly_cashflow_paise": MAX_CASHFLOW,
    "maximum_balance_paise": MAX_BALANCE,
    "maximum_months": 600,
    "maximum_step_percent": "100",
    "maximum_inflation_percent": "50",
    "reconciliation": "closing=opening+contribution+rounded_interest-paid; gain=closing+withdrawn-invested",
    "undefined": "reject non-finite/unsupported/budget-exceeding inputs; never silently clamp or fabricate zero",
    "persistence": "immutable executable policy only; no personal scenario/request/schedule persistence",
    "lineage": "normalized request + policy hash + code and runtime identity; deterministic result hash",
    "exclusions": [
        "fees",
        "tax",
        "exit loads",
        "fund-specific predictions",
        "distribution adjustment",
        "trading calendars",
    ],
}


class CalculationUnavailable(ValueError):
    pass


def policy():
    return deepcopy(_POLICY)


def validate_policy(document):
    if fingerprint(document) != fingerprint(_POLICY):
        raise ValueError("unsupported forward policy; publish a tested new version")
    return fingerprint(document)


def engine_identity():
    return {
        "version": ENGINE_VERSION,
        "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "decimal_library": __libmpdec_version__,
    }


def monthly_rate(rate, mode):
    value = Decimal(rate)
    if mode == "nominal_annual_monthly":
        return Fraction(value) / 1200
    if value == 0:
        return Decimal(0)
    return ((1 + value / 100).ln() / 12).exp() - 1


def contribution(base, month, step):
    year = (month - 1) // 12
    if step is None:
        value = Decimal(base)
    elif step.kind == "annual_fixed":
        value = Decimal(base + step.value_paise * year)
    else:
        value = Decimal(base) * (1 + Decimal(step.value_pct) / 100) ** year
    amount = int(value.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
    if not 0 <= amount <= MAX_CASHFLOW:
        raise CalculationUnavailable("cashflow_budget_exceeded")
    return amount


def simulate(months, rate, *, initial=0, base=0, step=None, withdrawal=0, timing="end"):
    balance = initial
    invested = initial
    withdrawn = 0
    depletion = None
    rows = []
    for month in range(1, months + 1):
        opening = balance
        deposit = contribution(base, month, step)
        paid = 0
        if timing == "beginning":
            balance += deposit
            paid = min(withdrawal, balance)
            balance -= paid
            if withdrawal and balance == 0 and depletion is None:
                depletion = month
        if isinstance(rate, Fraction):
            # A finite annual input divided by 1200 can repeat forever.
            # Decimal approximation must not move an exact half-paise tie.
            whole, remainder = divmod(balance * rate.numerator, rate.denominator)
            interest = whole + int(2 * remainder > rate.denominator or
                2 * remainder == rate.denominator and whole % 2 == 1)
        else:
            interest = int((Decimal(balance) * rate).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
        balance += interest
        if timing == "end":
            balance += deposit
            paid = min(withdrawal, balance)
            balance -= paid
            if withdrawal and balance == 0 and depletion is None:
                depletion = month
        if not 0 <= balance <= MAX_BALANCE:
            raise CalculationUnavailable("balance_budget_exceeded")
        invested += deposit
        withdrawn += paid
        rows.append(
            {
                "month": month,
                "opening_paise": opening,
                "contribution_paise": deposit,
                "interest_paise": interest,
                "requested_withdrawal_paise": withdrawal,
                "paid_withdrawal_paise": paid,
                "closing_paise": balance,
            }
        )
    return rows, {
        "invested_paise": invested,
        "withdrawn_paise": withdrawn,
        "closing_paise": balance,
        "net_gain_paise": balance + withdrawn - invested,
        "depletion_month": depletion,
        "inflated_goal_paise": None,
        "required_starting_monthly_paise": None,
        "goal_met": None,
    }


def goal(plan, rate):
    factor = (1 + Decimal(plan.annual_inflation_pct) / 100) ** (Decimal(plan.months) / 12)
    target = int(
        (Decimal(plan.cost_today_paise) * factor).to_integral_value(rounding=ROUND_CEILING)
    )
    maximum = 10**14
    if plan.step and plan.step.kind == "annual_percent":
        last_factor = (1 + Decimal(plan.step.value_pct) / 100) ** ((plan.months - 1) // 12)
        maximum = min(
            maximum,
            int((Decimal(MAX_CASHFLOW) / last_factor).to_integral_value(rounding=ROUND_FLOOR)),
        )

    def project(start):
        return simulate(plan.months, rate, base=start, step=plan.step, timing=plan.timing)

    # Trial at the full permitted input can exceed the balance budget even
    # though a tiny feasible SIP meets this goal. Find a sufficient bracket
    # before binary search rather than reject that valid low contribution.
    low, high = 0, 0
    if project(0)[1]["closing_paise"] < target:
        high = min(1, maximum)
        while project(high)[1]["closing_paise"] < target:
            if high == maximum:
                raise CalculationUnavailable("goal_infeasible_with_policy_bounds")
            low = high + 1
            high = min(maximum, high * 2)
    while low < high:
        middle = (low + high) // 2
        if project(middle)[1]["closing_paise"] >= target:
            high = middle
        else:
            low = middle + 1
    rows, summary = project(low)
    if low and project(low - 1)[1]["closing_paise"] >= target:
        raise ValueError("reverse goal minimality violated")
    summary.update(inflated_goal_paise=target, required_starting_monthly_paise=low, goal_met=True)
    return rows, summary


def derive(payload, document):
    ph = validate_policy(document)
    plan = schemas.REQUEST_ADAPTER.validate_python(payload)
    normalized = plan.model_dump(mode="json")
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999)):
        rate = monthly_rate(plan.annual_return_pct, plan.rate_mode)
        if plan.kind == "goal":
            rows, summary = goal(plan, rate)
        else:
            rows, summary = simulate(
                plan.months,
                rate,
                initial=plan.initial_paise if plan.kind in ("lumpsum", "swp") else 0,
                base=plan.monthly_paise if plan.kind in ("sip", "stepup_sip") else 0,
                step=plan.step if plan.kind == "stepup_sip" else None,
                withdrawal=plan.withdrawal_paise if plan.kind == "swp" else 0,
                timing=plan.timing if plan.kind != "lumpsum" else "end",
            )
    result = {
        "format": "forward-calculator-result-v1",
        "policy_hash": ph,
        "engine": engine_identity(),
        "request": normalized,
        "units": "INR_paise",
        "basis": "assumed_projection_not_forecast_or_observed_fund_return",
        "schedule": rows,
        "summary": summary,
        "personal_scenario_persisted": False,
    }
    return {"result_id": fingerprint(result), **result}
