"""Fixed metadata attestation evidence. Never a grant/capture-policy admission."""
from copy import deepcopy
from pathlib import Path
import re

from mfdataindia.review import reader_function_attestation_inventory as frozen
from mfdataindia.review import reader_function_attestation_validation as validation
from mfdataindia.review import reader_function_attestation_reference as reference
from mfdataindia.review.broad_reader_capture_spec_v3 import context_shape, DENIALS
from mfdataindia.review.evidence_transport import MAX_RAW_BYTES
from mfdataindia.review.policy import canonical, fingerprint
from mfdataindia.review.reader_access_transport_spec import keys, require, digest, parse as parse

ARTIFACTS = Path("/Users/deviojha/projects/codex/artifacts")
IDENTITY = "dev-reader-fa-261028a"
JOB = "mf-reader-function-attest-261028a"
STAGE = "reader-function-attestation-not-permission-acceptance"
SOURCE_HASHES = {
    "src/mfdataindia/review/reader_access_correction.py": "863fd37dfa45be74642cf731f44bbd7cc182e263c95309a7d48a4c91af315d00",
    "src/mfdataindia/review/reader_access_profile_v1.py": "5d54dbee9631c806b039306599ad0de0e07533fe4be54e93c6d9a817bcf5fdb9",
    "src/mfdataindia/review/reader_access_transport_spec.py": "9cc349a6e5ae38a97e752c6e50d303056e472b3ad87da3e66c56717fde263c3e",
    "src/mfdataindia/review/broad_reader_policy.py": "eec850a112b39b5fb147653dba2e17c8452d29592dcd4042d4dfe36de3267653",
    "src/mfdataindia/review/broad_reader_capture_inventory.py": "92225c1edb08e2c3dbfa108ce981b248448fe54aca61a2f5a8fb012178a59416",
    "src/mfdataindia/review/broad_reader_capture_spec_v3.py": "6fb008a86aae30ee44b5d7b367ec97eb192107761dfcd347f65e33747809cb04",
    "src/mfdataindia/review/reader_function_attestation_validation.py": "efd6dda94f191548587cffad99f95359886c114885fef346da0303f8a2a22cc0",
    "src/mfdataindia/review/reader_function_attestation_inventory.py": "bfb0513c883c780994f96476d481c6d2d8623d61692cde3e8a5f6da58b6b230b",
    "src/mfdataindia/review/reader_function_attestation.py": "08ff61349b25456dddbf181aab794240bd3eb2445326bbe58e3820b949c7cb73",
    "src/mfdataindia/review/reader_function_attestation_reference.py": "1c948380af1d45a5deaf2f68d50998e4d332f8d680da325744c33fb0b15e2b08",
    "src/mfdataindia/review/reader_function_identification_validation.py": "1c6a356e29884e9019727a07bc22adfa781f27be2d6bbd890bb78802e2199978"
}
BODY_KEYS = ("format", "context", "attestation", "completions",
             "reference_comparison", "inventory_sha256", "source_hashes", "capture_sha256", *DENIALS)


def validate_capture_result(value):
    keys(value, BODY_KEYS)
    require(value["format"] == "reader-function-attestation-capture-v1")
    context_shape(value["context"])
    require(value["context"]["version"] == 180006)
    validation.validate_document(value["attestation"])
    comparison = value["reference_comparison"]
    expected = reference.comparison(value["attestation"]["functions"][0])
    keys(comparison, tuple(expected))
    require(all(type(item) is bool for item in comparison.values()))
    require(comparison == expected)
    require(value["inventory_sha256"] == fingerprint(frozen.inventory()))
    require(value["source_hashes"] == SOURCE_HASHES)
    require(all(value[k] is False for k in DENIALS))
    completions = value["completions"]
    require(type(completions) is list and len(completions) == 4)
    for name, completion in zip(frozen.SEQUENCE, completions, strict=True):
        keys(completion, ("id", "command_rows", "driver_rows", "fetched_rows"))
        require(completion["id"] == name)
        require(all(type(completion[k]) is int and completion[k] == 1
                    for k in ("command_rows", "driver_rows", "fetched_rows")))
    captured = {k: value[k] for k in ("context", "attestation", "completions")}
    require(value["capture_sha256"] == fingerprint(captured))
    require(len(canonical(value).encode()) <= MAX_RAW_BYTES)


def result(captured):
    keys(captured, ("context", "attestation", "completions"))
    value = deepcopy(captured) | {
        "format": "reader-function-attestation-capture-v1",
        "reference_comparison": reference.comparison(captured["attestation"]["functions"][0]),
        "inventory_sha256": fingerprint(frozen.inventory()),
        "source_hashes": deepcopy(SOURCE_HASHES),
        "capture_sha256": fingerprint(captured),
        **dict.fromkeys(DENIALS, False),
    }
    validate_capture_result(value)
    return value


def validate_result(value):
    keys(value, (*BODY_KEYS, "worker_binding", "input_binding"))
    validate_capture_result({k: value[k] for k in BODY_KEYS})
    input_binding_shape(value["input_binding"])
    b = value["worker_binding"]
    keys(b, ("packaged_program_sha256", "packaged_spec_sha256", "packaged_inventory_sha256",
             "manifest_image", "manifest_git_sha", "packaged_controller_sha256"))
    for k in ("packaged_program_sha256", "packaged_spec_sha256",
              "packaged_inventory_sha256", "packaged_controller_sha256"):
        digest(b[k])
    require(type(b["manifest_image"]) is str and bool(b["manifest_image"]))
    require(type(b["manifest_git_sha"]) is str
            and re.fullmatch(r"[0-9a-f]{40}", b["manifest_git_sha"]))
    require(len(canonical(value).encode()) <= MAX_RAW_BYTES)


def input_binding_shape(value):
    keys(value, ("root", "approval_sha256", "request_sha256",
                 "controller_git_sha", "controller_sha256"))
    require(type(value["root"]) is str)
    root = Path(value["root"])
    require(root.parent == ARTIFACTS and re.fullmatch(
        r"mf-reader-function-attestation-20261028-[A-Za-z0-9]{6,16}", root.name))
    for key in ("approval_sha256", "request_sha256", "controller_sha256"):
        digest(value[key])
    require(type(value["controller_git_sha"]) is str
            and re.fullmatch(r"[0-9a-f]{40}", value["controller_git_sha"]))
