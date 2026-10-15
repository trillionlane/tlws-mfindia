"""Separate accepted-disclosure contract; the evidence-only v1 contract is frozen."""
from datetime import date
from typing import Literal

from pydantic import Field, StrictInt, model_validator

from mfdataindia.enrichment.schemas import Identity, Model

Hash = str


class Selection(Model):
    document_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_document_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    facts_page: StrictInt = Field(ge=1, le=400)
    identity_page: StrictInt = Field(ge=1, le=400)
    codes: list[StrictInt] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.codes)) != len(self.codes) or any(not 0 < x <= 2147483647 for x in self.codes):
            raise ValueError("bounded unique codes required")
        return self


class Fact(Model):
    status: Literal["accepted", "unavailable"]
    value: str | list[str] | None
    unit: Literal["name", "names", "percent"] | None
    scope: Literal["fund", "regular_plan"] | None
    disclosure_as_of: date
    date_basis: Literal["document_context_not_field_effective_date"]
    raw_value: str | None = Field(max_length=1500)
    source_anchor: str | None
    reason: str | None

    @model_validator(mode="after")
    def consistent(self):
        if self.status == "accepted":
            if self.value is None or self.unit is None or self.scope is None or not self.raw_value or not self.source_anchor or self.reason is not None:
                raise ValueError("accepted fact requires value, scope, unit and evidence")
        elif any(v is not None for v in (self.value, self.unit, self.scope, self.raw_value, self.source_anchor)) or not self.reason:
            raise ValueError("unavailable fact must not carry a usable value")
        return self


class Fields(Model):
    benchmark_name: Fact
    fund_managers: Fact
    base_expense_ratio: Fact
    aum: Fact
    total_expense_ratio: Fact


class Extraction(Model):
    extractor_version: str
    extraction_policy_hash: str
    extraction_engine_hash: str
    text_hash: str
    page: int


class IdentityEvidence(Model):
    variant_label: str
    amfi_code: int
    matched_payout_isin: str
    reinvestment_isin_verified: Literal[False]


class Snapshot(Model):
    format: Literal["official-fund-facts-v1"]
    snapshot_id: str
    code: int
    identity: Identity
    identity_hash: str
    document_id: str
    identity_document_id: str
    content_hash: str
    identity_content_hash: str
    source_url: str
    identity_source_url: str
    disclosure_as_of: date
    fields: Fields
    identity_evidence: IdentityEvidence
    disclosure_extraction: Extraction
    identity_extraction: Extraction
    engine_code_hash: str
    policy_hash: str


class Response(Model):
    format: Literal["official-fund-facts-response-v1"]
    code: int
    state: Literal["available", "not_captured", "identity_changed", "conflicting"]
    reason: str | None
    snapshot: Snapshot | None
    conflicting_snapshot_ids: list[str]
    selected_disclosure_as_of: date | None
    freshness_certified: Literal[False]
    universe_complete: Literal[False]
    originals_replaced: Literal[False]


class Page(Model):
    items: list[str]
    next_after: str | None


class PolicyResponse(Model):
    policy_hash: str
    document: dict
