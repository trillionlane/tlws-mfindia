"""Explicit response models for the MFData read contract (v2).

FastAPI renders these into the exported OpenAPI, so the contract carries the
actual fields and closed enums for every endpoint changed in the
data-quality / performance-methodology sprint — not generic
additionalProperties objects for those endpoints.

The models are exact mirrors of the JSON the query layer returns: FastAPI
validates each response against its model, so any drift fails loudly at
request time instead of silently widening (or narrowing) the contract.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Closed enums (rendered as literal value lists in OpenAPI)
# ---------------------------------------------------------------------------

LifecycleState = Literal["active", "redeemed", "defunct", "unknown"]
LifecycleEvidence = Literal[
    "amfi_current_feed",
    "amfi_redeemed_marker",
    "amfi_defunct_marker",
    "not_seen_in_latest_feed",
    "insufficient_evidence",
]
FreshnessStatus = Literal["current", "delayed", "stale", "very_stale", "missing"]
AssessmentStatus = Literal["current", "stale", "not_assessed"]
QualitySignal = Literal[
    "constant_nav_series",
    "duplicate_variant_series",
    "terminal_face_value_reset_candidate",
]
DistributionAdjustment = Literal["not_applicable", "unavailable"]
MethodologyBasis = Literal["nav_change"]
Limitation = Literal[
    "distribution_history_unavailable",
    "lifecycle_not_active",
    "nav_freshness_lag",
    "nav_quality_signal",
    "nav_series_unavailable",
]


# ---------------------------------------------------------------------------
# Fund detail and siblings
# ---------------------------------------------------------------------------


class FundLifecycle(BaseModel):
    """Evidence-backed lifecycle. Absence or a stale NAV alone yields
    ``unknown`` — there is deliberately no "matured" boolean in the contract."""

    state: LifecycleState
    evidence: LifecycleEvidence
    last_seen_in_source: date | None = None


class FundNavFreshness(BaseModel):
    """Objective NAV freshness. The bands are server-owned (see
    queries.FRESHNESS_*_DAYS); consumers must not redefine them."""

    dataset_as_of: date | None = None
    latest_nav_date: date | None = None
    lag_days: int | None = None
    status: FreshnessStatus


class FundNavQuality(BaseModel):
    """Durable NAV-quality assessment read-through. Signals are DETECTION
    signals, not proof that a stored NAV is wrong; ``assessment_status``
    describes the assessment's currency, never the fund's cleanliness."""

    assessment_status: AssessmentStatus
    methodology_version: str | None = None
    assessed_dataset_version: int | None = None
    assessed_at: datetime | None = None
    observation_count: int | None = None
    distinct_nav_count: int | None = None
    signals: list[QualitySignal] | None = None


class FundMethodology(BaseModel):
    """How the NAV-derived numeric fields must be read. Only confirmed Growth
    can be eligible; other options lack required distribution/unit-event truth."""

    basis: MethodologyBasis
    distribution_adjustment: DistributionAdjustment
    comparison_eligible: bool
    limitations: list[Limitation]


class FundFact(BaseModel):
    """fund_facts subset served by the detail endpoint (v1 shape unchanged).

    ``status`` / ``transaction_status`` are LEGACY, non-authoritative facts:
    they are not a buyability signal and nothing in the contract is derived
    from them.
    """

    aum: float | None = None
    expense_ratio: float | None = None
    face_value: float | None = None
    inception_date: date | None = None
    sebi_category_name: str | None = None
    asset_class: str | None = None
    sub_asset_class: str | None = None
    taxability: str | None = None
    return_1day: float | None = None
    return_3month: float | None = None
    return_6month: float | None = None
    return_1year: float | None = None
    return_3year: float | None = None
    return_5year: float | None = None
    return_10year: float | None = None
    return_since_launch: float | None = None
    min_initial_investment_amount: float | None = None
    min_subsequent_investment_amount: float | None = None
    is_sip_allowed: bool | None = None
    status: str | None = None
    transaction_status: str | None = None
    benchmark: str | None = None
    benchmark_name: str | None = None
    fund_manager_name: str | None = None
    risk_level: str | None = None
    base_expense_ratio: float | None = None
    registrar_agent: str | None = None
    expense_ratio_history: Any | None = None
    crisil_rating: str | None = None
    sub_type: str | None = None
    exit_load_value: str | None = None
    exit_load: Any | None = None
    lock_in_period: str | None = None
    portfolio_turnover: float | None = None
    return_1week: float | None = None
    return_1month: float | None = None
    return_9month: float | None = None
    sharpe_ratio: float | None = None
    beta: float | None = None
    std_deviation: float | None = None
    risk_rating: str | None = None
    holdings_analysis: Any | None = None
    holdings_maturity: Any | None = None
    category_return: Any | None = None


class FundSibling(BaseModel):
    """One variant-group sibling offered for navigation."""

    amfi_scheme_code: int
    scheme_name: str
    plan_type: str
    option_type: str
    periodicity: str | None = None


class FundFamilyRef(BaseModel):
    """MFData-owned fund-family identity (stable slug + tags)."""

    tlws_mf_id: str
    slug: str
    tags: list[str]


class FundHolding(BaseModel):
    holding_rank: int
    company_name: str | None = None
    sector_name: str | None = None
    nature_name: str | None = None
    weight_pct: float | None = None


class FundDetail(BaseModel):
    """GET /api/funds/{code} — the full v2 detail response."""

    amfi_scheme_code: int
    scheme_name: str
    plan_type: str
    plan_source: str | None = None
    option_type: str
    periodicity: str | None = None
    scheme_type: str
    scheme_category: str
    is_etf: bool
    is_defunct: bool
    is_active: bool
    in_scope: bool
    isin_growth_or_div_payout: str | None = None
    isin_div_reinvest: str | None = None
    isin_primary: str | None = None
    last_seen_in_source: date | None = None
    amfi_amc_name: str
    created_at: datetime
    updated_at: datetime
    latest_nav: float | None = None
    latest_nav_date: date | None = None
    facts: FundFact | None = None
    facts_source_code: int | None = None
    family: FundFamilyRef | None = None
    siblings: list[FundSibling]
    holdings: list[FundHolding]
    lifecycle: FundLifecycle
    nav_freshness: FundNavFreshness
    nav_quality: FundNavQuality


# ---------------------------------------------------------------------------
# Returns and analytics
# ---------------------------------------------------------------------------


class ReturnsResponse(BaseModel):
    """GET /api/funds/{code}/returns — NAV-to-NAV horizon changes.

    ``horizons`` values are NAV change percentages; non-Growth options are not
    established total return (see ``methodology``).
    """

    code: int
    as_of: date | None = None
    horizons: dict[str, float | None]
    lifecycle: FundLifecycle
    nav_freshness: FundNavFreshness
    nav_quality: FundNavQuality
    methodology: FundMethodology


class AnalyticsYearly(BaseModel):
    year: int
    return_pct: float | None = None


class AnalyticsResponse(BaseModel):
    """GET /api/funds/{code}/analytics — NAV-derived risk/behaviour analytics.

    All figures are NAV-to-NAV; for non-Growth options see ``methodology``
    (not established total return, comparison-ineligible).
    """

    code: int
    points: int
    first_date: date | None = None
    as_of: date | None = None
    risk_free_pct: float | None = None
    too_short: bool | None = None
    annualized_return_pct: float | None = None
    annual_vol_pct: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    max_drawdown_pct: float | None = None
    max_dd_peak_date: date | None = None
    max_dd_trough_date: date | None = None
    max_dd_recovery_date: date | None = None
    cagr_pct: float | None = None
    calmar: float | None = None
    win_rate_days_pct: float | None = None
    yearly: list[AnalyticsYearly] | None = None
    monthly: dict[str, dict[str, float]] | None = None
    win_rate_months_pct: float | None = None
    lifecycle: FundLifecycle
    nav_freshness: FundNavFreshness
    nav_quality: FundNavQuality
    methodology: FundMethodology


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


class ComparisonFund(BaseModel):
    amfi_scheme_code: int
    scheme_name: str
    plan_type: str
    option_type: str
    amfi_amc_name: str
    aum: float | None = None
    expense_ratio: float | None = None
    base_expense_ratio: float | None = None
    return_5year: float | None = None
    sharpe_ratio: float | None = None
    beta: float | None = None
    risk_level: str | None = None
    fund_manager_name: str | None = None
    benchmark_name: str | None = None
    inception_date: date | None = None
    registrar_agent: str | None = None
    latest_nav: float | None = None
    latest_nav_date: date | None = None


class ComparisonPoint(BaseModel):
    date: date
    nav: float
    value: float | None = None


class ComparisonItem(BaseModel):
    """One requested identity plus the policy signals needed to decide
    whether its normalized NAV performance may be compared."""

    fund: ComparisonFund
    points: list[ComparisonPoint]
    returns: dict[str, float | None]
    lifecycle: FundLifecycle
    nav_freshness: FundNavFreshness
    nav_quality: FundNavQuality
    methodology: FundMethodology


class CompareResponse(BaseModel):
    """GET /api/compare — identities are retained, while consumers use the
    supplied policy objects to withhold ineligible normalized performance."""

    years: float
    funds: list[ComparisonItem]


# ---------------------------------------------------------------------------
# Movers and category movers
# ---------------------------------------------------------------------------


class MoverItem(BaseModel):
    amfi_scheme_code: int
    scheme_name: str | None = None
    plan_type: str | None = None
    option_type: str | None = None
    amfi_amc_name: str | None = None
    latest_nav: float
    prev_nav: float | None = None
    latest_nav_date: date
    prev_nav_date: date
    pct_change: float


class MoversResponse(BaseModel):
    """GET /api/movers — confirmed Growth option series only."""

    period: str
    direction: str
    as_of: date | None = None
    from_: date | None = Field(default=None, alias="from")
    results: list[MoverItem]


class CategoryMoverItem(BaseModel):
    amfi_scheme_code: int
    scheme_name: str
    scheme_category: str | None = None
    amfi_amc_name: str
    option_type: str | None = None
    latest_nav: float
    latest_nav_date: date
    pct_change: float
    variants: int = Field(ge=1)


class MoverCategory(BaseModel):
    category: str
    funds: int
    gainers: list[CategoryMoverItem]
    losers: list[CategoryMoverItem]


class CategoryMoversResponse(BaseModel):
    """GET /api/movers/categories — comparison-eligible series only."""

    period: str
    as_of: date | None = None
    from_: date | None = Field(default=None, alias="from")
    categories: list[MoverCategory]


# ---------------------------------------------------------------------------
# Peers and risk-reward
# ---------------------------------------------------------------------------


class PeerHorizon(BaseModel):
    peer_count: int
    fund_return: float | None = None
    #: Null when the peer set is too small OR the subject is
    #: comparison-ineligible (e.g. an IDCW option).
    beats_pct: float | None = None
    rank: int | None = None


class PeersResponse(BaseModel):
    """GET /api/funds/{code}/peers — peer population excludes
    comparison-ineligible series."""

    code: int
    category: str | None = None
    horizons: dict[str, PeerHorizon]


class RiskRewardPoint(BaseModel):
    amfi_scheme_code: int
    scheme_name: str
    vol: float | None = None
    return_: float | None = Field(default=None, alias="return")
    max_drawdown: float | None = None
    aum: float | None = None
    self_: bool = Field(alias="self")


class RiskRewardResponse(BaseModel):
    """GET /api/funds/{code}/risk-reward — population excludes
    comparison-ineligible series.

    ``refreshed`` is absent (v1 shape) when the profile table yields no rows.
    """

    code: int
    category: str | None = None
    points: list[RiskRewardPoint]
    refreshed: bool | None = None
