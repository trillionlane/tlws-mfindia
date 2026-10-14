from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    TypeAdapter,
    field_validator,
)

Money = Annotated[StrictInt, Field(ge=0, le=100_000_000_000_000)]
PositiveMoney = Annotated[StrictInt, Field(gt=0, le=100_000_000_000_000)]
Rate = Annotated[StrictStr, Field(pattern=r"^-?(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,4})?$")]


def exact_rate(value, low, high):
    n = Decimal(value)
    if not low <= n <= high:
        raise ValueError("rate outside declared policy bounds")
    if n == 0:
        return "0"
    text = format(n, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PercentStep(StrictModel):
    kind: Literal["annual_percent"]
    value_pct: Rate

    @field_validator("value_pct")
    @classmethod
    def bounded(cls, value):
        return exact_rate(value, 0, 100)


class FixedStep(StrictModel):
    kind: Literal["annual_fixed"]
    value_paise: Money


Step = Annotated[PercentStep | FixedStep, Field(discriminator="kind")]


class Common(StrictModel):
    months: Annotated[StrictInt, Field(ge=1, le=600)]
    annual_return_pct: Rate
    rate_mode: Literal["effective_annual", "nominal_annual_monthly"]

    @field_validator("annual_return_pct")
    @classmethod
    def bounded(cls, value):
        return exact_rate(value, -99, 100)


class Lumpsum(Common):
    kind: Literal["lumpsum"]
    initial_paise: PositiveMoney


class Sip(Common):
    kind: Literal["sip"]
    monthly_paise: PositiveMoney
    timing: Literal["beginning", "end"]


class StepSip(Common):
    kind: Literal["stepup_sip"]
    monthly_paise: PositiveMoney
    timing: Literal["beginning", "end"]
    step: Step


class Swp(Common):
    kind: Literal["swp"]
    initial_paise: PositiveMoney
    withdrawal_paise: Money
    timing: Literal["beginning", "end"]


class Goal(Common):
    kind: Literal["goal"]
    cost_today_paise: PositiveMoney
    annual_inflation_pct: Rate
    timing: Literal["beginning", "end"]
    step: Step | None = None

    @field_validator("annual_inflation_pct")
    @classmethod
    def bounded_inflation(cls, value):
        return exact_rate(value, 0, 50)


Request = Annotated[Lumpsum | Sip | StepSip | Swp | Goal, Field(discriminator="kind")]
REQUEST_ADAPTER = TypeAdapter(Request)


class Month(StrictModel):
    month: int
    opening_paise: int
    contribution_paise: int
    interest_paise: int
    requested_withdrawal_paise: int
    paid_withdrawal_paise: int
    closing_paise: int


class Summary(StrictModel):
    invested_paise: int
    withdrawn_paise: int
    closing_paise: int
    net_gain_paise: int
    depletion_month: int | None
    inflated_goal_paise: int | None
    required_starting_monthly_paise: int | None
    goal_met: bool | None


class Response(StrictModel):
    format: Literal["forward-calculator-result-v1"]
    result_id: str
    policy_hash: str
    engine: dict[str, str]
    request: Request
    units: Literal["INR_paise"]
    basis: Literal["assumed_projection_not_forecast_or_observed_fund_return"]
    schedule: list[Month]
    summary: Summary
    personal_scenario_persisted: Literal[False]
