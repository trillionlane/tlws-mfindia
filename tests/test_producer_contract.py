"""Wholly synthetic MFData native contract/lifecycle checks. No source or store I/O."""
from copy import deepcopy
import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from mfdataindia.producer_contract import policy
from mfdataindia.producer_contract.execution_reference import (
    ContractError, canonical_bytes, load_json, serialize, serialize_record,
    synthetic_execution_page, synthetic_unavailable, validate_refresh_transition,
)
from mfdataindia.producer_contract.schemas import ExecutionReferenceRecord, SnapshotManifest

FIXTURES = Path(__file__).parent / "fixtures" / "producer_contract"
spec = importlib.util.spec_from_file_location("producer_fixture_builder", FIXTURES / "build_fixtures.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
BASE = builder.record()


def rehash(manifest):
    for section in manifest["sections"]:
        section["sectionId"] = policy.section_id(section)
    manifest["snapshotId"] = policy.root_id(manifest["sections"])
    return manifest


@pytest.mark.parametrize("container", [None, "variant", "mapping", "providerReference"])
def test_every_required_member_and_unknown_member_is_rejected(container):
    original = BASE if container is None else BASE[container]
    for key in original:
        item = deepcopy(BASE)
        target = item if container is None else item[container]
        del target[key]
        with pytest.raises(ContractError, match="^RECORD_INVALID$"):
            serialize_record(item)
    item = deepcopy(BASE)
    target = item if container is None else item[container]
    target["unknown"] = "SYNTHETIC_SECRET_MARKER"
    with pytest.raises(ContractError) as error:
        serialize_record(item)
    assert str(error.value) == "RECORD_INVALID"


class Coercible:
    def __str__(self):
        return "MAPPED"

    def __eq__(self, other):
        return True


class StringSubclass(str):
    pass


@pytest.mark.parametrize("bad", [Coercible(), StringSubclass("MAPPED"), ["MAPPED"], {"value": "MAPPED"}, 1, True])
def test_nonprimitive_discriminators_never_coerce(bad):
    item = deepcopy(BASE)
    item["mapping"]["status"] = bad
    with pytest.raises(ContractError, match="^RECORD_INVALID$"):
        serialize_record(item)


@pytest.mark.parametrize("bad", [True, False, 1.0, "123456", None, [], 0, -1, 2**53])
def test_amfi_code_is_a_primitive_positive_safe_integer(bad):
    item = deepcopy(BASE)
    item["variant"]["amfiSchemeCode"] = bad
    with pytest.raises(ContractError):
        serialize_record(item)


def test_safe_integer_boundary_and_all_mapping_statuses():
    item = builder.record(code=2**53 - 1)
    assert load_json(serialize_record(item))["variant"]["amfiSchemeCode"] == 2**53 - 1
    for status in builder.REASONS:
        item = builder.record(status)
        assert load_json(serialize_record(item))["mapping"]["status"] == status
        if status != "MAPPED":
            item["providerReference"] = deepcopy(BASE["providerReference"])
            with pytest.raises(ContractError):
                serialize_record(item)


@pytest.mark.parametrize("bad", ["2099-01-01T00:00:00Z", 0, False, {}, [], "null"])
def test_expires_at_is_literal_json_null_not_omitted_or_nonnull(bad):
    item = {**BASE, "expiresAt": bad}
    with pytest.raises(ContractError):
        serialize_record(item)
    item = deepcopy(BASE)
    del item["expiresAt"]
    with pytest.raises(ContractError):
        serialize_record(item)
    assert load_json(serialize_record(BASE))["expiresAt"] is None


def test_constructed_mutated_and_final_serialized_objects_are_revalidated(monkeypatch):
    unsafe = ExecutionReferenceRecord.model_construct(**BASE)
    object.__setattr__(unsafe, "contractVersion", True)
    with pytest.raises(ContractError):
        serialize_record(unsafe)
    valid = ExecutionReferenceRecord.model_validate(builder.record("CONFLICT"))
    valid.mapping.reasonCodes.append("SYNTHETIC_SECRET_MARKER")
    with pytest.raises(ContractError) as error:
        serialize_record(valid)
    assert str(error.value) == "RECORD_INVALID"
    original = ExecutionReferenceRecord.model_dump

    def corrupt(self, *args, **kwargs):
        return {**original(self, *args, **kwargs), "transactionEligibility": True}

    monkeypatch.setattr(ExecutionReferenceRecord, "model_dump", corrupt)
    with pytest.raises(ContractError):
        serialize_record(BASE)


def test_unavailable_fixture_is_synthetic_zero_items_and_no_acceptance_time():
    manifest, page = synthetic_unavailable()
    manifest = load_json(manifest)
    assert [s["section"] for s in manifest["sections"]] == list(policy.SECTIONS)
    for descriptor in manifest["sections"]:
        assert descriptor["availability"] == "UNAVAILABLE" and descriptor["itemCount"] == 0
        assert descriptor["sourceAsOf"] is None and descriptor["expiresAt"] is None
        assert "freshness" not in descriptor
    assert manifest["sections"][1]["executionMetadata"]["acceptedAt"] is None
    assert load_json(page)["items"] == [] and load_json(page)["nextCursor"] is None


@pytest.mark.parametrize("change", ["duplicate-section", "missing-section", "extra-section", "count", "version",
                                    "metadata", "source-time", "unknown-reason", "counts", "bool-count"])
def test_manifest_rejects_inconsistent_metadata_even_after_rehash(change):
    manifest, _ = builder.snapshot([builder.record()])
    descriptor = manifest["sections"][1]
    meta = descriptor["executionMetadata"]
    if change == "duplicate-section":
        manifest["sections"][0] = deepcopy(descriptor)
    elif change == "missing-section":
        manifest["sections"].pop()
    elif change == "extra-section":
        manifest["sections"].append(deepcopy(descriptor))
    elif change == "count":
        descriptor["itemCount"] += 1
    elif change == "version":
        meta["mappingPolicyVersion"] = "unknown-version"
    elif change == "metadata":
        descriptor["executionMetadata"] = None
    elif change == "source-time":
        descriptor["sourceAsOf"] = "2020-01-01T00:00:00Z"
    elif change == "unknown-reason":
        descriptor["reasonCodes"] = ["UNRECOGNIZED_NATIVE_REASON"]
    elif change == "counts":
        meta["universe"]["eligibleVariants"] += 1
    else:
        meta["recordStatusCounts"]["OUT_OF_SCOPE"] = False
    rehash(manifest)
    with pytest.raises(ContractError):
        serialize(manifest, SnapshotManifest)


def test_root_and_section_hashes_reject_tampering_and_refresh_is_not_in_identity():
    manifest, records = builder.snapshot([builder.record()])
    original = canonical_bytes(manifest)
    for key in ("snapshotId",):
        changed = deepcopy(manifest)
        changed[key] = "mfref-v1:" + "0" * 64
        with pytest.raises(ContractError):
            serialize(changed, SnapshotManifest)
    changed = deepcopy(manifest)
    changed["sections"][1]["contentDigest"] = "0" * 64
    with pytest.raises(ContractError):
        serialize(changed, SnapshotManifest)
    prior, _ = builder.refresh(manifest)
    later, receipt = builder.refresh(manifest, at="2027-01-11T10:00:00Z", prior=prior)
    validate_refresh_transition(prior, later, observation=receipt, accepted_manifest=manifest, accepted_records=records,
                                prior_manifest=manifest, prior_records=records)
    assert canonical_bytes(manifest) == original
    assert records[0]["sourceAsOf"] == manifest["sections"][1]["executionMetadata"]["acceptedAt"]


def test_bounded_paging_retains_every_verified_route_as_a_set():
    manifest = load_json((FIXTURES / "synthetic-manifest.json").read_bytes())
    records = load_json((FIXTURES / "synthetic-records.json").read_bytes())
    before = deepcopy(records)
    cursor, seen = None, []
    while True:
        raw = synthetic_execution_page(list(reversed(records)), manifest=manifest, limit=2, cursor=cursor)
        assert raw == synthetic_execution_page(records, manifest=manifest, limit=2, cursor=cursor)
        page = load_json(raw)
        seen += page["items"]
        cursor = page["nextCursor"]
        if cursor is None:
            break
    assert seen == sorted(before, key=policy.record_sort_key)
    grouped = {}
    for item in seen:
        key = (item["variant"]["amfiSchemeCode"], item["variant"]["matchedIsinSlot"])
        grouped.setdefault(key, set()).add(item["executionReferenceId"])
    assert len(grouped[(123456, "GROWTH_OR_DIV_PAYOUT")]) == 2
    assert {r["providerReference"]["schemeCode"] for r in seen if r["mapping"]["status"] == "MAPPED"} == {
        "SYNTHETIC-ROUTE-01", "SYNTHETIC-ROUTE-02"}
    assert len(seen) == 5 and records == before


@pytest.mark.parametrize("change", ["snapshot", "section", "section-id", "extra", "missing", "position", "duplicate", "noncanonical", "oversize", "invalid-base64"])
def test_cursor_is_bound_to_root_section_and_exact_position(change):
    manifest, records = builder.snapshot([builder.record(), builder.record(scheme="SYNTHETIC-ROUTE-02")])
    first = records[0]
    data = {"snapshotId": manifest["snapshotId"], "section": "executionReferences",
            "sectionId": manifest["sections"][1]["sectionId"],
            "afterAmfiSchemeCode": first["variant"]["amfiSchemeCode"],
            "afterMatchedIsinSlot": first["variant"]["matchedIsinSlot"],
            "afterExecutionReferenceId": first["executionReferenceId"]}
    if change == "snapshot":
        data["snapshotId"] = "mfref-v1:" + "0" * 64
    elif change == "section":
        data["section"] = "nav"
    elif change == "section-id":
        data["sectionId"] = "mfsection-v1:" + "0" * 64
    elif change == "extra":
        data["extra"] = True
    elif change == "missing":
        del data["section"]
    elif change == "position":
        data["afterExecutionReferenceId"] = "unknown-position"
    raw = canonical_bytes(data)
    if change == "duplicate":
        raw = b'{"snapshotId":"ignored",' + raw[1:]
    if change == "noncanonical":
        raw = json.dumps(data).encode()
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    if change == "oversize":
        encoded = "A" * 1025
    if change == "invalid-base64":
        encoded = "%%%"
    with pytest.raises(ContractError, match="^CURSOR_INVALID$"):
        synthetic_execution_page(records, manifest=manifest, limit=1, cursor=encoded)


@pytest.mark.parametrize("limit", [0, 101, True, 1.0, "1"])
def test_page_limit_is_not_coerced(limit):
    manifest, records = builder.snapshot([builder.record()])
    with pytest.raises(ContractError):
        synthetic_execution_page(records, manifest=manifest, limit=limit)


def test_duplicate_routes_mixed_assessments_conflicting_variant_and_scope_are_rejected():
    for records, code in [
        ([builder.record(), builder.record()], "DUPLICATE_REFERENCE"),
        ([builder.record(), builder.record("UNMAPPED")], "RECORD_INVALID"),
    ]:
        with pytest.raises(ContractError, match="^" + code + "$"):
            manifest, records = builder.snapshot(records)
            synthetic_execution_page(records, manifest=manifest, limit=2)
    a, b = builder.record(), builder.record(scheme="SYNTHETIC-ROUTE-02")
    b["variant"]["isin"] = "INZZ00000027"
    b["executionReferenceId"] = policy.expected_reference_id(b)
    manifest, records = builder.snapshot([a, b])
    with pytest.raises(ContractError, match="^CANONICAL_VARIANT_CONFLICT$"):
        synthetic_execution_page(records, manifest=manifest, limit=2)
    with pytest.raises(ContractError):
        manifest, records = builder.snapshot([builder.record("OUT_OF_SCOPE")])
        synthetic_execution_page(records, manifest=manifest, limit=1)


def test_unmapped_to_mapped_and_retirement_preserve_history():
    before, old_items = builder.snapshot([builder.record("UNMAPPED")])
    old_bytes = canonical_bytes({"manifest": before, "records": old_items})
    mapped, routes = builder.snapshot([builder.record(), builder.record(scheme="SYNTHETIC-ROUTE-02")],
                                     accepted_at="2026-01-11T10:00:00Z", source_revision=2)
    after, remaining = builder.snapshot([builder.record()], accepted_at="2026-01-12T10:00:00Z", source_revision=3)
    assert old_items[0]["executionReferenceId"] not in {r["executionReferenceId"] for r in routes}
    assert len({r["executionReferenceId"] for r in routes} - {r["executionReferenceId"] for r in remaining}) == 1
    assert remaining[0]["executionReferenceId"] in {r["executionReferenceId"] for r in routes}
    assert len({before["snapshotId"], mapped["snapshotId"], after["snapshotId"]}) == 3
    for manifest, items in ((before, old_items), (mapped, routes), (after, remaining)):
        synthetic_execution_page(items, manifest=manifest, limit=2)
    assert canonical_bytes({"manifest": before, "records": old_items}) == old_bytes


def test_incomplete_source_cannot_unmap_replace_or_retire_a_complete_head():
    active, records = builder.snapshot([builder.record(), builder.record(scheme="SYNTHETIC-ROUTE-02")])
    held, empty = builder.snapshot([], completeness="INCOMPLETE", eligible_variants=1, source_revision=2)
    assert not empty and held["sections"][1]["executionMetadata"]["acceptedAt"] is None
    prior, _ = builder.refresh(active)
    proposed, receipt = builder.refresh(held, status="UPDATED", at="2026-01-12T10:00:00Z", prior=prior,
                                        candidate_digest="0" * 64)
    with pytest.raises(ContractError, match="^NEW_COMPLETE_MAPPING_REQUIRED$"):
        validate_refresh_transition(prior, proposed, observation=receipt, accepted_manifest=held, accepted_records=empty,
                                    prior_manifest=active, prior_records=records)
    bad = deepcopy(held)
    bad["sections"][1]["executionMetadata"]["recordStatusCounts"]["UNMAPPED"] = 1
    rehash(bad)
    with pytest.raises(ContractError):
        serialize(bad, SnapshotManifest)
    assert len(records) == 2 and prior["activeSnapshotId"] == active["snapshotId"]


@pytest.mark.parametrize("status", ["FAILED", "INCOMPLETE", "CONFLICT"])
def test_bad_refresh_keeps_head_and_last_success_or_verification(status):
    manifest, _ = builder.snapshot([builder.record()])
    prior, _ = builder.refresh(manifest)
    after, receipt = builder.refresh(manifest, at="2026-01-12T10:00:00Z", status=status, prior=prior)
    result = load_json(validate_refresh_transition(prior, after, observation=receipt))
    assert result["activeSnapshotId"] == prior["activeSnapshotId"]
    assert result["lastVerifiedAt"] == prior["lastVerifiedAt"]
    bad = {**after, "activeSnapshotId": "mfref-v1:" + "0" * 64}
    with pytest.raises(ContractError, match="^REFRESH_RECEIPT_POINTER_MISMATCH$"):
        validate_refresh_transition(prior, bad, observation=receipt)
    bad = {**after, "lastVerifiedAt": after["lastRefreshAttemptAt"]}
    with pytest.raises(ContractError, match="^FAILED_REFRESH_CANNOT_CLAIM_VERIFIED$"):
        validate_refresh_transition(prior, bad, observation=receipt)


def test_old_source_and_failed_refresh_never_expire_mapping_identity():
    manifest, records = builder.snapshot([builder.record()], filename_date="2020-01-01",
                                        accepted_at="2026-01-10T10:00:00Z")
    raw = canonical_bytes(manifest)
    assert records[0]["sourceAsOf"] == "2026-01-10T10:00:00Z" and records[0]["expiresAt"] is None
    assert manifest["sections"][1]["executionMetadata"]["nseFilenameDate"] == "2020-01-01"
    prior, _ = builder.refresh(manifest)
    verified, receipt = builder.refresh(manifest, at="2028-01-12T10:00:00Z", prior=prior)
    validate_refresh_transition(prior, verified, observation=receipt, accepted_manifest=manifest, accepted_records=records,
                                prior_manifest=manifest, prior_records=records)
    failed, failure = builder.refresh(manifest, at="2028-01-13T10:00:00Z", status="FAILED", prior=verified)
    validate_refresh_transition(verified, failed, observation=failure)
    assert canonical_bytes(manifest) == raw
    assert records[0]["sourceAsOf"] == "2026-01-10T10:00:00Z"
    assert records[0]["expiresAt"] is None


def test_unchanged_verification_cannot_roll_acceptance_time():
    manifest, records = builder.snapshot([builder.record()])
    prior, _ = builder.refresh(manifest)
    revised, revised_records = builder.snapshot([builder.record()], accepted_at="2026-01-11T10:00:00Z")
    after, receipt = builder.refresh(revised, at="2026-01-12T10:00:00Z", prior=prior)
    with pytest.raises(ContractError, match="^NEW_COMPLETE_MAPPING_REQUIRED$"):
        validate_refresh_transition(prior, after, observation=receipt, accepted_manifest=revised, accepted_records=revised_records,
                                    prior_manifest=manifest, prior_records=records)


def test_new_complete_snapshot_can_supersede_once_without_rewriting_old_history():
    old, old_records = builder.snapshot([builder.record("UNMAPPED")])
    old_bytes = canonical_bytes({"manifest": old, "items": old_records})
    before, _ = builder.refresh(old)
    newer, items = builder.snapshot([builder.record(), builder.record(scheme="SYNTHETIC-ROUTE-02")],
                                   accepted_at="2026-01-12T10:00:00Z", source_revision=2)
    after, receipt = builder.refresh(newer, status="UPDATED", at="2026-01-12T11:00:00Z", prior=before)
    assert load_json(validate_refresh_transition(before, after, observation=receipt, accepted_manifest=newer, accepted_records=items,
                                prior_manifest=old, prior_records=old_records))["activeSnapshotId"] == newer["snapshotId"]
    assert validate_refresh_transition(after, after, observation=receipt, accepted_manifest=newer, accepted_records=items,
                                prior_manifest=old, prior_records=old_records) == canonical_bytes(after)
    assert len(items) == 2
    assert canonical_bytes({"manifest": old, "items": old_records}) == old_bytes
    same, repeated = builder.refresh(newer, status="UPDATED", at="2026-01-13T11:00:00Z", prior=after)
    with pytest.raises(ContractError):
        validate_refresh_transition(after, same, observation=repeated, accepted_manifest=newer, accepted_records=items,
                                prior_manifest=old, prior_records=old_records)


def test_duplicate_json_members_and_nonfinite_numbers_are_rejected():
    wire = canonical_bytes(BASE)
    for raw in (b'{"contractVersion":"SYNTHETIC_SECRET_MARKER",' + wire[1:], b'{"x":NaN}'):
        with pytest.raises(ContractError) as error:
            load_json(raw)
        assert str(error.value) == "JSON_INVALID"


def test_reserved_sections_have_no_untyped_pages_and_imports_need_no_io():
    from mfdataindia.producer_contract import schemas
    assert not hasattr(schemas, "DataSectionPage") and not hasattr(schemas, "PageEnvelope")
    with pytest.raises(ValidationError):
        ExecutionReferenceRecord.model_validate({**BASE, "nav": {"value": 1}})
    script = '''
import builtins,socket
original=builtins.__import__
def guarded(name,*args,**kwargs):
 if name.split('.')[0] in {'jsonschema','requests','psycopg','psycopg_pool','fastapi','uvicorn'}:
  raise AssertionError('forbidden runtime import')
 return original(name,*args,**kwargs)
builtins.__import__=guarded
def forbidden(*args,**kwargs): raise AssertionError('network forbidden')
socket.create_connection=forbidden
socket.socket.connect=forbidden
from mfdataindia.producer_contract.execution_reference import synthetic_unavailable
assert synthetic_unavailable()[1]
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
