"""Closed sustainability/custom-values response models."""
from typing import Literal

from pydantic import Field

from .schemas import TagModel
from . import values_policy as policy


ValuesState = Literal['supported', 'contradicted', 'insufficient-evidence', 'not-assessed']
ScreenKind = Literal['declared-esg', 'environmental', 'custom-values']


class ValuesError(TagModel):
    detail: str


class ValuesWeight(TagModel):
    lower: str = Field(pattern=r'^\d+(?:\.\d+)?$', max_length=160)
    upper: str = Field(pattern=r'^\d+(?:\.\d+)?$', max_length=160)
    upper_exclusive: bool
    unit: Literal['percent_of_original_disclosed_AUM']


class ActivityExposure(TagModel):
    activity: str = Field(pattern='^' + policy.ACTIVITY.pattern + '$')
    weight: ValuesWeight
    entity_ids: list[str] = Field(max_length=1000)
    evidence_hashes: list[str] = Field(max_length=1000)


class ValuesAssessment(TagModel):
    format: Literal[policy.VERSION]
    assessment_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    screen_id: str = Field(pattern='^' + policy.SCREEN.pattern + '$')
    screen_label: str = Field(min_length=1, max_length=120)
    screen_kind: ScreenKind
    state: ValuesState
    reasons: list[Literal[
        'SCREEN_NOT_ACTIVE_AT_PORTFOLIO_DATE', 'PORTFOLIO_EVIDENCE_STALE',
        'PORTFOLIO_ENTITY_COVERAGE_INCOMPLETE', 'FUND_LOOKTHROUGH_INCOMPLETE',
        'DERIVATIVE_CLASSIFICATION_INCOMPLETE', 'ENTITY_ACTIVITY_CLASSIFICATION_INCOMPLETE',
        'EXCLUSION_THRESHOLD_EXCEEDED', 'EXCLUSION_THRESHOLD_UNCERTAIN',
        'INCLUSION_THRESHOLD_NOT_MET', 'INCLUSION_THRESHOLD_UNCERTAIN',
        'ALL_EXPLICIT_CRITERIA_MET'
    ]] = Field(min_length=1, max_length=20)
    code: int = Field(gt=0, lt=2**31)
    portfolio_snapshot_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    portfolio_as_of: str = Field(pattern=r'^\d{4}-\d\d-\d\d$')
    exposure_assessment_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    screen_registry_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    activity_registry_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    screen_evidence_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    activity_exposures: list[ActivityExposure] = Field(max_length=100)
    certification_claim: Literal[False]
    custom_screen_not_religious_or_regulatory_certification: Literal[True]
    missing_evidence_never_means_zero: Literal[True]


class ScreenCriterion(TagModel):
    activity: str = Field(pattern='^' + policy.ACTIVITY.pattern + '$')
    threshold_percent: str = Field(pattern=r'^\d+(?:\.\d+)?$', max_length=160)
    comparison: Literal['maximum', 'minimum']


class ScreenDefinition(TagModel):
    screen_id: str = Field(pattern='^' + policy.SCREEN.pattern + '$')
    label: str = Field(min_length=1, max_length=120)
    kind: ScreenKind
    status: Literal['active', 'draft']
    valid_from: str = Field(pattern=r'^\d{4}-\d\d-\d\d$')
    valid_until: str | None = Field(pattern=r'^\d{4}-\d\d-\d\d$')
    max_evidence_age_days: int = Field(ge=0, le=3660)
    require_complete_entity_mapping: Literal[True]
    require_lookthrough: bool
    require_derivative_classification: bool
    criteria: list[ScreenCriterion] = Field(min_length=1, max_length=100)
    certification_claim: Literal[False]
    interpretation: str = Field(min_length=1, max_length=500)
    evidence_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class ValuesDiscovery(TagModel):
    format: Literal[policy.VERSION]
    screen_registry_format: Literal[policy.SCREEN_FORMAT]
    activity_registry_format: Literal[policy.ACTIVITY_FORMAT]
    screen_registry_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    activity_registry_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    screen_definitions: list[ScreenDefinition] = Field(max_length=policy.MAX_SCREENS)
    activity_mapping_records: int = Field(ge=0, le=policy.MAX_RECORDS)
    funds_url: str
    positive_live_assignments_claimed: Literal[False]
    certification_claimed: Literal[False]
    exact_scheme_news_unchanged: Literal[True]


class ValuesSummary(TagModel):
    format: Literal[policy.VERSION]
    code: int = Field(gt=0, lt=2**31)
    exposure_state: Literal['available', 'not_captured', 'identity_changed', 'conflicting']
    exposure_reason: Literal[
        'no_accepted_complete_portfolio', 'current_identity_differs',
        'same_date_distinct_retained_sources'
    ] | None
    portfolio_as_of: str | None = Field(pattern=r'^\d{4}-\d\d-\d\d$')
    assessments: list[ValuesAssessment] = Field(max_length=policy.MAX_SCREENS)
    positive_live_assignments_claimed: Literal[False]
    certification_claimed: Literal[False]

