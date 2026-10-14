from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mfdataindia.review.schemas import Context


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Window(StrictModel):
    end_boundary: str
    start_boundary: str
    start_price_date: str | None
    end_price_date: str | None
    start_boundary_lag_days: int | None
    end_boundary_lag_days: int | None
    elapsed_calendar_days: int | None
    observations: int | None
    largest_observed_gap_calendar_days: int | None
    annualized_return_pct: str | None = Field(pattern=r"^-?[0-9]+\.[0-9]{8}$")
    unavailable_reason: str | None


class Summary(StrictModel):
    scheduled_windows: int
    available_windows: int
    unavailable_windows: int
    minimum_pct: str | None
    maximum_pct: str | None
    median_pct: str | None
    arithmetic_mean_pct: str | None
    positive_windows_pct: str | None
    unavailable_reason: str | None


class Term(StrictModel):
    years: Literal[1, 3, 5]
    windows: list[Window]
    summary: Summary


class RollingData(StrictModel):
    analysis_date: str | None
    schedule: Literal["completed_calendar_month_end"]
    terms: list[Term]
    unavailable_reason: str | None


class ResultDocument(StrictModel):
    result_id: str
    format: Literal["rolling-nav-result-v1"]
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
    rolling: RollingData


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
    rolling: RollingData | None


class Response(StrictModel):
    format: Literal["rolling-returns-response-v1"]
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


class TermSummary(StrictModel):
    years: Literal[1, 3, 5]
    summary: Summary


class PublicRolling(StrictModel):
    analysis_date: str | None
    schedule: Literal["completed_calendar_month_end"]
    terms: list[TermSummary]
    unavailable_reason: str | None


class PublicArtifact(Artifact):
    rolling: PublicRolling | None


class Pagination(StrictModel):
    years: Literal[1, 3, 5]
    result_id: str | None
    limit: int
    total_windows: int
    returned_windows: int
    next_cursor: str | None


class PagedResponse(Response):
    artifact: PublicArtifact | None
    windows: list[Window]
    pagination: Pagination
