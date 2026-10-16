"""Current official values, separate verification status and retained originals."""

from copy import deepcopy
from datetime import date
from typing import Literal

from pydantic import Field

from mfdataindia.enrichment.schemas import Model

FIELDS = {
    "benchmark_name": ["benchmark_name", "benchmark"],
    "fund_managers": ["fund_manager_name"],
    "base_expense_ratio": ["base_expense_ratio"],
}
_POLICY = {
    "id": "current-official-fund-facts",
    "version": 1,
    "sources": "retained identity-sealed official-fund-facts-v1 disclosures",
    "selection": "latest unambiguous disclosed date PER FIELD, not fetch time; identical same-date values corroborate; newer conflicts retain the previous dated official value, or baseline if none, with an explicit conflict state",
    "validation": "finite bounded BER decimal text, nonempty benchmark name and bounded manager-name array; unusable later assertions retain the previous dated usable value with an explicit warning; numerically identical decimal spellings corroborate",
    "numeric_display": "trim insignificant trailing decimal zeros for the current BER value only; retain every original token in source evidence; no rounding or percent conversion",
    "priority": "dated accepted official disclosure over legacy facts of unverified field-effective date; baseline remains retained/recoverable",
    "missing": "a field omitted or unavailable in a later document does not erase its last disclosed value/date",
    "identity": "exact current code/AMC/name/plan/option/ISIN set; never borrow across variants",
    "verification": "source-validated does not imply human-reviewed; initially needs_verification; routine current publication is not blocked by absent human review",
    "dates": "document-context disclosure date, not invented field-effective date; latest retained is not current-month freshness certification",
    "api": "existing current_fund_facts projection serves the selected values everywhere; private metadata gives provenance, dates and conflict/review state",
    "supported_fields": FIELDS,
    "exclusions": [
        "risk level until an explicit layout adapter is implemented",
        "BER-to-TER conversion",
        "new analytics eligibility",
        "raw source writes",
        "UI",
    ],
    "recovery": "restore captured baseline view definition; keep all original and appended records",
}


def policy():
    return deepcopy(_POLICY)


class CurrentFact(Model):
    state: Literal["available", "conflicting", "unusable", "identity_changed", "not_captured"]
    value: str | list[str] | None
    disclosure_as_of: date | None
    latest_disclosure_as_of: date | None
    date_basis: str
    verification_status: Literal["needs_verification", "verified", "needs_revalidation"]
    freshness_status: Literal["latest_retained_not_current_month_certified"]
    reason: str | None
    sources: list[dict] = Field(max_length=100)
    source_count: int
    sources_truncated: bool
    unresolved_sources: list[dict] = Field(max_length=100)
    unresolved_source_count: int
    unresolved_sources_truncated: bool
    core_api_fields: list[str]


class CurrentFactsResponse(Model):
    format: Literal["current-official-facts-v1"]
    code: int
    policy_hash: str
    identity_hash: str
    facts_revision: str
    mapping_state: Literal["matched", "identity_changed", "not_captured"]
    projection_active: bool
    fields: dict[str, CurrentFact]
    originals_replaced: Literal[False]


class CurrentPolicyResponse(Model):
    policy_hash: str
    document: dict
