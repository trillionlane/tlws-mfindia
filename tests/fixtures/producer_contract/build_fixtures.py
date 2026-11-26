"""Deterministic, wholly synthetic producer-owned contract fixtures. No source I/O."""
from copy import deepcopy
from pathlib import Path

from mfdataindia.producer_contract import policy
from mfdataindia.producer_contract.compatibility import sha256
from mfdataindia.producer_contract.execution_reference import (
    canonical_bytes, serialize, serialize_record, synthetic_execution_page, synthetic_unavailable,
)
from mfdataindia.producer_contract.schemas import SnapshotManifest

VARIANT = {"amfiSchemeCode": 123456, "matchedIsinSlot": "GROWTH_OR_DIV_PAYOUT",
           "isin": "INZZ00000019", "planCode": "REGULAR", "optionCode": "GROWTH"}
REASONS = {"MAPPED": [], "AMBIGUOUS": ["MULTIPLE_PLAUSIBLE_NSE_REFERENCES"],
           "UNMAPPED": ["NO_EXACT_NSE_REFERENCE"], "CONFLICT": ["ISIN_CONFLICT"],
           "OUT_OF_SCOPE": ["PRODUCT_TYPE_OUT_OF_SCOPE"]}


def record(status="MAPPED", *, code=123456, scheme="SYNTHETIC-ROUTE-01",
           option="GROWTH", slot="GROWTH_OR_DIV_PAYOUT"):
    item = {
        "contractVersion": policy.EXECUTION_VERSION, "executionReferenceId": "replaced-below",
        "snapshotId": "mfref-v1:" + "1" * 64, "sourceAsOf": "2026-01-10T10:00:00Z",
        "expiresAt": None, "variant": {**VARIANT, "amfiSchemeCode": code, "matchedIsinSlot": slot,
                                       "optionCode": policy.derive_option("REGULAR", option, slot)},
        "mapping": {"status": status, "reasonCodes": REASONS[status].copy()},
        "providerReference": {"provider": "NSE", "schemeCode": scheme} if status == "MAPPED" else None,
    }
    item["executionReferenceId"] = policy.expected_reference_id(item)
    return item


def snapshot(records, *, accepted_at="2026-01-10T10:00:00Z", filename_date="2026-01-10",
             source_revision=1, completeness="COMPLETE", eligible_variants=None, exclusions=None):
    """Fixture assembly only. No real mapper, source reader, publisher or head store."""
    records = deepcopy(records)
    exclusions = dict(exclusions or {})
    observed_variants = {(item["variant"]["amfiSchemeCode"], item["variant"]["matchedIsinSlot"])
                         for item in records}
    eligible = len(observed_variants) if eligible_variants is None else eligible_variants
    for item in records:
        item["sourceAsOf"] = accepted_at
        item["expiresAt"] = None
    if completeness != "COMPLETE":
        assert not records, "Incomplete source cannot be accepted as a new complete mapping"
    evaluated = len(observed_variants)
    withheld = eligible - evaluated
    excluded = sum(exclusions.values())
    meta = {
        "canonicalUniverse": {"snapshotId": "synthetic-canonical-1", "digest": policy.digest({
            "synthetic": True, "variants": sorted([list(v) for v in observed_variants]),
            "eligible": eligible, "exclusions": exclusions})},
        "nseSource": {"snapshotId": f"synthetic-nse-{source_revision}",
                      "digest": policy.digest({"syntheticNseRevision": source_revision})},
        "codeRegistryVersion": policy.REGISTRY_VERSION, "mappingPolicyVersion": policy.POLICY_VERSION,
        "universe": {"inputVariants": eligible + excluded, "eligibleVariants": eligible,
                     "evaluatedVariants": evaluated, "excludedVariants": excluded,
                     "withheldEligibleVariants": withheld},
        "recordStatusCounts": {status: sum(item["mapping"]["status"] == status for item in records)
                         for status in REASONS},
        "variantOutcomeCounts": {status: len({(item["variant"]["amfiSchemeCode"], item["variant"]["matchedIsinSlot"])
                                                for item in records if item["mapping"]["status"] == status})
                                 for status in ("MAPPED", "AMBIGUOUS", "UNMAPPED", "CONFLICT")},
        "exclusions": exclusions, "withheldReasons": {"SOURCE_SNAPSHOT_INCOMPLETE": withheld} if withheld else {},
        "acceptedAt": accepted_at if completeness == "COMPLETE" else None,
        "nseFilenameDate": filename_date, "nseFilenameDateBasis": "FILENAME", "completeness": completeness,
    }
    meta["mappingContentDigest"] = policy.mapping_content_digest(records, meta) if completeness == "COMPLETE" else None
    import json
    sections = json.loads(synthetic_unavailable()[0])["sections"]
    descriptor = sections[1]
    descriptor.update({"availability": "AVAILABLE" if completeness == "COMPLETE" else "WITHHELD",
                       "contentDigest": policy.section_content_digest(records), "itemCount": len(records),
                       "sourceAsOf": meta["acceptedAt"], "executionMetadata": meta,
                       "reasonCodes": [] if completeness == "COMPLETE" else ["SOURCE_SNAPSHOT_INCOMPLETE"]})
    descriptor["sectionId"] = policy.section_id(descriptor)
    manifest = {"contractVersion": "mfdata-reference-snapshot-v1", "snapshotId": policy.root_id(sections),
                "sections": sections}
    serialize(manifest, SnapshotManifest)
    for item in records:
        item["snapshotId"] = manifest["snapshotId"]
        serialize_record(item)
    return manifest, sorted(records, key=policy.record_sort_key)


def refresh(manifest, *, at="2026-01-11T10:00:00Z", status="VERIFIED_UNCHANGED", prior=None,
            source_pin=None, filename_date=None, candidate_digest=None, canonical_pin=None):
    descriptor = manifest["sections"][1]
    meta = descriptor["executionMetadata"]
    success = status in {"VERIFIED_UNCHANGED", "UPDATED"}
    previous = prior["activeSnapshotId"] if prior is not None else None if status == "UPDATED" else manifest["snapshotId"]
    resulting = manifest["snapshotId"] if status == "UPDATED" else previous
    source = source_pin if source_pin is not None else meta["nseSource"] if status != "FAILED" else None
    date = filename_date if filename_date is not None else meta["nseFilenameDate"] if source is not None else None
    receipt = {
        "contractVersion": "mfdata-reference-refresh-observation-v1", "attemptId": "synthetic-attempt-" + at + "-" + status,
        "attemptAt": at, "disposition": status,
        "sourceSnapshotId": source["snapshotId"] if source else None,
        "sourceDigest": source["digest"] if source else None,
        "filenameGenerationDate": date, "filenameDateBasis": "FILENAME" if date else None,
        "diagnosticCode": {"VERIFIED_UNCHANGED": "VERIFIED_UNCHANGED", "UPDATED": "NEW_SNAPSHOT_ACCEPTED",
                           "FAILED": "FETCH_FAILED", "INCOMPLETE": "SOURCE_INCOMPLETE", "CONFLICT": "IDENTITY_CONFLICT"}[status],
        "candidateMappingContentDigest": candidate_digest if candidate_digest is not None else meta["mappingContentDigest"] if success else None,
        "canonicalUniverse": canonical_pin if canonical_pin is not None else meta["canonicalUniverse"],
        "codeRegistryVersion": meta["codeRegistryVersion"], "mappingPolicyVersion": meta["mappingPolicyVersion"],
        "previousHeadSnapshotId": previous, "retainedOrAcceptedHeadSnapshotId": resulting,
    }
    receipt["observationId"] = policy.observation_id(receipt)
    summary = {
        "contractVersion": "mfdata-reference-refresh-status-v1", "activeSnapshotId": resulting,
        "lastRefreshAttemptAt": at,
        "lastSuccessfulRefreshAt": at if success else prior["lastSuccessfulRefreshAt"],
        "lastVerifiedAt": at if success else prior["lastVerifiedAt"], "refreshStatus": status,
        "latestObservationId": receipt["observationId"],
    }
    return summary, receipt


def files() -> dict[str, bytes]:
    base = record()
    cases, wires = [], []

    def add(name, value, schema=True, producer=True, error=None):
        cases.append({"id": name, "nativeSchemaValid": schema, "producerValid": producer,
                      "producerError": error if error else None if producer else "RECORD_INVALID"})
        wires.append(canonical_bytes(value))

    for status in REASONS:
        add("valid-" + status.lower(), record(status))
    add("valid-idcw-payout", record(option="IDCW"))
    add("valid-idcw-reinvest", record(option="IDCW", slot="DIV_REINVEST"))

    def changed(name, path, value, schema=False):
        item = deepcopy(base)
        target = item
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
        add(name, item, schema=schema, producer=False)

    for member in ("expiresAt", "providerReference"):
        item = deepcopy(base)
        del item[member]
        add("missing-" + member, item, False, False)
    for suffix, value in (("timestamp", "2099-01-01T00:00:00Z"), ("bool", False),
                          ("number", 0), ("object", {}), ("string-null", "null")):
        changed("expiresAt-" + suffix, ["expiresAt"], value)
    changed("extra-root", ["transactionEligibility"], "SYNTHETIC_SECRET_MARKER")
    changed("extra-variant-family", ["variant", "tlws_mf_id"], "synthetic-family")
    changed("nonprimitive-status", ["mapping", "status"], {"value": "MAPPED"})
    changed("historical-tip-contract-version", ["contractVersion"], "1")
    changed("bool-amfi-code", ["variant", "amfiSchemeCode"], True)
    changed("string-amfi-code", ["variant", "amfiSchemeCode"], "123456")
    changed("fractional-amfi-code", ["variant", "amfiSchemeCode"], 1.5)
    changed("integer-float-amfi-code", ["variant", "amfiSchemeCode"], 123456.0, schema=True)
    changed("zero-amfi-code", ["variant", "amfiSchemeCode"], 0)
    changed("unsafe-amfi-code", ["variant", "amfiSchemeCode"], 2**53)
    changed("unknown-slot", ["variant", "matchedIsinSlot"], "GROWTH")
    changed("invalid-isin", ["variant", "isin"], "not-an-isin")
    changed("direct-plan", ["variant", "planCode"], "DIRECT")
    changed("human-plan-label", ["variant", "planCode"], "Regular Plan")
    changed("human-option-label", ["variant", "optionCode"], "IDCW Reinvestment")
    changed("option-slot-conflict", ["variant", "optionCode"], "IDCW_REINVEST")
    item = record("CONFLICT")
    item["mapping"]["reasonCodes"] = ["ISIN_CONFLICT", "ISIN_CONFLICT"]
    add("duplicate-reasons", item, False, False)
    changed("unknown-reason", ["mapping", "reasonCodes"], ["SYNTHETIC_UNAPPROVED_REASON"])
    changed("unsafe-reason", ["mapping", "reasonCodes"], ["SYNTHETIC_SECRET_MARKER with spaces"])
    changed("too-many-reasons", ["mapping", "reasonCodes"], ["ISIN_CONFLICT"] * 11)
    changed("unavailable-record-status", ["mapping", "status"], "UNAVAILABLE")
    changed("mapped-null-provider", ["providerReference"], None)
    changed("wrong-provider", ["providerReference", "provider"], "OTHER")
    changed("extra-provider", ["providerReference", "secret"], "SYNTHETIC_SECRET_MARKER")
    for status in ("AMBIGUOUS", "UNMAPPED", "CONFLICT", "OUT_OF_SCOPE"):
        item = record(status)
        item["providerReference"] = base["providerReference"]
        add("provider-on-" + status.lower(), item, False, False)
    changed("wrong-derived-id", ["executionReferenceId"], "mfer-route-v1:" + "0" * 64, schema=True)
    changed("empty-snapshot-id", ["snapshotId"], "")
    changed("invalid-calendar", ["sourceAsOf"], "2026-02-30T10:00:00Z")
    changed("missing-timezone", ["sourceAsOf"], "2026-01-10T10:00:00")
    changed("invalid-timezone", ["sourceAsOf"], "2026-01-10T10:00:00+14:01", schema=True)
    item = deepcopy(base)
    item["sourceAsOf"] = "2026-01-10T10:00:00." + "0" * 15000 + "Z"
    padding = 16384 - len(canonical_bytes(item))
    assert padding > 0
    item["sourceAsOf"] = item["sourceAsOf"][:-1] + "0" * padding + "Z"
    assert len(canonical_bytes(item)) == 16384
    add("valid-byte-ceiling", item)
    item["sourceAsOf"] = item["sourceAsOf"][:-1] + "0Z"
    add("over-byte-ceiling", item, True, False, "RECORD_TOO_LARGE")
    item["providerReference"]["schemeCode"] = "😀" * 32
    item["executionReferenceId"] = policy.expected_reference_id(item)
    add("unicode-over-byte-ceiling", item, True, False, "RECORD_TOO_LARGE")
    unavailable, empty_page = synthetic_unavailable()
    manifest, records = snapshot([record(scheme="SYNTHETIC-ROUTE-01"), record(scheme="SYNTHETIC-ROUTE-02"),
                                  record("AMBIGUOUS", code=123457), record("UNMAPPED", code=123458),
                                  record("CONFLICT", code=123459)],
                                 exclusions={"PLAN_OUT_OF_SCOPE": 1, "PRODUCT_TYPE_OUT_OF_SCOPE": 1,
                                             "INACTIVE": 1, "NON_CANONICAL": 1,
                                             "CANONICAL_SCOPE_EXCLUDED": 1, "CANONICAL_PLAN_UNRESOLVED": 1})
    summary, receipt = refresh(manifest, source_pin={"snapshotId": "synthetic-nse-verified-new-raw",
                                                      "digest": policy.digest({"syntheticChangedRaw": 2})},
                               filename_date="2026-01-11")
    result = {
        "cases.json": canonical_bytes(cases) + b"\n", "execution-records.jsonl": b"\n".join(wires) + b"\n",
        "synthetic-unavailable-manifest.json": unavailable + b"\n",
        "synthetic-unavailable-page.json": empty_page + b"\n",
        "synthetic-manifest.json": canonical_bytes(manifest) + b"\n",
        "synthetic-records.json": canonical_bytes(records) + b"\n",
        "synthetic-page.json": synthetic_execution_page(records, manifest=manifest, limit=2) + b"\n",
        "synthetic-refresh-status.json": canonical_bytes(summary) + b"\n",
        "synthetic-refresh-observation.json": canonical_bytes(receipt) + b"\n",
    }
    for case, raw in zip(cases, wires, strict=True):
        if case["producerValid"]:
            import json
            assert serialize_record(json.loads(raw)) == raw
    result["SHA256SUMS"] = "".join(f"{sha256(raw)}  {name}\n" for name, raw in sorted(result.items())).encode()
    return result


if __name__ == "__main__":
    for name, raw in files().items():
        Path(__file__).with_name(name).write_bytes(raw)
