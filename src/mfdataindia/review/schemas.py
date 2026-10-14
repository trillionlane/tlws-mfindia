"""Separate versioned enhancement contract; existing v2 models stay unchanged."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator

from mfdataindia.api.schemas import FundLifecycle, FundNavFreshness, FundNavQuality, FundMethodology

Status = Literal["pending_review", "in_review", "reviewed", "needs_evidence"]
Disposition = Literal["not_determined", "consistent_with_evidence", "discrepancy_candidate", "insufficient_evidence"]


class Mutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: StrictInt = Field(ge=0, le=9223372036854775807)
    expected_input_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    result_id: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    reason: StrictStr = Field(min_length=1, max_length=4000)
    evidence: list[StrictStr] = Field(default_factory=list, max_length=20)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value

    @field_validator("evidence")
    @classmethod
    def evidence_bounds(cls, values):
        if any(not v.strip() or len(v) > 1000 for v in values) or len(set(values)) != len(values):
            raise ValueError("evidence references must be bounded and unique")
        return values


class ReviewMutation(Mutation):
    status: Status
    disposition: Disposition

    @model_validator(mode="after")
    def evidence_required(self):
        if self.status == "reviewed" and (not self.evidence or self.disposition == "not_determined"):
            raise ValueError("reviewed status requires evidence and a disposition")
        return self


class EnrichmentField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["aum", "expense_ratio", "benchmark", "benchmark_name", "fund_manager_name", "inception_date", "scheme_name", "scheme_category"]
    proposed_value: StrictStr = Field(min_length=1, max_length=1000)


class ProposalMutation(Mutation):
    fields: list[EnrichmentField] = Field(min_length=1, max_length=8)

    @field_validator("fields")
    @classmethod
    def unique_fields(cls, values):
        if len({v.field for v in values}) != len(values):
            raise ValueError("proposal fields must be unique")
        return values


class EventResponse(BaseModel):
    version: int
    kind: Literal["review", "proposal"]
    status: Status
    result_id: str


class PolicyResponse(BaseModel):
    policy_hash: str
    policy_id: str
    policy_version: int
    document: dict


class Context(BaseModel):
    lifecycle: FundLifecycle
    nav_freshness: FundNavFreshness
    nav_quality: FundNavQuality
    methodology: FundMethodology


class Metrics(BaseModel):
    cumulative_return_pct: str | None
    cagr_pct: str | None
    annual_volatility_pct: str | None
    sharpe: str | None
    sortino: str | None
    max_drawdown_pct: str | None
    calmar: str | None


class ResultDocument(BaseModel):
    result_id: str
    format: Literal["verified-nav-result-v1"]
    code: int
    input_hash: str
    policy_hash: str
    engine_version: str
    engine_fingerprint: str
    engine: dict[str, str]
    source_revision: int
    dataset_version: int
    first_date: str | None
    as_of: str | None
    points: int
    context: Context
    eligible_at_capture: bool
    metrics: Metrics
    unavailable: dict[str, str]


class ResultResponse(BaseModel):
    result: ResultDocument
    input_status: Literal["current", "stale"]
    current_input_hash: str
    comparison_eligible: bool


class CertifiedWindow(BaseModel):
    result_id: str
    input_hash: str
    certificate_id: str
    certified_at: datetime
    runtime_git_sha: str
    source_revision: int
    dataset_version: int
    first_date: str | None
    as_of: str | None
    captured_context: Context
    metric_policy_hash: str
    metrics: Metrics | None
    unavailable: dict[str, str]


class PublicationResponse(BaseModel):
    format: Literal['certified-research-publication-v1']
    code: int
    publication_policy_hash: str
    certificate_scope: Literal['provider_contract_conformance_not_fund_correctness']
    state: Literal['not_certified', 'current_artifact_certified', 'historical_certified', 'historical_invalidated']
    current_input_hash: str
    current_dataset_version: int
    current_source_revision: int
    current_context: Context
    current_comparison_eligible: bool
    historical_window_reusable: bool
    last_certified: CertifiedWindow | None
    withheld_reasons: list[str]


class HistoryEvent(EventResponse):
    caller: str
    actor: str
    document: dict
    recorded_at: datetime


class ReviewResponse(BaseModel):
    code: int
    version: int
    status: Status
    needs_revalidation: bool
    current_input_hash: str
    identity: dict
    original_facts: dict | None
    context: Context
    history: list[HistoryEvent]
    next_after_version: int | None


class QueueItem(BaseModel):
    code: int
    scheme_name: str
    option_type: str
    version: int
    status: Status
    signals: list[str] | None
    reviewed_input_hash: str | None
    needs_revalidation: bool | None


class QueueResponse(BaseModel):
    items: list[QueueItem]
    next_after_code: int | None
    revalidation_basis: str
