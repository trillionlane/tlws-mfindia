"""Immutable v2 policy for publishing scalar monthly facts into the existing API."""

from copy import deepcopy

from mfdataindia.enrichment import current_facts, monthly_facts
from mfdataindia.review.policy import fingerprint

FIELDS = {**current_facts.FIELDS, "risk_level": ["risk_level"], "aum": ["aum"]}
_POLICY = {
    "id": "current-official-fund-facts",
    "version": 2,
    "previous_policy_hash": fingerprint(current_facts.policy()),
    "monthly_source_policy_hash": fingerprint(monthly_facts.policy()),
    "legacy_inputs": "only the initial legacy snapshot IDs sealed by the owner in an approved projection transition; later provisional v1 inserts are not automatic core publication",
    "publication_boundary": "new monthly inputs affect the core only through a sealed complete-cohort publication receipt; direct provisional snapshot inserts are not current publication",
    "scope": "existing E06 disclosures plus exact-identity monthly ABSL Regular Growth/Annual IDCW cohort",
    "supported_fields": FIELDS,
    "selection": "latest unambiguous usable disclosure date per field; never fetch-time ordering; missing/unusable/conflicting later assertions retain the last usable dated value with explicit metadata",
    "risk": "explicit Current Month scheme risk only; accepted published category, never benchmark risk or gauge legend",
    "aum": "explicit INR_crore and fund scope only; unknown units never overwrite the last usable current value",
    "numeric_display": "trim insignificant decimal zeros for BER/AUM current display only; original tokens retained; no rounding, percent conversion, TER inference or NAV replacement",
    "core_cast": "only source values that round-trip through the existing target column type without changed numeric value are eligible for the core projection",
    "identity": "exact current code/AMC/name/plan/option/ISIN set; no cross-variant borrowing",
    "verification": "publication is independent of human verification; new source dependencies require review revalidation, never blanket human verification",
    "freshness": "per-field disclosure date remains explicit; source refresh receipts are separate from NAV dataset version and immutable snapshot status",
    "recovery": "restore the captured previous configuration and exact prior core view in a short transaction; append recovery history, retain every source/snapshot/configuration",
    "exclusions": [
        "source table DML",
        "canonical analytics replacement",
        "IDCW total-return eligibility",
        "fund-to-plan inception inference",
        "UI",
    ],
}


def policy():
    return deepcopy(_POLICY)
