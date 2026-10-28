"""Fixed metadata identification evidence. Never a grant/capture-policy admission."""
from copy import deepcopy
from pathlib import Path
import re

from mfdataindia.review import reader_function_identification_inventory as frozen
from mfdataindia.review import reader_function_identification_validation as validation
from mfdataindia.review.broad_reader_capture_spec_v3 import context_shape, DENIALS
from mfdataindia.review.evidence_transport import MAX_RAW_BYTES
from mfdataindia.review.policy import canonical, fingerprint
from mfdataindia.review.reader_access_transport_spec import keys, require, digest, parse as parse

ARTIFACTS = Path("/Users/deviojha/projects/codex/artifacts")
IDENTITY = "dev-reader-fn-261028a"
JOB = "mf-reader-function-identify-261028a"
STAGE = "reader-function-identification-not-permission-acceptance"
SOURCE_HASHES = {
    "src/mfdataindia/review/reader_access_correction.py": "863fd37dfa45be74642cf731f44bbd7cc182e263c95309a7d48a4c91af315d00",
    "src/mfdataindia/review/reader_access_profile_v1.py": "5d54dbee9631c806b039306599ad0de0e07533fe4be54e93c6d9a817bcf5fdb9",
    "src/mfdataindia/review/reader_access_transport_spec.py": "9cc349a6e5ae38a97e752c6e50d303056e472b3ad87da3e66c56717fde263c3e",
    "src/mfdataindia/review/broad_reader_policy.py": "eec850a112b39b5fb147653dba2e17c8452d29592dcd4042d4dfe36de3267653",
    "src/mfdataindia/review/broad_reader_capture_inventory.py": "92225c1edb08e2c3dbfa108ce981b248448fe54aca61a2f5a8fb012178a59416",
    "src/mfdataindia/review/broad_reader_capture_spec_v3.py": "6fb008a86aae30ee44b5d7b367ec97eb192107761dfcd347f65e33747809cb04",
    "src/mfdataindia/review/reader_function_identification_validation.py": "1c6a356e29884e9019727a07bc22adfa781f27be2d6bbd890bb78802e2199978",
    "src/mfdataindia/review/reader_function_identification_inventory.py": "1d7688207bd6395b084c788b274fe741cfef900a198294fc9b11ffadab38db38",
    "src/mfdataindia/review/reader_function_identification.py": "4c44e7334767691404e9d8a42d6c4f51b0668dd2b940427ed2c730aa2d00eb95"
}
BODY_KEYS = ("format", "context", "identification", "completions",
             "inventory_sha256", "source_hashes", "capture_sha256", *DENIALS)


def validate_capture_result(value):
    keys(value, BODY_KEYS)
    require(value["format"] == "reader-function-identification-capture-v1")
    context_shape(value["context"])
    require(value["context"]["version"] == 180006)
    validation.validate_document(value["identification"])
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
    captured = {k: value[k] for k in ("context", "identification", "completions")}
    require(value["capture_sha256"] == fingerprint(captured))
    require(len(canonical(value).encode()) <= MAX_RAW_BYTES)


def result(captured):
    keys(captured, ("context", "identification", "completions"))
    value = deepcopy(captured) | {
        "format": "reader-function-identification-capture-v1",
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
        r"mf-reader-function-identification-20261028-[A-Za-z0-9]{6,16}", root.name))
    for key in ("approval_sha256", "request_sha256", "controller_sha256"):
        digest(value[key])
    require(type(value["controller_git_sha"]) is str
            and re.fullmatch(r"[0-9a-f]{40}", value["controller_git_sha"]))
