"""Actual emitted bytes versus MFData's own strict, mechanically generated schema."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

from mfdataindia.producer_contract import compatibility as c
from mfdataindia.producer_contract.execution_reference import ContractError, load_json, serialize_record

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / c.FIXTURES
CASES = load_json((FIXTURES / "cases.json").read_bytes())
WIRES = (FIXTURES / "execution-records.jsonl").read_bytes().splitlines()


@pytest.mark.parametrize("case,wire", list(zip(CASES, WIRES, strict=True)), ids=[case["id"] for case in CASES])
def test_final_standalone_bytes_against_native_authored_schema(case, wire):
    native, _ = c.validators()
    value = load_json(wire)
    assert native.is_valid(value) is case["nativeSchemaValid"]
    if case["producerValid"]:
        emitted = serialize_record(value)
        assert emitted == wire
        native.validate(load_json(emitted))
        assert len(emitted) <= 16 * 1024
    else:
        with pytest.raises(ContractError) as error:
            serialize_record(value)
        assert str(error.value) == case["producerError"]
        assert "SYNTHETIC_SECRET_MARKER" not in str(error.value)


def test_native_schema_is_authority_and_historical_tip_input_is_non_normative():
    native = load_json(c.contract_artifacts()[c.RECORD_SCHEMA_PATH])
    assert native["$id"] == "urn:trillionlane:mfdata:execution-reference:1"
    assert native["properties"]["contractVersion"]["const"] == "mfdata-execution-reference-v1"
    assert native["properties"]["expiresAt"]["type"] == "null"
    assert native["properties"]["expiresAt"]["const"] is None
    assert "expiresAt" in native["required"]
    alignment = c.future_alignment(ROOT)
    assert alignment["normative"] is False
    assert alignment["compatibilityWithHistoricalTipV1"] == "INCOMPATIBLE_BY_DESIGN"
    assert alignment["tipParserQualification"] == "NOT_RUN"
    raw = (ROOT / c.LEGACY_TIP_SCHEMA_PATH).read_bytes()
    assert c.sha256(raw) == c.LEGACY_TIP_SCHEMA_SHA256
    assert load_json(raw)["properties"]["expiresAt"]["format"] == "date-time"
    # This is negative historical schema evidence, not a TIP parser qualification.
    from jsonschema import Draft202012Validator, FormatChecker
    old = Draft202012Validator(load_json(raw), format_checker=FormatChecker())
    for case, wire in zip(CASES, WIRES, strict=True):
        if case["producerValid"]:
            assert not old.is_valid(load_json(wire))


def test_checksums_and_artifacts_are_deterministic_and_current():
    first, second = c.generated_files(ROOT), c.generated_files(ROOT)
    assert first == second
    for name, raw in first.items():
        assert (ROOT / name).read_bytes() == raw, f"regenerate {name}"
    for directory in (FIXTURES, ROOT / "contracts/tip-s2-i0-v1.0"):
        for line in (directory / "SHA256SUMS").read_text().splitlines():
            expected, name = line.split("  ")
            assert c.sha256((directory / name).read_bytes()) == expected
    report = load_json(first[c.EVIDENCE_PATH])
    assert report["result"] == "PASS" and report["realExecutionItems"] == 0
    assert report["tipParserCompatibility"] == "NOT_CLAIMED"
    assert report["hostedVerify"] == "NOT_RUN" and report["liveAcceptance"] == "NOT_OBSERVED"
    semantic = {row["fixtureId"] for row in report["fixtures"]
                if row["nativeSchema"]["actual"] and not row["producer"]["actual"]}
    assert semantic >= {"wrong-derived-id", "over-byte-ceiling", "integer-float-amfi-code"}
    openapi = load_json(first[c.OPENAPI_PATH])
    assert openapi["paths"] == {}
    assert "DataSectionPage" not in openapi["components"]["schemas"]
    item = openapi["components"]["schemas"]["ExecutionReferenceRecord"]
    assert item["properties"]["expiresAt"]["const"] is None
    assert item["additionalProperties"] is False
    assert "$ref" not in item
    for path in (c.SCHEMA_PATH, c.RECORD_SCHEMA_PATH, c.OPENAPI_PATH):
        assert b"urn:trillionlane:mfdata:execution-reference-consumer" not in first[path]


def test_native_format_checks_are_active():
    native, _ = c.validators()
    value = load_json(WIRES[0])
    value["sourceAsOf"] = "2026-02-30T10:00:00Z"
    assert not native.is_valid(value)


def test_fixture_bytes_and_hash_manifest_regenerate():
    spec = importlib.util.spec_from_file_location("producer_fixture_builder", FIXTURES / "build_fixtures.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    for name, raw in builder.files().items():
        assert (FIXTURES / name).read_bytes() == raw


def test_exporter_main_checks_all_generated_artifacts():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/export_producer_contract.py"), "--check"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_native_validator_and_format_checker_remain_dev_only():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert not any(item.startswith(("jsonschema", "rfc3339-validator")) for item in project["dependencies"])
    assert "jsonschema>=4.23,<5" in project["optional-dependencies"]["dev"]
    assert "rfc3339-validator>=0.1.4,<0.2" in project["optional-dependencies"]["dev"]


@pytest.mark.parametrize("name", ["pyproject.toml", "uv.lock"])
def test_dependency_delta_retains_exact_predecessor_and_rejects_tamper(name, tmp_path):
    from dataset_source_history import PRODUCER_DEPENDENCIES, PRODUCER_DEPENDENCY_BASE, source_native_previous
    from pypdf_patch_preservation import restore_pypdf_parent
    current = (ROOT / name).read_bytes()
    old = subprocess.check_output(["git", "show", PRODUCER_DEPENDENCY_BASE + ":" + name], cwd=ROOT)
    previous_digest, current_digest = PRODUCER_DEPENDENCIES[name]
    assert c.sha256(current) == current_digest and c.sha256(old) == previous_digest
    assert source_native_previous(ROOT / name, current) == old
    assert source_native_previous(ROOT / name, old) == old
    assert source_native_previous(tmp_path / name, current) == current
    for changed in (current + b"\n# unapproved", current[:-1], current + current):
        assert source_native_previous(ROOT / name, changed) == changed
        with pytest.raises(AssertionError):
            restore_pypdf_parent(ROOT / name, changed)
    if name == "uv.lock":
        original, updated = tomllib.loads(old.decode()), tomllib.loads(current.decode())
        prior = {item["name"]: item for item in original["package"] if item["name"] != "mfdataindia"}
        after = {item["name"]: item for item in updated["package"]}
        assert all(after[key] == value for key, value in prior.items())
