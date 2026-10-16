"""Additive monthly reporting contract; no v1/v2/v3 response reinterpretation."""

from datetime import date
from typing import Literal

from pydantic import Field, JsonValue, StrictInt, field_validator, model_validator

from mfdataindia.enrichment.schemas import Identity, Intake, Model

Hash = str


class Selection(Model):
    intake: Intake
    identity_document_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    detail_page: StrictInt = Field(ge=1, le=399)
    changes_page: StrictInt = Field(ge=2, le=400)
    identity_page: StrictInt = Field(ge=1, le=400)

    @model_validator(mode="after")
    def bounded(self):
        from mfdataindia.enrichment.monthly_facts import COHORT, facts
        from mfdataindia.ingest.amc_factsheets.download import factsheet_media_url

        if (
            self.intake.media_type != "application/pdf"
            or self.intake.document_as_of is None
            or self.intake.amc != facts.AMC
            or set(self.intake.codes) != COHORT
            or self.changes_page != self.detail_page + 1
            or self.intake.source_url != self.intake.final_url
            or self.intake.official_hosts != ["mutualfund.adityabirlacapital.com"]
        ):
            raise ValueError(
                "exact bounded monthly book, two variants and adjacent changes page required"
            )
        factsheet_media_url(self.intake.final_url)
        return self


class MonthlyFact(Model):
    status: Literal["accepted", "unavailable"]
    value: JsonValue
    unit: str | None = Field(max_length=100)
    scope: str | None = Field(max_length=150)
    disclosure_as_of: date
    date_basis: Literal["document_context_not_field_effective_date", "explicit_field_date"]
    field_effective_date: date | None
    raw_value: str | None = Field(max_length=10000)
    source_anchor: str | None = Field(max_length=300)
    reason: str | None = Field(max_length=300)

    @field_validator("value")
    @classmethod
    def no_binary_floats(cls, value):
        def check(item):
            if isinstance(item, float):
                raise ValueError("reported numeric values must retain decimal strings")
            if isinstance(item, dict):
                for child in item.values():
                    check(child)
            if isinstance(item, list):
                for child in item:
                    check(child)

        check(value)
        return value

    @model_validator(mode="after")
    def consistent(self):
        if self.status == "accepted":
            if (
                self.value is None
                or not self.unit
                or not self.scope
                or not self.raw_value
                or not self.source_anchor
                or self.reason is not None
            ):
                raise ValueError(
                    "accepted monthly fact requires exact reported value, scope and source"
                )
        elif (
            self.value is not None
            or self.unit is not None
            or self.scope is not None
            or not self.reason
        ):
            raise ValueError("unavailable fact cannot masquerade as a usable value")
        if (self.date_basis == "explicit_field_date") != (self.field_effective_date is not None):
            raise ValueError("field date basis differs")
        return self


class Snapshot(Model):
    format: Literal["official-monthly-fund-facts-v1"]
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    code: StrictInt
    identity: Identity
    identity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_document_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_url: str
    identity_source_url: str
    disclosure_as_of: date
    fields: dict[str, MonthlyFact] = Field(min_length=1, max_length=48)
    identity_evidence: dict
    detail_extraction: dict
    changes_extraction: dict
    identity_extraction: dict
    verification_status: Literal["needs_verification"]
    canonical_analytics_replaced: Literal[False]
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class Response(Model):
    format: Literal["official-monthly-fund-facts-response-v1"]
    code: int
    state: Literal["available", "not_captured", "identity_changed", "conflicting"]
    snapshot: Snapshot | None
    conflicting_snapshot_ids: list[str] = Field(max_length=100)
    original_records_replaced: Literal[False]
    canonical_analytics_replaced: Literal[False]
    current_core_projection_includes_monthly_snapshot: bool
    freshness_certified: Literal[False]
    universe_complete: Literal[False]


class PolicyResponse(Model):
    policy_hash: str
    document: dict
