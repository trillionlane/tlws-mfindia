"""Strict additive private full-portfolio contract; never replaces legacy holdings."""

from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit
from pydantic import Field, StrictInt, model_validator
from mfdataindia.enrichment.schemas import Model, Identity
from mfdataindia.enrichment.evidence import public_url

Hash = str


class Intake(Model):
    source_url: str = Field(max_length=2048)
    listing_url: str = Field(max_length=2048)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: Literal["application/zip", "application/pdf"]
    observed_at: datetime
    disclosure_as_of: date | None
    use_basis: Literal["operator_reported_authorization"]
    authority_reference: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def bounded(self):
        hosts = {"mutualfund.adityabirlacapital.com", "nsearchives.nseindia.com"}
        public_url(self.source_url, hosts)
        public_url(self.listing_url, hosts)
        if (
            self.observed_at.tzinfo is None
            or self.disclosure_as_of
            and self.disclosure_as_of > self.observed_at.date()
        ):
            raise ValueError("timezone-aware observation and valid disclosure date required")
        if any(ord(x) < 32 for x in self.authority_reference):
            raise ValueError("unsafe authority reference")
        host = urlsplit(self.source_url).hostname
        if self.media_type == "application/zip" and (
            host != "mutualfund.adityabirlacapital.com"
            or not self.source_url.startswith(
                "https://mutualfund.adityabirlacapital.com/-/media/bsl/files/resources/monthly-portfolio/"
            )
        ):
            raise ValueError("official monthly portfolio path required")
        if self.media_type == "application/pdf" and not self.source_url.startswith(
            "https://nsearchives.nseindia.com/trading_security/mf/pdf/Aditya_"
        ):
            raise ValueError("official issuer summary path required")
        return self


class Target(Model):
    code: StrictInt = Field(gt=0, le=2147483647)
    sheet: str = Field(pattern=r"^[A-Z0-9]{1,20}$")
    identity_page: StrictInt = Field(ge=1, le=400)


class Selection(Model):
    source_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_source_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    targets: list[Target] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def unique(self):
        if len({t.code for t in self.targets}) != len(self.targets) or len(
            {t.sheet for t in self.targets}
        ) != len(self.targets):
            raise ValueError("distinct fund portfolios required")
        return self


class Weight(Model):
    value: str | None
    basis: Literal["reported", "censored"]
    upper_bound_exclusive: str | None
    raw: str


class Holding(Model):
    row: int
    name: str
    asset_class: Literal["equity", "debt", "government", "cash", "current_assets_liabilities"]
    identifier: str | None
    identifier_kind: Literal["isin", "issuer_local", "not_applicable"]
    industry_or_rating: str | None
    quantity: str | None
    market_value_inr_lakh: str
    weight_percent: Weight
    raw_cells: list[str]


class Subtotal(Model):
    row: int
    market_value_inr_lakh: str
    weight_percent: Weight


class Derivative(Model):
    row: int
    underlying_name: str
    direction: Literal["Long", "Short"]
    purchase_price_inr: str
    current_price_inr: str
    margin_inr_lakh: str
    reported_exposure_percent: str
    identifier: None
    quantity: None
    notional_inr: None


class PortfolioIdentityEvidence(Model):
    variant_label: str
    amfi_code: int
    payout_isin: str
    scope: Literal["fund_shared_portfolio"]


class Metadata(Model):
    format: Literal["official-complete-portfolio-v1"]
    snapshot_id: str
    code: int
    identity: Identity
    identity_hash: str
    source_id: str
    identity_source_id: str
    content_hash: str
    identity_content_hash: str
    source_url: str
    identity_source_url: str
    disclosure_as_of: date
    identity_evidence: PortfolioIdentityEvidence
    subtotals: list[Subtotal]
    grand_total_inr_lakh: str
    grand_total_row: int
    derivative_positions: list[Derivative]
    statement_complete: Literal[True]
    overlap_eligible: bool
    overlap_limitation: str | None
    holdings_count: int
    sheet: str
    member_name: str
    member_hash: str
    extractor_version: str
    identity_extraction: dict
    sheet_cells_hash: str
    engine_code_hash: str
    policy_hash: str


class Snapshot(Metadata):
    notes: list[dict]
    holdings: list[Holding] = Field(min_length=1, max_length=1000)


class Response(Model):
    code: int
    state: Literal["available", "not_captured", "identity_changed", "conflicting"]
    reason: str | None
    portfolio: Metadata | None
    conflicting_snapshot_ids: list[str]
    freshness_certified: Literal[False]
    universe_complete: Literal[False]
    originals_replaced: Literal[False]


class HoldingsPage(Model):
    snapshot_id: str
    items: list[Holding]
    next_after_row: int | None


class SnapshotPage(Model):
    items: list[str]
    next_after: str | None


class PolicyResponse(Model):
    policy_hash: str
    document: dict
