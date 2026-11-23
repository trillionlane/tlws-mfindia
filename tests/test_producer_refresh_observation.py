"""Four bounded architecture revisions, tested against synthetic full sections."""
from copy import deepcopy
import importlib.util
from pathlib import Path
from typing import get_args

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from mfdataindia.producer_contract import compatibility as c, policy
from mfdataindia.producer_contract.execution_reference import (
    ContractError, canonical_bytes, load_json, serialize, serialize_refresh_observation,
    synthetic_execution_page, validate_refresh_transition,
)
from mfdataindia.producer_contract.schemas import RefreshObservation, SnapshotManifest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("producer_refresh_fixtures", ROOT / c.FIXTURES / "build_fixtures.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def rehash(manifest):
    for descriptor in manifest["sections"]:
        descriptor["sectionId"] = policy.section_id(descriptor)
    manifest["snapshotId"] = policy.root_id(manifest["sections"])


def resign(summary, receipt):
    receipt["observationId"] = policy.observation_id(receipt)
    summary["latestObservationId"] = receipt["observationId"]


def test_observation_and_status_have_standalone_versioned_schemas_pins_and_fixtures():
    artifacts = c.contract_artifacts()
    paths = ((c.OBSERVATION_SCHEMA_PATH, "mfdata-reference-refresh-observation-v1", "synthetic-refresh-observation.json"),
             (c.STATUS_SCHEMA_PATH, "mfdata-reference-refresh-status-v1", "synthetic-refresh-status.json"))
    for path, version, fixture in paths:
        schema = load_json(artifacts[path])
        assert schema["properties"]["contractVersion"]["const"] == version
        assert schema["additionalProperties"] is False
        raw = (ROOT / c.FIXTURES / fixture).read_bytes()
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(load_json(raw))
        assert path in c.evidence(ROOT)["artifacts"]
    assert "latestObservation" not in load_json(artifacts[c.STATUS_SCHEMA_PATH])["properties"]
    for line in (ROOT / c.REFRESH_PINS_PATH).read_text().splitlines():
        digest, name = line.split("  ")
        assert c.sha256((ROOT / name).read_bytes()) == digest


def test_receipts_are_content_addressed_immutable_and_preserve_prior_attempts():
    manifest, _ = builder.snapshot([builder.record()])
    first, first_receipt = builder.refresh(manifest)
    original = serialize_refresh_observation(first_receipt)
    latest, receipt = builder.refresh(manifest, at="2026-01-12T10:00:00Z", prior=first)
    assert receipt["observationId"] != first_receipt["observationId"]
    assert receipt["observationId"] == policy.observation_id(deepcopy(receipt))
    assert latest["latestObservationId"] == receipt["observationId"]
    assert serialize_refresh_observation(first_receipt) == original
    model = RefreshObservation.model_validate(first_receipt)
    with pytest.raises(ValidationError):
        model.attemptAt = "2027-01-01T00:00:00Z"
    with pytest.raises(ValidationError):
        model.canonicalUniverse.digest = "0" * 64
    altered = {**receipt, "sourceDigest": "0" * 64}
    with pytest.raises(ContractError, match="^RECORD_INVALID$"):
        serialize_refresh_observation(altered)
    bad_pointer = {**latest, "latestObservationId": first_receipt["observationId"]}
    with pytest.raises(ContractError, match="^REFRESH_RECEIPT_POINTER_MISMATCH$"):
        validate_refresh_transition(first, bad_pointer, observation=receipt, accepted_manifest=manifest)


@pytest.mark.parametrize("disposition", ["VERIFIED_UNCHANGED", "UPDATED", "FAILED", "INCOMPLETE", "CONFLICT"])
def test_each_terminal_disposition_has_an_independent_receipt(disposition):
    old, _ = builder.snapshot([builder.record()])
    before, _ = builder.refresh(old)
    accepted = old
    if disposition == "UPDATED":
        accepted, _ = builder.snapshot([builder.record(), builder.record(scheme="SYNTHETIC-ROUTE-02")],
                                       source_revision=2, accepted_at="2026-01-12T09:00:00Z")
    after, receipt = builder.refresh(accepted, at="2026-01-12T10:00:00Z", status=disposition, prior=before)
    encoded = serialize_refresh_observation(receipt)
    schema = load_json(c.contract_artifacts()[c.OBSERVATION_SCHEMA_PATH])
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(load_json(encoded))
    validate_refresh_transition(before, after, observation=receipt,
                                accepted_manifest=accepted if disposition in {"VERIFIED_UNCHANGED", "UPDATED"} else None)
    if disposition == "FAILED":
        assert receipt["sourceSnapshotId"] is None and receipt["sourceDigest"] is None
        assert receipt["candidateMappingContentDigest"] is None


def test_new_raw_source_can_be_verified_unchanged_without_rewriting_mapping_provenance():
    manifest, records = builder.snapshot([builder.record(), builder.record(scheme="SYNTHETIC-ROUTE-02")])
    original = canonical_bytes({"manifest": manifest, "records": records})
    before, first = builder.refresh(manifest)
    source = {"snapshotId": "synthetic-new-raw-source-2", "digest": "b" * 64}
    after, receipt = builder.refresh(manifest, at="2026-01-12T10:00:00Z", prior=before,
                                     source_pin=source, filename_date="2026-01-12")
    assert policy.section_content_digest(records) == receipt["candidateMappingContentDigest"]
    result = load_json(validate_refresh_transition(before, after, observation=receipt, accepted_manifest=manifest))
    assert result["refreshStatus"] == "VERIFIED_UNCHANGED"
    assert result["activeSnapshotId"] == before["activeSnapshotId"]
    assert receipt["sourceDigest"] != first["sourceDigest"]
    assert receipt["sourceSnapshotId"] != first["sourceSnapshotId"]
    assert receipt["filenameGenerationDate"] != first["filenameGenerationDate"]
    assert after["latestObservationId"] != before["latestObservationId"]
    assert after["lastVerifiedAt"] == "2026-01-12T10:00:00Z"
    assert canonical_bytes({"manifest": manifest, "records": records}) == original


@pytest.mark.parametrize("tamper", ["mapping-content", "canonical-id", "canonical-digest", "registry", "policy"])
def test_verified_unchanged_requires_all_four_mapping_decision_pins(tamper):
    manifest, _ = builder.snapshot([builder.record()])
    before, _ = builder.refresh(manifest)
    after, receipt = builder.refresh(manifest, at="2026-01-12T10:00:00Z", prior=before,
                                     source_pin={"snapshotId": "new-raw", "digest": "b" * 64}, filename_date="2026-01-12")
    if tamper == "mapping-content":
        receipt["candidateMappingContentDigest"] = "0" * 64
    elif tamper == "canonical-id":
        receipt["canonicalUniverse"]["snapshotId"] = "different-canonical-universe"
    elif tamper == "canonical-digest":
        receipt["canonicalUniverse"]["digest"] = "0" * 64
    elif tamper == "registry":
        receipt["codeRegistryVersion"] = "unsupported-registry"
    else:
        receipt["mappingPolicyVersion"] = "unsupported-policy"
    resign(after, receipt)
    with pytest.raises(ContractError):
        validate_refresh_transition(before, after, observation=receipt, accepted_manifest=manifest)


@pytest.mark.parametrize("field,value", [("sourceSnapshotId", "different-source-id"), ("sourceDigest", "0" * 64),
                                         ("filenameGenerationDate", "2026-01-01")])
def test_updated_receipt_must_match_exact_new_manifest_source_pin_and_date(field, value):
    old, _ = builder.snapshot([builder.record()])
    before, _ = builder.refresh(old)
    newer, _ = builder.snapshot([builder.record(), builder.record(scheme="SYNTHETIC-ROUTE-02")],
                                source_revision=2, accepted_at="2026-01-12T09:00:00Z")
    after, receipt = builder.refresh(newer, at="2026-01-12T10:00:00Z", status="UPDATED", prior=before)
    receipt[field] = value
    resign(after, receipt)
    with pytest.raises(ContractError, match="^REFRESH_SOURCE_PIN_MISMATCH$"):
        validate_refresh_transition(before, after, observation=receipt, accepted_manifest=newer)


def test_variant_outcomes_and_record_counts_use_distinct_units():
    manifest = load_json((ROOT / c.FIXTURES / "synthetic-manifest.json").read_bytes())
    records = load_json((ROOT / c.FIXTURES / "synthetic-records.json").read_bytes())
    meta = manifest["sections"][1]["executionMetadata"]
    assert meta["variantOutcomeCounts"] == {"MAPPED": 1, "AMBIGUOUS": 1, "UNMAPPED": 1, "CONFLICT": 1}
    assert meta["recordStatusCounts"] == {"MAPPED": 2, "AMBIGUOUS": 1, "UNMAPPED": 1, "CONFLICT": 1, "OUT_OF_SCOPE": 0}
    assert sum(meta["variantOutcomeCounts"].values()) == meta["universe"]["evaluatedVariants"] == 4
    assert len(records) == sum(meta["recordStatusCounts"].values()) == 5
    synthetic_execution_page(records, manifest=manifest, limit=1)


def test_full_section_validation_detects_wrong_mapped_variant_count_before_first_page():
    manifest, records = builder.snapshot([builder.record(), builder.record(scheme="SYNTHETIC-ROUTE-02")])
    meta = manifest["sections"][1]["executionMetadata"]
    meta["variantOutcomeCounts"]["MAPPED"] = 2
    meta["universe"].update(inputVariants=2, eligibleVariants=2, evaluatedVariants=2)
    rehash(manifest)
    serialize(manifest, SnapshotManifest)  # Relationships are plausible, but actual full section disagrees.
    for item in records:
        item["snapshotId"] = manifest["snapshotId"]
    with pytest.raises(ContractError, match="^VARIANT_OUTCOME_COUNTS_MISMATCH$"):
        synthetic_execution_page(records, manifest=manifest, limit=1)


@pytest.mark.parametrize("change", ["sum", "mapped-zero", "ambiguous", "unmapped", "conflict", "bool"])
def test_variant_outcome_relationships_reject_invalid_metadata(change):
    manifest = load_json((ROOT / c.FIXTURES / "synthetic-manifest.json").read_bytes())
    meta = manifest["sections"][1]["executionMetadata"]
    if change == "sum":
        meta["variantOutcomeCounts"]["MAPPED"] += 1
    elif change == "mapped-zero":
        meta["variantOutcomeCounts"].update(MAPPED=0, AMBIGUOUS=2)
    elif change == "bool":
        meta["variantOutcomeCounts"]["MAPPED"] = True
    else:
        meta["recordStatusCounts"][change.upper()] += 1
        manifest["sections"][1]["itemCount"] += 1
    rehash(manifest)
    with pytest.raises(ContractError):
        serialize(manifest, SnapshotManifest)


def test_normative_reason_vocabulary_is_exact_and_shared_by_generated_contracts():
    expected = {"NO_EXACT_NSE_REFERENCE", "MULTIPLE_PLAUSIBLE_NSE_REFERENCES", "AMC_IDENTITY_CONFLICT",
                "ISIN_CONFLICT", "PLAN_CONFLICT", "OPTION_CONFLICT", "SOURCE_DUPLICATE_CONFLICT",
                "CANONICAL_PLAN_UNRESOLVED", "CANONICAL_OPTION_UNRESOLVED", "CANONICAL_ISIN_UNRESOLVED",
                "PLAN_OUT_OF_SCOPE", "PRODUCT_TYPE_OUT_OF_SCOPE", "SOURCE_SNAPSHOT_INCOMPLETE"}
    assert set(get_args(policy.ClosedReason)) == expected
    record_schema = load_json(c.contract_artifacts()[c.RECORD_SCHEMA_PATH])
    assert set(record_schema["$defs"]["Mapping"]["properties"]["reasonCodes"]["items"]["enum"]) == expected
    for path, raw in c.contract_artifacts().items():
        assert b"EXPIRED" not in raw, path
    for path in (ROOT / c.FIXTURES).glob("*.json*"):
        assert b"EXPIRED" not in path.read_bytes(), path.name
