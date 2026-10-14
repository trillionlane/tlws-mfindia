"""No investor identifiers or executable cash-flow instructions accepted."""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, StrictInt, StrictStr, TypeAdapter, field_validator, model_validator
from mfdataindia.calculators.schemas import PositiveMoney, StrictModel
from mfdataindia.review.schemas import Context

Day = Annotated[StrictStr, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]


class Common(StrictModel):
    code: Annotated[StrictInt, Field(gt=0, le=2147483647)]
    start_date: Day
    end_date: Day

    @field_validator("start_date", "end_date")
    @classmethod
    def valid_day(cls, value):
        parsed = date.fromisoformat(value)
        if not 1900 <= parsed.year <= 2100:
            raise ValueError("unsupported calendar year")
        return value

    @model_validator(mode="after")
    def bounded_range(self):
        start, end = date.fromisoformat(self.start_date), date.fromisoformat(self.end_date)
        months = (end.year - start.year) * 12 + end.month - start.month + 1
        if end < start or months > 600:
            raise ValueError("ordered range within 600 calendar months required")
        return self


class Lumpsum(Common):
    kind: Literal["lumpsum"]
    initial_paise: PositiveMoney


class Sip(Common):
    kind: Literal["sip"]
    monthly_paise: PositiveMoney
    day_of_month: Annotated[StrictInt, Field(ge=1, le=31)]


Request = Annotated[Lumpsum | Sip, Field(discriminator="kind")]
REQUEST_ADAPTER = TypeAdapter(Request)


class Purchase(StrictModel):
    requested_date: str
    price_date: str
    shift_calendar_days: int
    nav_inr: str
    contribution_paise: int
    units_purchased_display: str


class Summary(StrictModel):
    invested_paise: int
    terminal_paise: int
    net_gain_paise: int
    period_gain_pct: str
    units_held_display: str
    valuation_date: str
    valuation_nav_inr: str
    valuation_lag_calendar_days: int
    largest_observed_gap_calendar_days: int
    xirr_pct: str | None
    xirr_unavailable_reason: str | None
    xirr_residual_paise: str | None


class Response(StrictModel):
    format: Literal["historical-growth-calculator-result-v1"]
    result_id: str
    policy_hash: str
    engine: dict[str, str]
    request: Request
    input_hash: str
    source_revision: int
    dataset_version: int
    context: Context
    basis: Literal["historical_observed_growth_nav_simulation_not_execution_or_forecast"]
    units: Literal["INR_paise"]
    comparison_eligible: Literal[False]
    personal_scenario_persisted: Literal[False]
    available: bool
    unavailable_reason: str | None
    purchases: list[Purchase]
    summary: Summary | None
