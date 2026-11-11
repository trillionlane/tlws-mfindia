"""Closed observed-exposure contract, distinct from fund-tag and news v1."""
from typing import Literal
from pydantic import Field
from .schemas import TagModel
from . import exposure_policy as p

Section = Literal['positions','securities','entities','attributes']
State = Literal['available','not_captured','identity_changed','conflicting']
Reason = Literal['no_accepted_complete_portfolio','current_identity_differs','same_date_distinct_retained_sources']


class ExposureError(TagModel):
    detail: str


class ExposureWeight(TagModel):
    lower: str = Field(max_length=160,pattern=r'^\d+(?:\.\d+)?$')
    upper: str = Field(max_length=160,pattern=r'^\d+(?:\.\d+)?$')
    upper_exclusive: bool
    unit: Literal['percent_of_original_disclosed_AUM']


class ExposurePositionWeight(TagModel):
    lower: str = Field(max_length=160,pattern=r'^-?\d+(?:\.\d+)?$')
    upper: str = Field(max_length=160,pattern=r'^-?\d+(?:\.\d+)?$')
    upper_exclusive: bool
    unit: Literal['percent_of_original_disclosed_AUM']


class ExposurePosition(TagModel):
    item_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    position_kind: Literal['holding','derivative']
    row: int = Field(gt=0)
    source_label: str = Field(min_length=1,max_length=500)
    asset_class: Literal[
        'equity','debt','government','cash','current_assets_liabilities','derivative'
    ]
    identifier: str | None = Field(max_length=200)
    identifier_kind: Literal['isin','issuer_local','not_applicable']
    direction: Literal['Long','Short'] | None
    weight: ExposurePositionWeight
    mapping_state: Literal[
        'mapped','unmapped','ambiguous','identity-unverified','entity-kind-conflict',
        'not-applicable'
    ]
    entity_id: str | None = Field(pattern='^'+p.ENTITY.pattern+'$')
    evidence_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class ExposureSecurity(TagModel):
    item_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    security_id: str = Field(pattern=r'^[A-Z]{2}[A-Z0-9]{9}[0-9]$')
    row: int = Field(gt=0)
    asset_class: Literal['equity','debt','government']
    weight: ExposureWeight


class ExposureEntity(TagModel):
    item_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    entity_id: str = Field(pattern='^'+p.ENTITY.pattern+'$')
    entity_kind: Literal['company','sovereign','fund','other']
    weight: ExposureWeight
    rows: list[int] = Field(max_length=1000)
    security_ids: list[str] = Field(max_length=1000)
    evidence_hashes: list[str] = Field(max_length=1000)


class ExposureAttribute(TagModel):
    item_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    dimension: Literal['issuer-domicile','sector','theme']
    value: str = Field(pattern='^'+p.VALUE.pattern+'$')
    weight: ExposureWeight
    entity_ids: list[str] = Field(max_length=1000)
    evidence_hashes: list[str] = Field(max_length=1000)


class ExposureMetadata(TagModel):
    format: Literal[p.VERSION]
    assessment_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    derivation_policy_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    code: int = Field(gt=0,lt=2**31)
    portfolio_snapshot_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    portfolio_as_of: str = Field(pattern=r'^\d{4}-\d\d-\d\d$')
    portfolio_engine_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_content_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    identity_content_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    registry_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    observed_on: str = Field(pattern=r'^\d{4}-\d\d-\d\d$')
    evidence_age_days: int = Field(ge=0)
    freshness_certified: Literal[False]
    basis: Literal['dated_held_issuer_attributes_not_scheme_mandate']
    statement_complete: Literal[True]
    holdings_count: int = Field(ge=1,le=1000)
    mapped_holding_count: int = Field(ge=0,le=1000)
    withheld_row_reasons: dict[Literal['NON_ISSUER_CASH_OR_NET_BALANCE','SECURITY_IDENTITY_UNVERIFIED',
        'SECURITY_ENTITY_AMBIGUOUS','SECURITY_ENTITY_NOT_MAPPED_AT_DATE','ENTITY_ATTRIBUTE_CONFLICT',
        'ENTITY_KIND_CONFLICT','FUND_LOOKTHROUGH_UNAVAILABLE','ENTITY_CLASSIFICATION_UNAVAILABLE'],int]
    derivative_position_count: int = Field(ge=0)
    lookthrough_performed: Literal[False]
    absence_or_exclusion_certified: Literal[False]
    originals_replaced: Literal[False]


class ExposureSummary(TagModel):
    format: Literal[p.VERSION]
    code: int = Field(gt=0,lt=2**31)
    state: State
    reason: Reason | None
    metadata: ExposureMetadata | None
    entity_count: int = Field(ge=0,le=1000)
    attribute_count: int = Field(ge=0)
    security_count: int = Field(ge=0,le=1000)
    position_count: int = Field(ge=0,le=2000)
    related_news_linkage: Literal['requires_verified_article_entity_evidence_in_TI']
    exact_scheme_news_unchanged: Literal[True]


class ExposurePage(TagModel):
    format: Literal[p.VERSION]
    code: int = Field(gt=0,lt=2**31)
    state: State
    reason: Reason | None
    section: Section
    assessment_id: str | None = Field(pattern=r'^[0-9a-f]{64}$')
    portfolio_as_of: str | None = Field(pattern=r'^\d{4}-\d\d-\d\d$')
    items: list[ExposurePosition | ExposureSecurity | ExposureEntity | ExposureAttribute] = Field(max_length=50)
    next_after: str | None = Field(pattern=r'^[0-9a-f]{64}$')
    limit: int = Field(ge=1,le=50)
    absence_or_exclusion_certified: Literal[False]


class ExposureDiscovery(TagModel):
    format: Literal[p.VERSION]
    source_format: Literal['official-complete-portfolio-v1']
    source_enrollment_verified: Literal[True]
    retained_snapshots: int = Field(ge=0)
    first_reporting_date: str | None = Field(pattern=r'^\d{4}-\d\d-\d\d$')
    last_reporting_date: str | None = Field(pattern=r'^\d{4}-\d\d-\d\d$')
    coverage_basis: Literal['retained_snapshots_not_current_universe_coverage']
    entity_registry_format: Literal[p.REGISTRY_VERSION]
    entity_registry_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    entity_mapping_records: int = Field(ge=0,le=p.MAX_REGISTRY_RECORDS)
    sections: list[Section]
    funds_url: str
    related_news_requires_verified_bridge: Literal[True]
    absence_or_exclusion_certified: Literal[False]
    freshness_certified: Literal[False]
