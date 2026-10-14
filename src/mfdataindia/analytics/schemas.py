from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mfdataindia.review.schemas import Context


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Period(StrictModel):
    kind: Literal["calendar_year", "year_to_date", "month"]
    label: str
    start_boundary: str
    end_boundary: str
    start_price_date: str | None
    end_price_date: str | None
    start_boundary_lag_days: int | None
    end_boundary_lag_days: int | None
    return_pct: str | None = Field(pattern=r"^-?[0-9]+\.[0-9]{8}$")
    unavailable_reason: str | None


class CalendarData(StrictModel):
    analysis_date: str | None
    current_month_complete: bool
    calendar_years: list[Period]
    year_to_date: Period | None
    months: list[Period]
    unavailable_reason: str | None


class ResultDocument(StrictModel):
    result_id: str
    format: Literal["calendar-nav-result-v1"]
    code: int
    input_hash: str
    policy_hash: str
    engine_version: str
    engine_fingerprint: str
    engine: dict[str, str]
    source_revision: int
    dataset_version: int
    context: Context
    eligible_at_capture: bool
    first_date: str | None
    as_of: str | None
    points: int
    calendar: CalendarData


class Artifact(StrictModel):
    result_id: str
    input_hash: str
    dataset_version: int
    source_revision: int
    first_date: str | None
    as_of: str | None
    policy_hash: str
    engine: dict[str, str]
    context: Context
    certificate_id: str
    certified_at: datetime
    runtime_git_sha: str
    calendar: CalendarData | None


class Response(StrictModel):
    format: Literal["calendar-returns-response-v1"]
    code: int
    current_input_hash: str
    current_dataset_version: int
    current_context: Context
    policy_hash: str
    policy_scope: Literal["provider_contract_conformance_not_fund_correctness"]
    current_comparison_eligible: bool
    historical_window_reusable: bool
    withheld_reasons: list[str]
    artifact: Artifact | None
    audit_only: Literal[False]
    state: Literal[
        "not_certified",
        "current_artifact_certified",
        "historical_certified",
        "historical_invalidated",
    ]


class AuditResponse(StrictModel):
    audit_only: Literal[True]
    result: ResultDocument
    current_input_hash: str
    current_context: Context
    input_status: Literal["current", "stale"]
