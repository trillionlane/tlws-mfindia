"""Complete current permission evidence, not grant, freshness or lock authority."""
from copy import deepcopy
from datetime import datetime
import re
from pathlib import Path

from mfdataindia.review import broad_reader_policy as engine
from mfdataindia.review import broad_reader_capture_inventory as frozen
from mfdataindia.review.evidence_transport import MAX_RAW_BYTES
from mfdataindia.review.policy import canonical, fingerprint
from mfdataindia.review.reader_access_transport_spec import state_shape, seal, keys, require, digest, parse as parse

ARTIFACTS = Path("/Users/deviojha/projects/codex/artifacts")
IDENTITY = "dev-bread-cap-261027s"
JOB = "mf-reader-broad-capture-261027s"
STAGE = "broad-reader-capture-not-write-acceptance"
SOURCE_HASHES = {
    "src/mfdataindia/review/reader_access_correction.py": "863fd37dfa45be74642cf731f44bbd7cc182e263c95309a7d48a4c91af315d00",
    "src/mfdataindia/review/broad_reader_policy.py": "eec850a112b39b5fb147653dba2e17c8452d29592dcd4042d4dfe36de3267653",
    "src/mfdataindia/review/reader_access_profile_v1.py": "5d54dbee9631c806b039306599ad0de0e07533fe4be54e93c6d9a817bcf5fdb9",
    "src/mfdataindia/review/reader_access_transport_spec.py": "9cc349a6e5ae38a97e752c6e50d303056e472b3ad87da3e66c56717fde263c3e",
    "src/mfdataindia/review/broad_reader_capture.py": "66860bd9945e713b60f6b83693fac3421e7e634deb12e03fd0251cb8c9400be1",
}
DENIALS = ("permission_authority", "runtime_authority", "performance_acceptance",
           "FS13_acceptance", "actual_lock_witness", "concurrent_change_exclusion", "future_freshness")
BODY_KEYS = ("format", "context", "state", "candidate", "completions", "candidate_semantics",
             "inventory_sha256", "source_hashes", "capture_sha256", *DENIALS)


def context_shape(row):
    keys(row, frozen.CONTEXT_COLUMNS)
    fixed = {"database": "mfdataindia", "principal": engine.OWNER, "session": engine.OWNER,
        "isolation": "repeatable read", "readonly": "on", "statement_timeout": "5s",
        "lock_timeout": "5s", "transaction_timeout": "15s", "search_path": "pg_catalog", "timezone": "UTC"}
    require(all(type(row[k]) is str and row[k] == v for k, v in fixed.items()))
    require(type(row["version"]) is int and 180000 <= row["version"] < 190000)
    require(type(row["pid"]) is int and row["pid"] > 0)
    require(type(row["snapshot"]) is str and re.fullmatch(r"[0-9]+:[0-9]+:(?:[0-9]+(?:,[0-9]+)*)?", row["snapshot"]))
    require(type(row["transaction_started_at"]) is str and re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z", row["transaction_started_at"]))
    datetime.fromisoformat(row["transaction_started_at"].replace("Z", "+00:00"))


def candidate_shape(rows, state):
    require(type(rows) is list)
    relations = {r["oid"]: r for r in state["relations"]}
    for row in rows:
        keys(row, frozen.CANDIDATE_COLUMNS)
        for key in ("oid", "owner"):
            require(type(row[key]) is int and 0 < row[key] <= 4294967295)
        require(type(row["nspname"]) is str and row["nspname"] in engine.SCHEMAS)
        require(type(row["relname"]) is str and bool(row["relname"]))
        require(row["relkind"] in ("r", "p", "v", "m"))
        seal(row["definition"])
        require(row["definition"]["is_null"] is (row["relkind"] not in ("v", "m")))
        if row["relkind"] in ("v", "m"):
            require(row["definition"]["utf8_bytes"] > 0)
        require(row["oid"] in relations)
        # Frozen public STATE_SQL seals ordinary views only. Materialized-view
        # definitions are independently server-sealed by the candidate query;
        # do not relabel its null state definition as a genuine definition seal.
        common = [k for k in frozen.CANDIDATE_COLUMNS
                  if not (row["relkind"] == "m" and k == "definition")]
        require({k: row[k] for k in common} == {k: relations[row["oid"]][k] for k in common})
    require([r["oid"] for r in rows] == sorted({r["oid"] for r in rows}))
    roots = sorted(r["oid"] for r in state["relations"] if r["relkind"] in ("r", "p", "v", "m"))
    require([r["oid"] for r in rows] == roots)

def validate_capture_result(value):
    keys(value, BODY_KEYS)
    require(value["format"] == "broad-reader-capture-v1")
    context_shape(value["context"])
    require(value["context"]["version"] == 180006)
    state_shape(value["state"])
    engine.prepare(value["state"])
    candidate_shape(value["candidate"], value["state"])
    require(value["candidate_semantics"] == frozen.LABEL)
    require(value["inventory_sha256"] == fingerprint(frozen.inventory()))
    require(value["source_hashes"] == SOURCE_HASHES)
    require(all(value[k] is False for k in DENIALS))
    completions = value["completions"]
    require(type(completions) is list and len(completions) == 6)
    for name, completion in zip(frozen.inventory()["sequence"], completions, strict=True):
        keys(completion, ("id", "command_rows", "driver_rows", "fetched_rows"))
        require(completion["id"] == name)
        expected = len(value["candidate"]) if name == "candidate" else 1
        require(all(type(completion[k]) is int and completion[k] == expected
                    for k in ("command_rows", "driver_rows", "fetched_rows")))
    captured = {k: value[k] for k in ("context", "state", "candidate", "completions")}
    require(value["capture_sha256"] == fingerprint(captured))
    require(len(canonical(value).encode()) <= MAX_RAW_BYTES)


def result(captured):
    keys(captured, ("context", "state", "candidate", "completions"))
    value = deepcopy(captured) | {"format": "broad-reader-capture-v1",
        "candidate_semantics": frozen.LABEL, "inventory_sha256": fingerprint(frozen.inventory()),
        "source_hashes": deepcopy(SOURCE_HASHES), "capture_sha256": fingerprint(captured),
        **dict.fromkeys(DENIALS, False)}
    validate_capture_result(value)
    return value


def validate_result(value):
    keys(value, (*BODY_KEYS, "worker_binding", "input_binding"))
    validate_capture_result({k: value[k] for k in BODY_KEYS})
    input_binding_shape(value["input_binding"])
    b = value["worker_binding"]
    keys(b, ("packaged_program_sha256", "packaged_spec_sha256", "packaged_inventory_sha256",
             "manifest_image", "manifest_git_sha", "packaged_controller_sha256"))
    for k in ("packaged_program_sha256", "packaged_spec_sha256", "packaged_inventory_sha256", "packaged_controller_sha256"):
        digest(b[k])
    require(type(b["manifest_image"]) is str and bool(b["manifest_image"]))
    require(type(b["manifest_git_sha"]) is str and re.fullmatch(r"[0-9a-f]{40}", b["manifest_git_sha"]))
    require(len(canonical(value).encode()) <= MAX_RAW_BYTES)


def input_binding_shape(value):
    keys(value, ("root", "approval_sha256", "request_sha256", "controller_git_sha", "controller_sha256"))
    require(type(value["root"]) is str)
    root = Path(value["root"])
    require(root.parent == ARTIFACTS and re.fullmatch(
        r"mf-reader-broad-capture-20261027-[A-Za-z0-9]{6,16}", root.name))
    for key in ("approval_sha256", "request_sha256", "controller_sha256"):
        digest(value[key])
    require(type(value["controller_git_sha"]) is str
            and re.fullmatch(r"[0-9a-f]{40}", value["controller_git_sha"]))
