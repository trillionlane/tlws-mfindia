"""Closed Slice 1 mapping policy. Pure contract rules, not a source adapter."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

POLICY_VERSION = "MFDATA-PRODUCER-MAPPING-POLICY-v1"
REGISTRY_VERSION = "MFDATA-CODE-REGISTRY-v1"
EXECUTION_VERSION = "mfdata-execution-reference-v1"
MAPPING_SEMANTICS_VERSION = "mfdata-mapping-semantics-v1"
SECTIONS = ("variants", "executionReferences", "nav", "facts", "family")
ClosedReason = Literal[
    "NO_EXACT_NSE_REFERENCE", "MULTIPLE_PLAUSIBLE_NSE_REFERENCES",
    "AMC_IDENTITY_CONFLICT", "ISIN_CONFLICT", "PLAN_CONFLICT", "OPTION_CONFLICT",
    "SOURCE_DUPLICATE_CONFLICT", "CANONICAL_PLAN_UNRESOLVED",
    "CANONICAL_OPTION_UNRESOLVED", "CANONICAL_ISIN_UNRESOLVED",
    "PLAN_OUT_OF_SCOPE", "PRODUCT_TYPE_OUT_OF_SCOPE",
    "SOURCE_SNAPSHOT_INCOMPLETE",
]
PopulationExclusion = Literal[
    "INACTIVE", "NON_CANONICAL", "CANONICAL_SCOPE_EXCLUDED", "PLAN_OUT_OF_SCOPE",
    "PRODUCT_TYPE_OUT_OF_SCOPE", "CANONICAL_PLAN_UNRESOLVED", "CANONICAL_OPTION_UNRESOLVED",
    "CANONICAL_ISIN_UNRESOLVED", "OPTION_CONFLICT", "POPULATION_EVIDENCE_UNRESOLVED",
]
DERIVATIONS = {
    ("REGULAR", "GROWTH", "GROWTH_OR_DIV_PAYOUT"): "GROWTH",
    ("REGULAR", "IDCW", "GROWTH_OR_DIV_PAYOUT"): "IDCW_PAYOUT",
    ("REGULAR", "IDCW", "DIV_REINVEST"): "IDCW_REINVEST",
}
CONFLICT_REASONS = frozenset({
    "AMC_IDENTITY_CONFLICT", "ISIN_CONFLICT", "PLAN_CONFLICT", "OPTION_CONFLICT",
    "SOURCE_DUPLICATE_CONFLICT",
})
EXCLUSION_REASONS = frozenset({
    "CANONICAL_PLAN_UNRESOLVED", "CANONICAL_OPTION_UNRESOLVED", "CANONICAL_ISIN_UNRESOLVED",
    "PLAN_OUT_OF_SCOPE", "PRODUCT_TYPE_OUT_OF_SCOPE", "PLAN_CONFLICT", "OPTION_CONFLICT",
})
# Specific reviewed evidence only. Coarse legacy labels (MF_HELD, issuer not
# corroborated, etc.) deliberately have no automatic translation.
EVIDENCE_TO_RECORD = {
    "EXACT_CANONICAL_IDENTITY_AND_VERIFIED_NSE_ROUTE": ("MAPPED", ()),
    "NO_EXACT_NSE_REFERENCE": ("UNMAPPED", ("NO_EXACT_NSE_REFERENCE",)),
    "MULTIPLE_PLAUSIBLE_NSE_REFERENCES": ("AMBIGUOUS", ("MULTIPLE_PLAUSIBLE_NSE_REFERENCES",)),
    **{reason: ("CONFLICT", (reason,)) for reason in CONFLICT_REASONS},
}


class PolicyError(ValueError):
    """Only fixed local policy error codes; never source or provider text."""


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def derive_option(plan: str, option: str, slot: str) -> str:
    if any(type(value) is not str for value in (plan, option, slot)):
        raise PolicyError("CANONICAL_OPTION_UNRESOLVED")
    if plan != "REGULAR":
        raise PolicyError("PLAN_OUT_OF_SCOPE" if plan == "DIRECT" else "CANONICAL_PLAN_UNRESOLVED")
    if slot not in {"GROWTH_OR_DIV_PAYOUT", "DIV_REINVEST"}:
        raise PolicyError("CANONICAL_ISIN_UNRESOLVED")
    try:
        return DERIVATIONS[(plan, option, slot)]
    except KeyError:
        raise PolicyError("OPTION_CONFLICT" if option == "GROWTH" else "CANONICAL_OPTION_UNRESOLVED") from None


def population_exclusion(*, active: bool | None, canonical: bool | None, in_scope: bool | None, product_type: str,
                         plan: str, option: str, slot: str, isin: str) -> str | None:
    """Classify explicit synthetic/canonical facts, never titles or provider flags.

    First-match precedence makes manifest buckets mutually exclusive. These
    population buckets are not record-level mapping reasons.
    """
    if any(type(flag) is not bool and flag is not None for flag in (active, canonical, in_scope)):
        raise PolicyError("POPULATION_EVIDENCE_INVALID")
    if (any(flag is None for flag in (active, canonical, in_scope))
            or type(product_type) is not str
            or product_type not in {"CONVENTIONAL_MF", "SIF", "OTHER_PRODUCT"}):
        return "POPULATION_EVIDENCE_UNRESOLVED"
    if product_type != "CONVENTIONAL_MF":
        return "PRODUCT_TYPE_OUT_OF_SCOPE"
    if not canonical:
        return "NON_CANONICAL"
    if not active:
        return "INACTIVE"
    if not in_scope:
        return "CANONICAL_SCOPE_EXCLUDED"
    try:
        derive_option(plan, option, slot)
    except PolicyError as error:
        return str(error)
    if type(isin) is not str or re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin) is None:
        return "CANONICAL_ISIN_UNRESOLVED"
    return None


def record_decision(evidence: str, *, complete_accepted_universe: bool) -> tuple[str, list[str]]:
    if type(complete_accepted_universe) is not bool or type(evidence) is not str:
        raise PolicyError("POLICY_EVIDENCE_UNCLASSIFIED")
    if evidence == "NO_EXACT_NSE_REFERENCE" and not complete_accepted_universe:
        raise PolicyError("SOURCE_SNAPSHOT_INCOMPLETE")
    try:
        status, reasons = EVIDENCE_TO_RECORD[evidence]
        return status, list(reasons)
    except KeyError:
        raise PolicyError("POLICY_EVIDENCE_UNCLASSIFIED") from None


def _variant_key(code, slot):
    if type(code) is not int or not 1 <= code <= 2**53 - 1:
        raise PolicyError("CANONICAL_VARIANT_INVALID")
    if type(slot) is not str or slot not in {"GROWTH_OR_DIV_PAYOUT", "DIV_REINVEST"}:
        raise PolicyError("CANONICAL_VARIANT_INVALID")
    return {"amfiSchemeCode": code, "matchedIsinSlot": slot}


def assessment_id(code: int, slot: str) -> str:
    return "mfer-assessment-v1:" + digest(_variant_key(code, slot))


def mapped_id(variant: dict, scheme_code: str) -> str:
    if type(variant) is not dict or any(type(key) is not str for key in variant) or set(variant) != {
        "amfiSchemeCode", "matchedIsinSlot", "isin", "planCode", "optionCode"
    }:
        raise PolicyError("CANONICAL_VARIANT_INVALID")
    _variant_key(variant["amfiSchemeCode"], variant["matchedIsinSlot"])
    if (type(variant["isin"]) is not str
            or re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", variant["isin"]) is None
            or type(variant["planCode"]) is not str or variant["planCode"] != "REGULAR"
            or type(variant["optionCode"]) is not str
            or variant["optionCode"] not in {"GROWTH", "IDCW_PAYOUT", "IDCW_REINVEST"}
            or variant["matchedIsinSlot"] != (
                "DIV_REINVEST" if variant["optionCode"] == "IDCW_REINVEST" else "GROWTH_OR_DIV_PAYOUT")):
        raise PolicyError("CANONICAL_VARIANT_INVALID")
    if type(scheme_code) is not str:
        raise PolicyError("NSE_ROUTE_INVALID")
    try:
        size = len(scheme_code.encode("utf-16-le")) // 2
    except UnicodeError:
        raise PolicyError("NSE_ROUTE_INVALID") from None
    if not 1 <= size <= 64:
        raise PolicyError("NSE_ROUTE_INVALID")
    return "mfer-route-v1:" + digest({"provider": "NSE", **variant, "schemeCode": scheme_code})


def expected_reference_id(record: dict) -> str:
    variant = record["variant"]
    if record["mapping"]["status"] == "MAPPED":
        return mapped_id(variant, record["providerReference"]["schemeCode"])
    return assessment_id(variant["amfiSchemeCode"], variant["matchedIsinSlot"])


def root_id(sections: list[dict]) -> str:
    return "mfref-v1:" + digest(sections)


def section_id(descriptor: dict) -> str:
    return "mfsection-v1:" + digest({key: value for key, value in descriptor.items() if key != "sectionId"})


def record_sort_key(record: dict):
    variant = record["variant"]
    return variant["amfiSchemeCode"], variant["matchedIsinSlot"], record["executionReferenceId"]


def section_content_digest(records: list[dict]) -> str:
    # Exclude the root pointer to avoid a root/section/item hash cycle. It is
    # still mandatory on each wire item and fenced against the final root ID.
    return digest([{key: value for key, value in record.items() if key != "snapshotId"}
                   for record in sorted(records, key=record_sort_key)])


def mapping_content_digest(records: list[dict], metadata: dict) -> str:
    """Semantic projection of a validated full section, never its envelope labels.

    Callers must validate the complete records/accounting before trusting this
    digest. Explicit allowlists prevent future envelope fields entering identity.
    Wire integrity remains independently bound by section_content_digest.
    """
    return digest({
        "version": MAPPING_SEMANTICS_VERSION,
        "records": [{
            "variant": {key: record["variant"][key] for key in
                        ("amfiSchemeCode", "matchedIsinSlot", "isin", "planCode", "optionCode")},
            "mapping": {key: record["mapping"][key] for key in ("status", "reasonCodes")},
            "providerReference": ({key: record["providerReference"][key] for key in ("provider", "schemeCode")}
                                  if record["providerReference"] is not None else None),
        } for record in sorted(records, key=record_sort_key)],
        "population": {key: metadata[key] for key in
                       ("universe", "recordStatusCounts", "variantOutcomeCounts", "exclusions", "withheldReasons")},
    })


def observation_id(observation: dict) -> str:
    return "mfrefresh-observation-v1:" + digest({key: value for key, value in observation.items()
                                                if key != "observationId"})
