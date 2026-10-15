"""Bounded private observed-overlap projections, not the legacy Jaccard contract."""

from datetime import date
from typing import Literal
from pydantic import Field, StrictInt, model_validator
from mfdataindia.enrichment.schemas import Model


class OverlapPair(Model):
    left_code: StrictInt = Field(gt=0, le=2147483647)
    right_code: StrictInt = Field(gt=0, le=2147483647)
    left_snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    right_snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def ordered(self):
        if self.left_code >= self.right_code:
            raise ValueError("canonical ascending distinct fund codes required")
        return self


class OverlapExclusion(Model):
    row: int
    reason: Literal[
        "non_security_balance", "unresolved_security_identifier", "censored_or_missing_weight"
    ]
    reported_weight_percent: str | None
    upper_bound_exclusive: str | None


class OverlapCoverage(Model):
    code: int
    snapshot_id: str
    holdings_count: int
    eligible_security_count: int
    eligible_reported_weight_percent: str
    excluded_rows: list[OverlapExclusion]
    derivative_positions_excluded: int
    source_unrestricted_overlap_eligible: bool
    concentration_state: Literal["available", "unavailable"]
    top5_observed_weight_percent: str | None
    top10_observed_weight_percent: str | None
    observed_hhi_contribution: str | None


class OverlapShared(Model):
    isin: str
    left_name: str
    right_name: str
    asset_class: Literal["equity", "debt", "government"]
    left_weight_percent: str
    right_weight_percent: str
    overlap_contribution_percent: str


class OverlapResult(OverlapPair):
    format: Literal["reported-isin-overlap-v1"]
    result_id: str
    policy_hash: str
    engine_code_hash: str
    disclosure_as_of: date
    observed_state: Literal["available", "unavailable"]
    observed_reason: str | None
    observed_overlap_percent: str | None
    full_economic_overlap_percent: None
    full_economic_state: Literal["unavailable"]
    full_economic_reason: str
    scope: Literal["observed_reported_isin_securities_only"]
    not_normalized: Literal[True]
    coverage: list[OverlapCoverage] = Field(min_length=2, max_length=2)
    shared_security_count: int
    freshness_certified: Literal[False]
    originals_replaced: Literal[False]


class OverlapDocument(OverlapResult):
    shared_holdings: list[OverlapShared] = Field(max_length=1000)


class OverlapResponse(Model):
    state: Literal["available", "not_published", "inputs_unavailable"]
    reason: str | None
    selection: Literal["current", "explicit_historical_pins"]
    result: OverlapResult | None


class OverlapPage(Model):
    result_id: str
    items: list[OverlapShared]
    next_after_isin: str | None


Pair, Document, Result = OverlapPair, OverlapDocument, OverlapResult
Response, Page = OverlapResponse, OverlapPage
