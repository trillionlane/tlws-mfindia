"""Offline MFData-native schema validation and non-normative TIP impact notes.

Development only. No network resolution or external schema authority.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic.json_schema import models_json_schema

from . import policy
from .execution_reference import ContractError, canonical_bytes, load_json, serialize_record
from .schemas import ExecutionReferencePage, ExecutionReferenceRecord, RefreshObservation, RefreshStatus, SnapshotManifest

BASE = "4b2244fbe45d90bc315b4ba48f2967d6869db002"
SCHEMA_PATH = "contracts/mfdata-reference-snapshot-v1.schema.json"
RECORD_SCHEMA_PATH = "contracts/mfdata-execution-reference-v1.schema.json"
OPENAPI_PATH = "contracts/mfdata-reference-snapshot-v1.openapi.json"
EVIDENCE_PATH = "contracts/mfdata-producer-validation-evidence.json"
ALIGNMENT_PATH = "contracts/future-tip-alignment.json"
OBSERVATION_SCHEMA_PATH = "contracts/mfdata-reference-refresh-observation-v1.schema.json"
STATUS_SCHEMA_PATH = "contracts/mfdata-reference-refresh-status-v1.schema.json"
REFRESH_PINS_PATH = "contracts/mfdata-reference-refresh.SHA256SUMS"
FIXTURES = "tests/fixtures/producer_contract"
LEGACY_TIP_SCHEMA_PATH = "contracts/tip-s2-i0-v1.0/mfdata-execution-reference-consumer-v1.schema.json"
LEGACY_TIP_SCHEMA_SHA256 = "1683c48603f0452c5aa5def1f417418d05796472b31291f84ceca6d0529f27ed"


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def contract_artifacts() -> dict[str, bytes]:
    models = [(model, "validation") for model in
              (SnapshotManifest, ExecutionReferencePage, RefreshStatus, RefreshObservation)]
    _, schema = models_json_schema(models)
    schema.update({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:trillionlane:mfdata:reference-snapshot:1",
        "title": "MFData native source-only bundle and refresh contracts",
        "oneOf": [{"$ref": f"#/$defs/{model.__name__}"} for model, _ in models],
    })
    record = ExecutionReferenceRecord.model_json_schema()
    record.update({"$schema": "https://json-schema.org/draft/2020-12/schema",
                   "$id": "urn:trillionlane:mfdata:execution-reference:1"})
    _, component = models_json_schema(models, ref_template="#/components/schemas/{model}")
    openapi = {
        "openapi": "3.1.0",
        "info": {"title": "MFData native reference contracts", "version": "1.0.0",
                 "description": "Source-only contract documentation. No route or listener exists."},
        "paths": {}, "components": {"schemas": component["$defs"]},
        "x-contract-owner": "MFData", "x-mapping-policy": policy.POLICY_VERSION,
        "x-code-registry": policy.REGISTRY_VERSION,
    }
    artifacts = {SCHEMA_PATH: canonical_bytes(schema) + b"\n",
                 RECORD_SCHEMA_PATH: canonical_bytes(record) + b"\n",
                 OPENAPI_PATH: canonical_bytes(openapi) + b"\n"}
    for model, path, urn in (
        (RefreshObservation, OBSERVATION_SCHEMA_PATH, "urn:trillionlane:mfdata:reference-refresh-observation:1"),
        (RefreshStatus, STATUS_SCHEMA_PATH, "urn:trillionlane:mfdata:reference-refresh-status:1"),
    ):
        standalone = model.model_json_schema()
        standalone.update({"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": urn})
        artifacts[path] = canonical_bytes(standalone) + b"\n"
    return artifacts


def refresh_pin_bytes(root: Path) -> bytes:
    artifacts = contract_artifacts()
    pins = {path: sha256(artifacts[path]) for path in (OBSERVATION_SCHEMA_PATH, STATUS_SCHEMA_PATH)}
    for name in ("synthetic-refresh-observation.json", "synthetic-refresh-status.json"):
        path = f"{FIXTURES}/{name}"
        pins[path] = sha256((root / path).read_bytes())
    return "".join(f"{digest}  {path}\n" for path, digest in sorted(pins.items())).encode()


def validators():
    from jsonschema import Draft202012Validator, FormatChecker
    checker = FormatChecker()
    if "date-time" not in checker.checkers:
        raise ContractError("DATE_TIME_CHECKER_UNAVAILABLE")
    artifacts = contract_artifacts()
    native = load_json(artifacts[RECORD_SCHEMA_PATH])
    envelope = load_json(artifacts[SCHEMA_PATH])
    Draft202012Validator.check_schema(native)
    Draft202012Validator.check_schema(envelope)
    return (Draft202012Validator(native, format_checker=checker),
            Draft202012Validator(envelope, format_checker=checker))


def future_alignment(root: Path) -> dict:
    raw = (root / LEGACY_TIP_SCHEMA_PATH).read_bytes()
    if sha256(raw) != LEGACY_TIP_SCHEMA_SHA256:
        raise ContractError("HISTORICAL_INPUT_DIGEST_MISMATCH")
    return {
        "format": "mfdata-future-tip-alignment-v1", "normative": False,
        "producerContract": policy.EXECUTION_VERSION,
        "producerSchemaSha256": sha256(contract_artifacts()[RECORD_SCHEMA_PATH]),
        "historicalTipConsumerCommit": "ee999f8287329f9ee13f707aa38e3d5b7bc34696",
        "historicalTipSchemaSha256": LEGACY_TIP_SCHEMA_SHA256,
        "historicalTipSchemaRole": "NON_NORMATIVE_INPUT_ONLY",
        "compatibilityWithHistoricalTipV1": "INCOMPATIBLE_BY_DESIGN",
        "tipParserQualification": "NOT_RUN",
        "differences": [
            "Native contractVersion is mfdata-execution-reference-v1, not the historical TIP string 1.",
            "expiresAt is required literal JSON null; timestamps and omission are rejected.",
            "Mapping validity follows supersession/retirement or explicit revocation, never source age.",
            "Immutable refresh receipts and their mutable latest pointer do not change verified-unchanged mapping identity.",
        ],
        "futureTipQualification": [
            "Pin the independently reviewed MFData contract version and schema/fixture hashes.",
            "Adopt native null-only lifecycle and acceptance-time sourceAsOf without field translation or synthetic TTL.",
            "Keep every independently verified route per canonical variant in a set; no first-item-wins selection.",
            "Atomically replace from a complete accepted mapping head; reconcile explicit retirement/revocation.",
            "Failed, incomplete or conflicting refresh observations retain the last accepted mapping head.",
        ],
        "activation": "NOT_AUTHORIZED",
    }


def evidence(root: Path) -> dict:
    native, envelope = validators()
    cases = load_json((root / FIXTURES / "cases.json").read_bytes())
    lines = (root / FIXTURES / "execution-records.jsonl").read_bytes().splitlines()
    if len(cases) != len(lines):
        raise ContractError("FIXTURE_COUNT_MISMATCH")
    results = []
    for case, wire in zip(cases, lines, strict=True):
        schema_valid = native.is_valid(load_json(wire))
        error = None
        try:
            emitted = serialize_record(load_json(wire))
            if emitted != wire:
                raise ContractError("FIXTURE_NOT_CANONICAL")
            producer_valid = True
        except ContractError as exception:
            producer_valid = False
            error = str(exception)
        expected = (case["nativeSchemaValid"], case["producerValid"], case["producerError"])
        actual = (schema_valid, producer_valid, error)
        results.append({"fixtureId": case["id"], "sha256": sha256(wire),
                        "serializedUtf8Bytes": len(wire),
                        "nativeSchema": {"expected": expected[0], "actual": actual[0]},
                        "producer": {"expected": expected[1], "actual": actual[1], "error": error,
                                     "expectedError": expected[2]},
                        "result": "PASS" if actual == expected else "FAIL"})
    envelope_names = ("synthetic-unavailable-manifest.json", "synthetic-unavailable-page.json",
                      "synthetic-manifest.json", "synthetic-page.json", "synthetic-refresh-status.json",
                      "synthetic-refresh-observation.json")
    for name in envelope_names:
        envelope.validate(load_json((root / FIXTURES / name).read_bytes()))
    paths = [f"{FIXTURES}/{name}" for name in ("cases.json", "execution-records.jsonl", "SHA256SUMS", *envelope_names,
             "synthetic-records.json", "build_fixtures.py")]
    paths += ["src/mfdataindia/producer_contract/" + name for name in
              ("schemas.py", "policy.py", "execution_reference.py", "compatibility.py")]
    hashes = {path: sha256((root / path).read_bytes()) for path in paths}
    hashes.update({path: sha256(raw) for path, raw in contract_artifacts().items()})
    hashes[REFRESH_PINS_PATH] = sha256(refresh_pin_bytes(root))
    return {
        "format": "mfdata-native-contract-validation-v1", "mfdataBase": BASE,
        "draftStatus": "UNCOMMITTED_ARCHITECTURE_CONFIRMATION_ONLY",
        "normativeAuthority": "MFData authored strict Pydantic models and schema annotations",
        "recordContract": policy.EXECUTION_VERSION, "bundleContract": "mfdata-reference-snapshot-v1",
        "mappingPolicyVersion": policy.POLICY_VERSION, "codeRegistryVersion": policy.REGISTRY_VERSION,
        "validator": "Development-only jsonschema Draft202012Validator with RFC3339 format checker",
        "fixtureKind": "SYNTHETIC_ONLY", "realExecutionItems": 0,
        "hostedVerify": "NOT_RUN", "liveAcceptance": "NOT_OBSERVED", "tipParserCompatibility": "NOT_CLAIMED",
        "artifacts": hashes, "fixtures": results,
        "result": "PASS" if all(row["result"] == "PASS" for row in results) else "FAIL",
    }


def generated_files(root: Path) -> dict[str, bytes]:
    files = contract_artifacts()
    files[REFRESH_PINS_PATH] = refresh_pin_bytes(root)
    files[EVIDENCE_PATH] = canonical_bytes(evidence(root)) + b"\n"
    files[ALIGNMENT_PATH] = canonical_bytes(future_alignment(root)) + b"\n"
    return files
