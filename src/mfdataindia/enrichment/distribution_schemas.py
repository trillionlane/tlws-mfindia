"""Explicit uncertainty in the private observations contract; no accepted events."""

from datetime import date
from typing import Literal
from pydantic import Field
from mfdataindia.enrichment.schemas import Model, Identity
from mfdataindia.enrichment.idcw_discovery import IdcwDiscoveryRequest

HASH = r"^[0-9a-f]{64}$"
AMOUNT = r"^[0-9]{1,9}(?:\.[0-9]{1,8})?$"


class DistributionWindow(Model):
    start: date
    end: date


class DistributionEvent(Model):
    record_date: date
    amount_inr_per_unit: str = Field(pattern=AMOUNT)
    amount_others_inr_per_unit: str = Field(pattern=AMOUNT)
    cum_idcw_nav_inr: str = Field(pattern=AMOUNT)
    amount_basis: Literal["reported_history_not_net_investor_receipt"]
    ex_date: None
    payment_date: None
    reinvestment_date: None
    reinvestment_nav_inr: None
    scheme_levy_inr_per_unit: None
    unit_event_coverage: Literal["unknown"]
    source_role: Literal["history"]
    source_page: int = Field(ge=1, le=400)


class DistributionAnnouncement(Model):
    notice_date: date
    announced_record_date: date
    record_date_business_day_condition: Literal[True]
    announced_maximum_inr_per_unit: str = Field(pattern=AMOUNT)
    face_value_inr_per_unit: str = Field(pattern=AMOUNT)
    pre_notice_nav_inr: str = Field(pattern=AMOUNT)
    pre_notice_nav_date: date
    amount_is_conditional: Literal[True]
    statutory_levy_quantified: Literal[False]
    tds_applicable_rate: None
    stamp_duty_quantified: Literal[False]
    reinvestment_nav_inr: None
    reinvestment_date: None


class DistributionIdentityEvidence(Model):
    amfi_code: int
    scheme: str
    plan: Literal["REGULAR"]
    option: Literal["IDCW"]
    payout_isin: str
    reinvestment_isin: str


class DistributionSnapshot(Model):
    format: Literal["reported-idcw-observations-v1"]
    snapshot_id: str = Field(pattern=HASH)
    code: Literal[101763]
    identity: Identity
    identity_hash: str = Field(pattern=HASH)
    identity_evidence: DistributionIdentityEvidence
    window: DistributionWindow
    history_reporting_month: str
    issuer_history_scope_label: Literal["Past 3 years"]
    request: IdcwDiscoveryRequest
    source_lineage: dict
    extractor_version: str
    policy_hash: str = Field(pattern=HASH)
    engine_code_hash: str = Field(pattern=HASH)
    events: list[DistributionEvent] = Field(max_length=20)
    conditional_announcement: DistributionAnnouncement | None
    coverage_state: Literal["unknown"]
    complete_event_window_proven: Literal[False]
    analytics_eligible: Literal[False]
    freshness_certified: Literal[False]
    universe_complete: Literal[False]
    source_data_replaced: Literal[False]


class DistributionSummary(Model):
    snapshot_id: str
    window: DistributionWindow
    history_reporting_month: str
    observed_event_count: int
    policy_hash: str
    engine_code_hash: str


class DistributionCoverage(Model):
    code: int
    state: Literal[
        "not_captured", "observed", "identity_changed", "conflicting_evidence", "too_many_snapshots"
    ]
    coverage_state: Literal["unknown"]
    complete_event_window_proven: Literal[False]
    analytics_eligible: Literal[False]
    freshness_certified: Literal[False]
    universe_complete: Literal[False]
    source_data_replaced: Literal[False]
    # Null for no captured/current identity evidence; never interpreted as zero payouts.
    observed_record_dates: list[date] | None
    conflicting_record_dates: list[date]
    snapshots: list[DistributionSummary] = Field(max_length=100)
    reason: str


class DistributionResponse(Model):
    code: int
    snapshot_id: str
    state: Literal["observed", "identity_changed", "conflicting_evidence", "too_many_snapshots"]
    coverage_state: Literal["unknown"]
    analytics_eligible: Literal[False]
    reason: str
    conflicting_record_dates: list[date]
    snapshot: DistributionSnapshot | None
