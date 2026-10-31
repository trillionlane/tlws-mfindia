"""One current, provider-free internal DEV operation contract.

A candidate and its hashes are not human approval or fresh runtime readiness.
No provider, secret, database, journal or mutation intent is accessed here.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

from . import broad_reader_direct_delivery_spec as spec
from . import broad_reader_direct_grant as direct
from . import broad_reader_policy as policy
from . import broad_reader_source_authority as source
from . import reader_access_profile as profiles
from .policy import canonical, fingerprint

FORMAT = "mf-reader-execution-plan-v1"
STAGE_FORMAT = "mf-reader-execution-stage-v1"
EXECUTION_LIMITATIONS = ("SINGLE_AUTHORITATIVE_EXECUTOR_REQUIRED", "NO_DISTRIBUTED_EXCLUSION",
                        "PRIVILEGED_HISTORY_DELETION_NOT_RECOVERABLE_RO05")
KINDS = ("capture", "grant", "reader")
STAGES = {"capture": "direct-capture-readonly", "grant": "direct-grant-transaction",
          "reader": "direct-authenticated-reader"}
TARGET = {"project": spec.PROJECT, "region": spec.REGION, "operator": spec.OPERATOR,
          "database": spec.DATABASE, "connection": spec.CONNECTION,
          "owner": spec.OWNER_ROLE, "reader": spec.READER_ROLE,
          "schemas": list(policy.SCHEMAS)}
_NEW = ("contract", "evidence", "diagnostics", "journal", "worker", "controller",
        "backend", "application", "authority")
_EXISTING = (
    "broad_reader_direct_delivery_spec", "broad_reader_direct_delivery_backend",
    "broad_reader_direct_grant", "broad_reader_direct_plan", "broad_reader_direct_capacity",
    "broad_reader_direct_reader_check", "broad_reader_live_snapshot", "broad_reader_policy",
    "broad_reader_source_authority", "broad_reader_view_profile", "broad_reader_capture_inventory",
    "broad_reader_function_scope", "broad_reader_function_closure", "broad_reader_dependency_scope",
    "broad_reader_lock_witness", "reader_function_attestation", "reader_function_attestation_spec",
    "reader_function_attestation_reference", "reader_access_profile", "privileges", "app",
    "evidence_transport",
    "reader_execution_source_reference", "auth",
)
CODE_PATHS = tuple(sorted(
    ["src/mfdataindia/review/reader_execution_" + name + ".py" for name in _NEW]
    + ["src/mfdataindia/review/" + name + ".py" for name in _EXISTING]
    + ["scripts/reader_execution_dev.py"]))


class ContractError(ValueError):
    """Static, value-free operation input failure."""


def require(condition, reason):
    if not condition:
        raise ContractError(reason)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_MEMBER")
        result[key] = value
    return result


def parse_json(raw):
    require(type(raw) is bytes and 0 < len(raw) <= spec.MAX_PAYLOAD_BYTES,
            "COMPLETE_CANONICAL_INPUT_REQUIRED")
    def invalid(_value):
        raise ContractError("INVALID_CANONICAL_JSON")
    try:
        value = json.loads(raw, object_pairs_hook=_unique, parse_constant=invalid)
        encoded = canonical(value).encode()
        require(raw in (encoded, encoded + b"\n"), "NONCANONICAL_INPUT")
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError, TypeError, OverflowError):
        raise ContractError("INVALID_CANONICAL_JSON") from None


def _code_bytes(root, name):
    root = Path(root).resolve()
    path = root / name
    if not path.is_file() and name.startswith("src/mfdataindia/"):
        found = importlib.util.find_spec(name[4:-3].replace("/", "."))
        require(found is not None and type(found.origin) is str, "MISSING_PACKAGED_CODE")
        path = Path(found.origin)
    require(path.is_file() and not path.is_symlink(), "MISSING_PACKAGED_CODE")
    return path.read_bytes()


def code_binding(root):
    rows = []
    for name in CODE_PATHS:
        raw = _code_bytes(root, name)
        rows.append({"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    return {"format": "mf-reader-execution-code-v1", "files": rows}


def _recorded_code(value):
    require(type(value) is dict and set(value) == {"format", "files"}
            and value["format"] == "mf-reader-execution-code-v1"
            and type(value["files"]) is list
            and len(value["files"]) == len(CODE_PATHS), "INCOMPLETE_CODE_BINDING")
    for row, name in zip(value["files"], CODE_PATHS, strict=True):
        require(type(row) is dict and set(row) == {"path", "bytes", "sha256"}
                and row["path"] == name and type(row["bytes"]) is int
                and 0 < row["bytes"] <= spec.MAX_PAYLOAD_BYTES, "INCOMPLETE_CODE_BINDING")
        spec.sha(row["sha256"])


def operation_root(plan):
    spec.role_for("capture", operation_id=plan["operation_id"])
    return str(spec.ARTIFACTS / ("mf-reader-internal-" + plan["operation_id"]))


def validate_plan(value, *, source_root, expected_git, inspect_only=False, now=None):
    require(type(value) is dict and set(value) == {
        "format", "operation_id", "target", "reader_profile", "git_sha", "image_binding",
        "packaged_source", "code_binding", "protected_sha256", "window", "probe_codes",
        "application_trust_sha256"},
        "EXACT_OPERATION_PLAN_REQUIRED")
    require(value["format"] == FORMAT and value["target"] == TARGET
            and value["reader_profile"] == profiles.BROAD, "FIXED_INTERNAL_POLICY_REQUIRED")
    spec.role_for("capture", operation_id=value["operation_id"])
    require(value["operation_id"] is not None, "CURRENT_OPERATION_ID_REQUIRED")
    require(value["git_sha"] == spec.git_sha(expected_git), "OPERATION_GIT_MISMATCH")
    spec.sha(value["protected_sha256"])
    spec.sha(value["application_trust_sha256"])
    images = value["image_binding"]
    require(type(images) is dict and set(images) == {"app", "tools"}, "COMPLETE_IMAGES_REQUIRED")
    for key, repo in (("app", "mfdataindia"), ("tools", "mfdataindia-review-tools")):
        spec.image_binding(images[key], expected_git_sha=expected_git, repository=repo)
    try:
        start, end = direct.direct_window(value["window"])
    except policy.PolicyError:
        raise ContractError("OPERATION_WINDOW_INVALID") from None
    require(direct.custody_marker(value["window"]) == {
        "mode": "DEV_NO_FREEZE_V1", "exclusive_administration": False,
        "data_schema_custody": False, "future_owner_provisioning": False,
        "dev_no_freeze_risk_accepted": True, "postcommit_permanence_guarantee": False},
        "EXPLICIT_DEV_NO_FREEZE_MODE_REQUIRED")
    if not inspect_only:
        require(end > (now or datetime.now(timezone.utc)), "OPERATION_WINDOW_EXPIRED")
    codes = value["probe_codes"]
    require(type(codes) is dict and set(codes) == {"monthly", "multi", "book"}
            and all(type(code) is int and 0 < code <= 2147483647 for code in codes.values()),
            "FIXED_APPLICATION_PROBES_REQUIRED")
    _recorded_code(value["code_binding"])
    require(type(value["packaged_source"]) is dict, "COMPLETE_SOURCE_BINDING_REQUIRED")
    if not inspect_only:
        require(value["code_binding"] == code_binding(source_root), "OPERATION_CODE_MISMATCH")
        source.require_packaged_source(value["packaged_source"], source_root)
    # Inspect-only reads recorded seals. It cannot become an execution input.
    require(len(canonical(value).encode()) <= spec.MAX_PAYLOAD_BYTES, "COMPLETE_PLAN_TOO_LARGE")
    operation_root(value)
    return deepcopy(value)


def candidate(*, operation_id, git_sha, images, window, protected_sha256,
              probe_codes, application_trust_sha256, source_root, expected_git):
    value = {"format": FORMAT, "operation_id": operation_id, "target": deepcopy(TARGET),
             "reader_profile": profiles.BROAD, "git_sha": git_sha,
             "image_binding": deepcopy(images), "window": deepcopy(window),
             "protected_sha256": protected_sha256, "probe_codes": deepcopy(probe_codes),
             "application_trust_sha256": application_trust_sha256,
             "packaged_source": source.verify_packaged_source(source_root),
             "code_binding": code_binding(source_root)}
    return validate_plan(value, source_root=source_root, expected_git=expected_git)


def stage_payload(plan, kind, predecessors):
    require(kind in KINDS and type(predecessors) is list, "INVALID_OPERATION_STAGE")
    require((kind == "capture" and not predecessors)
            or (kind == "grant" and len(predecessors) == 1)
            or (kind == "reader" and len(predecessors) in (1, 2)), "INCOMPLETE_PREDECESSOR_CHAIN")
    payload = {"format": STAGE_FORMAT, "plan": deepcopy(plan), "kind": kind,
               "predecessors": deepcopy(predecessors), "create_only_intent_sha256": "0" * 64}
    payload["create_only_intent_sha256"] = spec.intent_preimage(
        kind, operation_root(plan), payload, operation_id=plan["operation_id"])[1]
    return payload


def validate_stage(payload, *, source_root, expected_git, inspect_only=False):
    require(type(payload) is dict and set(payload) == {
        "format", "plan", "kind", "predecessors", "create_only_intent_sha256"}
        and payload["format"] == STAGE_FORMAT, "EXACT_STAGE_PAYLOAD_REQUIRED")
    validate_plan(payload["plan"], source_root=source_root, expected_git=expected_git,
                  inspect_only=inspect_only)
    require(payload == stage_payload(payload["plan"], payload["kind"], payload["predecessors"]),
            "STAGE_IDENTITY_OR_INTENT_MISMATCH")
    spec.encode_payload(payload)  # Complete environment/request capacity, never truncation.
    return deepcopy(payload)


def package_hashes(root):
    return {"worker_sha256": hashlib.sha256(_code_bytes(root, "scripts/reader_execution_dev.py")).hexdigest(),
            "controller_sha256": hashlib.sha256(_code_bytes(root,
                "src/mfdataindia/review/reader_execution_controller.py")).hexdigest(),
            "spec_sha256": hashlib.sha256(Path(spec.__file__).read_bytes()).hexdigest()}


def prepare_request(plan, kind, predecessors, *, source_root, expected_git):
    from . import reader_execution_evidence as evidence
    checked = validate_plan(plan, source_root=source_root, expected_git=expected_git)
    payload = validate_stage(stage_payload(checked, kind, predecessors),
                             source_root=source_root, expected_git=expected_git)
    prior = evidence.verify_chain(checked, kind, predecessors)
    if kind == "grant":
        evidence.prove_capacity(checked, prior["capture"], predecessors)
    return spec.request(kind, root=operation_root(checked), final_git_sha=expected_git,
                        image=checked["image_binding"]["app"], payload=payload,
                        source_sha256=fingerprint(checked["packaged_source"]),
                        operation_id=checked["operation_id"], **package_hashes(source_root))


def dry_run(plan, *, source_root, expected_git):
    checked = validate_plan(plan, source_root=source_root, expected_git=expected_git)
    prepared = prepare_request(checked, "capture", [], source_root=source_root,
                               expected_git=expected_git)
    return {"format": "mf-reader-execution-validation-v1", "state": "LOCAL_VALIDATION_PASSED_NO_DISPATCH",
            "execution_limitations": list(EXECUTION_LIMITATIONS),
            "operation_id": checked["operation_id"], "plan_sha256": fingerprint(checked),
            "capture_request_sha256": prepared["request_sha256"], "dispatches": 0,
            "mutation_intents_written": 0, "consumer_acceptance": "PENDING_RO04",
            "unverified": ["actual_merged_checkout", "runtime_images_twice", "protected_baseline", "target_inventory",
                           "prior_operation_effects", "live_window", "derived_stage_capacity",
                           "RO04_bounded_plan_authority", "database_and_application_readiness"]}
