"""Pure serialization and synthetic paging helpers; no source materialization."""

from __future__ import annotations

import base64
import json
from typing import TypeVar

from pydantic import ValidationError

from . import policy
from .schemas import (
    SECTIONS, Cursor, ExecutionReferencePage, ExecutionReferenceRecord, RefreshObservation, RefreshStatus,
    SnapshotManifest, WireModel, timestamp_ms,
)

MAX_RECORD_BYTES = 16 * 1024
VERSION = "mfdata-reference-snapshot-v1"
Model = TypeVar("Model", bound=WireModel)


class ContractError(ValueError):
    """Value-free public error. Validation details and input values are never echoed."""


def canonical_bytes(value) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ContractError("JSON_INVALID") from None


def load_json(raw: bytes):
    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise ContractError("DUPLICATE_MEMBER")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ContractError("JSON_INVALID")

    try:
        if type(raw) is not bytes:
            raise ContractError("JSON_INVALID")
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ContractError("JSON_INVALID") from None


def serialize(value, model: type[Model]) -> bytes:
    """Revalidate even constructed/mutated models and then the final serialized object."""
    try:
        data = value.model_dump(mode="json", warnings="error") if type(value) is model else value
        checked = model.model_validate(data, strict=True)
        raw = canonical_bytes(checked.model_dump(mode="json", warnings="error"))
        model.model_validate(load_json(raw), strict=True)
        return raw
    except (ValidationError, TypeError, ValueError, UnicodeError, RecursionError):
        raise ContractError("RECORD_INVALID") from None


def serialize_record(value) -> bytes:
    raw = serialize(value, ExecutionReferenceRecord)
    if len(raw) > MAX_RECORD_BYTES:
        raise ContractError("RECORD_TOO_LARGE")
    return raw


def serialize_execution_page(value, manifest: dict) -> bytes:
    manifest = load_json(serialize(manifest, SnapshotManifest))
    raw = serialize(value, ExecutionReferencePage)
    page = load_json(raw)
    descriptor = manifest["sections"][1]
    if (page["snapshotId"] != manifest["snapshotId"]
            or page["sectionId"] != descriptor["sectionId"]
            or page["sectionContentDigest"] != descriptor["contentDigest"]
            or page["count"] > descriptor["itemCount"]):
        raise ContractError("SNAPSHOT_MISMATCH")
    for item in page["items"]:
        serialize_record(item)
        if (item["sourceAsOf"] != descriptor["sourceAsOf"]
                or item["expiresAt"] != descriptor["expiresAt"]):
            raise ContractError("ACCEPTANCE_TIME_MISMATCH")
        if (item["mapping"]["status"] == "UNMAPPED"
                and descriptor["executionMetadata"]["completeness"] != "COMPLETE"):
            raise ContractError("SOURCE_SNAPSHOT_INCOMPLETE")
    return raw


def synthetic_unavailable() -> tuple[bytes, bytes]:
    """A clearly synthetic absent-source fixture, never a runtime state inspection."""
    metadata = {
        "canonicalUniverse": {"snapshotId": "synthetic-canonical-1", "digest": "a" * 64},
        "nseSource": None, "codeRegistryVersion": policy.REGISTRY_VERSION,
        "mappingPolicyVersion": policy.POLICY_VERSION,
        "universe": {"inputVariants": 0, "eligibleVariants": 0, "evaluatedVariants": 0,
                     "excludedVariants": 0, "withheldEligibleVariants": 0},
        "recordStatusCounts": {status: 0 for status in ("MAPPED", "AMBIGUOUS", "UNMAPPED", "CONFLICT", "OUT_OF_SCOPE")},
        "variantOutcomeCounts": {status: 0 for status in ("MAPPED", "AMBIGUOUS", "UNMAPPED", "CONFLICT")},
        "exclusions": {}, "withheldReasons": {}, "acceptedAt": None,
        "nseFilenameDate": None, "nseFilenameDateBasis": None, "completeness": "UNAVAILABLE",
    }
    sections = [{
        "section": section, "availability": "UNAVAILABLE", "itemCount": 0,
        "sourceAsOf": None, "expiresAt": None,
        "schemaVersion": "mfdata-reference-" + section + "-v1", "contentDigest": policy.digest([]),
        "reasonCodes": ["SOURCE_SNAPSHOT_INCOMPLETE"] if section == "executionReferences" else [],
        "executionMetadata": metadata if section == "executionReferences" else None,
    } for section in SECTIONS]
    for section in sections:
        section["sectionId"] = policy.section_id(section)
    snapshot_id = policy.root_id(sections)
    manifest = serialize({"contractVersion": VERSION, "snapshotId": snapshot_id,
                          "sections": sections}, SnapshotManifest)
    page = serialize_execution_page({"contractVersion": VERSION, "snapshotId": snapshot_id,
                                    "section": "executionReferences", "sectionId": sections[1]["sectionId"],
                                    "sectionContentDigest": sections[1]["contentDigest"], "count": 0,
                                    "items": [], "nextCursor": None}, load_json(manifest))
    return manifest, page


def _key(item):
    return policy.record_sort_key(item)


def synthetic_execution_page(records: list[dict], *, manifest: dict, limit: int,
                             cursor: str | None = None) -> bytes:
    """Deterministic fixture paging only; not a publisher or canonical mapping adapter.

    Distinct route IDs for the same canonical variant are retained. Cursor is an
    untrusted position, never authorization; a future serving layer must own access.
    """
    if type(records) is not list or type(limit) is not int or not 1 <= limit <= 100:
        raise ContractError("PAGE_INVALID")
    manifest = load_json(serialize(manifest, SnapshotManifest))
    snapshot_id = manifest["snapshotId"]
    descriptor = manifest["sections"][1]
    validated = [load_json(serialize_record(item)) for item in records]
    if any(item["snapshotId"] != snapshot_id for item in validated):
        raise ContractError("SNAPSHOT_MISMATCH")
    if len({item["executionReferenceId"] for item in validated}) != len(validated):
        raise ContractError("DUPLICATE_REFERENCE")
    if policy.section_content_digest(validated) != descriptor["contentDigest"]:
        raise ContractError("SECTION_CONTENT_MISMATCH")
    metadata = descriptor["executionMetadata"]
    observed = {status: sum(item["mapping"]["status"] == status for item in validated)
                for status in metadata["recordStatusCounts"]}
    if observed != metadata["recordStatusCounts"] or len(validated) != descriptor["itemCount"]:
        raise ContractError("STATUS_COUNTS_MISMATCH")
    by_variant, variant_values = {}, {}
    for item in validated:
        key = (item["variant"]["amfiSchemeCode"], item["variant"]["matchedIsinSlot"])
        by_variant.setdefault(key, set()).add(item["mapping"]["status"])
        variant_values.setdefault(key, set()).add((item["variant"]["isin"], item["variant"]["planCode"],
                                                   item["variant"]["optionCode"]))
    if any(len(statuses) > 1 for statuses in by_variant.values()):
        raise ContractError("MIXED_VARIANT_ASSESSMENT")
    if any(len(values) > 1 for values in variant_values.values()):
        raise ContractError("CANONICAL_VARIANT_CONFLICT")
    actual_outcomes = {status: sum(status in statuses for statuses in by_variant.values())
                       for status in metadata["variantOutcomeCounts"]}
    if actual_outcomes != metadata["variantOutcomeCounts"]:
        raise ContractError("VARIANT_OUTCOME_COUNTS_MISMATCH")
    if len(by_variant) != metadata["universe"]["evaluatedVariants"]:
        raise ContractError("EVALUATED_VARIANTS_MISMATCH")
    ordered = sorted(validated, key=_key)
    if cursor is not None:
        try:
            if type(cursor) is not str or not 1 <= len(cursor) <= 1024:
                raise ValueError()
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            decoded = load_json(raw)
            encoded = base64.urlsafe_b64encode(serialize(decoded, Cursor)).rstrip(b"=").decode("ascii")
            if (encoded != cursor or decoded["snapshotId"] != snapshot_id
                    or decoded["sectionId"] != descriptor["sectionId"]):
                raise ValueError()
            after = (decoded["afterAmfiSchemeCode"], decoded["afterMatchedIsinSlot"],
                     decoded["afterExecutionReferenceId"])
            if after not in {_key(item) for item in ordered}:
                raise ValueError()
            ordered = [item for item in ordered if _key(item) > after]
        except (ValueError, UnicodeError, KeyError):
            raise ContractError("CURSOR_INVALID") from None
    selected = ordered[:limit]
    next_cursor = None
    if len(ordered) > limit:
        code, slot, reference = _key(selected[-1])
        raw = serialize({"snapshotId": snapshot_id, "section": "executionReferences",
                         "sectionId": descriptor["sectionId"],
                         "afterAmfiSchemeCode": code, "afterMatchedIsinSlot": slot,
                         "afterExecutionReferenceId": reference}, Cursor)
        next_cursor = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return serialize_execution_page({"contractVersion": VERSION, "snapshotId": snapshot_id,
                                     "sectionId": descriptor["sectionId"],
                                     "sectionContentDigest": descriptor["contentDigest"],
                                     "section": "executionReferences", "count": len(selected),
                                     "items": selected, "nextCursor": next_cursor}, manifest)


def serialize_refresh_observation(value: dict) -> bytes:
    return serialize(value, RefreshObservation)


def validate_refresh_transition(before: dict, after: dict, *, observation: dict,
                                accepted_manifest: dict | None = None) -> bytes:
    """Check the exact immutable receipt and mutable pointer. Never writes either."""
    before = load_json(serialize(before, RefreshStatus))
    encoded = serialize(after, RefreshStatus)
    after = load_json(encoded)
    receipt = load_json(serialize_refresh_observation(observation))
    if (after["latestObservationId"] != receipt["observationId"]
            or after["lastRefreshAttemptAt"] != receipt["attemptAt"]
            or after["refreshStatus"] != receipt["disposition"]
            or after["activeSnapshotId"] != receipt["retainedOrAcceptedHeadSnapshotId"]):
        raise ContractError("REFRESH_RECEIPT_POINTER_MISMATCH")
    replay = after == before
    if after["refreshStatus"] == "NOT_ATTEMPTED":
        raise ContractError("REFRESH_HISTORY_CANNOT_RESET")
    if not replay and before["lastRefreshAttemptAt"] is not None and timestamp_ms(after["lastRefreshAttemptAt"]) <= timestamp_ms(before["lastRefreshAttemptAt"]):
        raise ContractError("REFRESH_TIME_NOT_ADVANCING")
    if not replay and receipt["previousHeadSnapshotId"] != before["activeSnapshotId"]:
        raise ContractError("OBSERVATION_PREDECESSOR_MISMATCH")
    if after["refreshStatus"] != "UPDATED":
        if after["activeSnapshotId"] != before["activeSnapshotId"]:
            raise ContractError("REFRESH_CANNOT_CHANGE_MAPPING_HEAD")
        if after["refreshStatus"] in {"FAILED", "INCOMPLETE", "CONFLICT"}:
            if any(after[key] != before[key] for key in ("lastSuccessfulRefreshAt", "lastVerifiedAt")):
                raise ContractError("FAILED_REFRESH_CANNOT_CLAIM_VERIFIED")
    elif not replay and after["activeSnapshotId"] == before["activeSnapshotId"]:
        raise ContractError("NEW_COMPLETE_MAPPING_REQUIRED")
    if after["refreshStatus"] in {"UPDATED", "VERIFIED_UNCHANGED"}:
        if accepted_manifest is None:
            raise ContractError("NEW_COMPLETE_MAPPING_REQUIRED")
        manifest = load_json(serialize(accepted_manifest, SnapshotManifest))
        descriptor = manifest["sections"][1]
        if (manifest["snapshotId"] != after["activeSnapshotId"]
                or descriptor["executionMetadata"]["completeness"] != "COMPLETE"
                or descriptor["availability"] != "AVAILABLE"):
            raise ContractError("NEW_COMPLETE_MAPPING_REQUIRED")
        if timestamp_ms(descriptor["sourceAsOf"]) > timestamp_ms(after["lastRefreshAttemptAt"]):
            raise ContractError("ACCEPTANCE_TIME_AFTER_REFRESH")
        meta = descriptor["executionMetadata"]
        if (receipt["candidateMappingContentDigest"] != descriptor["contentDigest"]
                or receipt["canonicalUniverse"] != meta["canonicalUniverse"]
                or receipt["codeRegistryVersion"] != meta["codeRegistryVersion"]
                or receipt["mappingPolicyVersion"] != meta["mappingPolicyVersion"]):
            raise ContractError("REFRESH_MAPPING_DECISION_PINS_MISMATCH")
        if after["refreshStatus"] == "UPDATED":
            if (receipt["sourceSnapshotId"] != meta["nseSource"]["snapshotId"]
                    or receipt["sourceDigest"] != meta["nseSource"]["digest"]
                    or receipt["filenameGenerationDate"] != meta["nseFilenameDate"]
                    or receipt["filenameDateBasis"] != meta["nseFilenameDateBasis"]):
                raise ContractError("REFRESH_SOURCE_PIN_MISMATCH")
    return encoded
