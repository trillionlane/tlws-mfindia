"""Create-once fixed DEV capture, grant and reader-check state machine.

This is provider-free orchestration logic. A separately reviewed backend must
enforce the exact network/API allowlist. A successful grant receipt is not a
TI acceptance receipt, and no failed or ambiguous stage is retried.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import time

from mfdataindia.review import broad_reader_direct_delivery_controller as prepare
from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review import broad_reader_direct_grant as direct
from mfdataindia.review import broad_reader_direct_capacity as capacity
from mfdataindia.review import broad_reader_direct_reader_check as reader_check
from mfdataindia.review import broad_reader_lock_witness as witness
from mfdataindia.review.policy import canonical, fingerprint


ARTIFACTS = Path("/Users/deviojha/projects/codex/artifacts")
STAGES = {"capture": "direct-capture-readonly",
          "grant": "direct-grant-transaction",
          "reader": "direct-authenticated-reader"}
ROOT_NAMES = spec.ROOT_PREFIXES


class DeliveryError(ValueError):
    """No role or consumer permission acceptance follows from this failure."""


def require(condition, reason):
    if not condition:
        raise DeliveryError(reason)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_DIRECT_PREDECESSOR_MEMBER")
        result[key] = value
    return result


def _read_exact(path):
    require(path.is_file() and not path.is_symlink(),
            "MISSING_OR_MUTABLE_DIRECT_PREDECESSOR")
    raw = path.read_bytes()
    require(0 < len(raw) <= spec.MAX_PAYLOAD_BYTES,
            "OVERSIZED_DIRECT_PREDECESSOR")
    try:
        document = json.loads(raw, object_pairs_hook=_unique)
    except (ValueError, UnicodeError, RecursionError):
        raise DeliveryError("INVALID_DIRECT_PREDECESSOR") from None
    require(type(document) is dict and raw == (canonical(document) + "\n").encode(),
            "NONCANONICAL_DIRECT_PREDECESSOR")
    return document, hashlib.sha256(raw).hexdigest()


def predecessor(approval, *, source_root):
    """Pure exact capture→grant→reader chain before any provider/token/DSN."""
    kind = approval["kind"]
    pointer = approval["predecessor"]
    if kind == "capture":
        require(pointer is None, "CAPTURE_CANNOT_HAVE_PREDECESSOR")
        return None
    expected_kind = "capture" if kind == "grant" else "grant"
    require(type(pointer) is dict and set(pointer) == {"root", "result_sha256"},
            "EXACT_DIRECT_PREDECESSOR_POINTER_REQUIRED")
    spec.sha(pointer["result_sha256"])
    root = Path(pointer["root"])
    require(root.parent == ARTIFACTS and not root.is_symlink()
            and root.resolve() == root
            and re.fullmatch(re.escape(ROOT_NAMES[expected_kind]) +
                             r"[A-Za-z0-9]{6,16}", root.name)
            and root != Path(approval["root"]),
            "EXACT_DIRECT_PREDECESSOR_ROOT_REQUIRED")
    previous_approval, _ = _read_exact(root / "approval.json")
    require(previous_approval.get("kind") == expected_kind,
            "DIRECT_PREDECESSOR_KIND_DRIFT")
    require(previous_approval.get("root") == str(root),
            "DIRECT_PREDECESSOR_INTERNAL_ROOT_DRIFT")
    previous_prepared = validate_approval(previous_approval, source_root=source_root)
    result, result_sha = _read_exact(root / "result.json")
    require(result_sha == pointer["result_sha256"]
            and set(result) == {
                "format", "kind", "approval_sha256", "request_sha256",
                "job", "execution", "certificate", "evidence",
                "protected_sha256", "permission_enabled", "ti_verified"}
            and result["format"] == "mf-broad-reader-direct-delivery-completion-v1"
            and result["kind"] == expected_kind
            and result["approval_sha256"] == fingerprint(previous_approval)
            and result["request_sha256"] == previous_prepared["request_sha256"]
            and result["protected_sha256"] == approval["protected_hash"]
            and result["permission_enabled"] is False
            and result["ti_verified"] is False
            and type(result["job"]) is dict
            and type(result["execution"]) is dict,
            "UNVERIFIED_DIRECT_PREDECESSOR_COMPLETION")
    for key in ("final_git_sha", "image_binding", "protected_hash"):
        require(previous_approval[key] == approval[key],
                "DIRECT_PREDECESSOR_SOURCE_OR_RUNTIME_DRIFT")
    previous_evidence = _evidence(expected_kind, result["certificate"],
                                  previous_prepared)
    require(result["evidence"] == previous_evidence,
            "DIRECT_PREDECESSOR_EVIDENCE_DRIFT")
    payload = approval["payload"]
    prior_payload = previous_approval["payload"]
    marker = direct.custody_marker(payload["window"])
    require(marker == direct.custody_marker(prior_payload["window"]),
            "DIRECT_PREDECESSOR_CUSTODY_MODE_DRIFT")
    require(payload["packaged_source"] == prior_payload["packaged_source"]
            and payload["git_sha"] == prior_payload["git_sha"]
            and payload["image_binding"] == prior_payload["image_binding"],
            "DIRECT_PREDECESSOR_PAYLOAD_DRIFT")
    if kind == "grant":
        require(payload["before_snapshot"] == previous_evidence["before_snapshot"]
                and payload["helper_attestation"] ==
                previous_evidence["helper_attestation"]
                and (marker is None or
                     (payload.get("capture_custody") == marker and
                      previous_evidence.get("custody") == marker)),
                "GRANT_NOT_DERIVED_FROM_COMPLETED_CAPTURE")
    else:
        receipt = previous_evidence["transaction_receipt"]
        require(receipt["stage"] == "COMMITTED_STATE_VERIFIED"
                and payload["grant_receipt_sha256"] == fingerprint(receipt)
                and payload["planned_after"] ==
                prior_payload["before_snapshot"]["plan"]["after"]
                and (marker is None or
                     (payload.get("grant_custody") == marker and
                      receipt.get("custody") == marker)),
                "READER_NOT_DERIVED_FROM_COMPLETED_GRANT")
    return {"kind": expected_kind, "root": str(root),
            "approval": previous_approval, "prepared": previous_prepared,
            "completion": result, "result_sha256": result_sha}


def validate_approval(document, *, source_root):
    require(type(document) is dict and set(document) == {
        "format", "kind", "operator", "project", "region", "final_git_sha",
        "image_binding", "payload", "root", "protected_hash",
        "request_sha256", "predecessor"},
        "EXACT_DIRECT_DELIVERY_APPROVAL_REQUIRED")
    kind = document["kind"]
    require(document["format"] == "mf-broad-reader-direct-delivery-v1"
            and kind in spec.ROLES
            and document["operator"] == spec.OPERATOR
            and document["project"] == spec.PROJECT
            and document["region"] == spec.REGION,
            "EXACT_DIRECT_DELIVERY_IDENTITY_REQUIRED")
    spec.git_sha(document["final_git_sha"])
    spec.sha(document["protected_hash"])
    spec.sha(document["request_sha256"])
    images = document["image_binding"]
    require(type(images) is dict and set(images) == {"app", "tools"},
            "EXACT_DIRECT_DELIVERY_IMAGES_REQUIRED")
    spec.image_binding(images["app"], expected_git_sha=document["final_git_sha"])
    spec.image_binding(images["tools"],
                       expected_git_sha=document["final_git_sha"],
                       repository="mfdataindia-review-tools")
    root = Path(document["root"])
    require(root.parent == ARTIFACTS and not root.is_symlink()
            and root.resolve() == root
            and re.fullmatch(re.escape(ROOT_NAMES[kind]) + r"[A-Za-z0-9]{6,16}",
                             root.name), "EXACT_NEW_DIRECT_EVIDENCE_ROOT_REQUIRED")
    maker = {"capture": prepare.prepare_capture,
             "grant": prepare.prepare_grant,
             "reader": prepare.prepare_reader}[kind]
    candidate = maker(payload=document["payload"], root=document["root"],
                      final_git_sha=document["final_git_sha"],
                      image=images["app"], source_root=source_root)
    require(candidate["request_sha256"] == document["request_sha256"],
            "EXACT_DIRECT_CREATE_BODY_SHA_REQUIRED")
    predecessor(document, source_root=source_root)
    return candidate


def exclusive(path, value):
    """Durable create-only record; no overwrite, resume or cleanup delete."""
    raw = (canonical(value) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _job_status(job, *, execution=None):
    status = job.get("status", {})
    wanted = 1 if execution is not None else 0
    require(type(status) is dict and type(status.get("executionCount", 0)) is int
            and status.get("executionCount", 0) == wanted,
            "EXACT_DIRECT_JOB_EXECUTION_COUNT_REQUIRED")
    latest = status.get("latestCreatedExecution")
    if execution is None:
        require(latest in (None, {}), "PRISTINE_DIRECT_JOB_REQUIRED")
    else:
        require(type(latest) is dict
                and latest.get("name") == execution["name"]
                and latest.get("creationTimestamp") == execution["creationTimestamp"]
                and not latest.get("deletionTimestamp"),
                "EXACT_SOLE_DIRECT_EXECUTION_REQUIRED")


def _inventory(backend, kind, prepared, seal, *, execution=None):
    job = backend.job(kind)
    current = spec.validate_job_export(
        kind, job, prepared, namespace=spec.PROJECT_NUMBER,
        expected_executions=1 if execution is not None else 0)
    require(current == seal, "DIRECT_JOB_PROFILE_OR_UID_DRIFT")
    _job_status(job, execution=execution)
    executions = backend.executions(kind)
    expected_count = 1 if execution is not None else 0
    require(type(executions) is list and len(executions) == expected_count,
            "EXACT_DIRECT_EXECUTION_INVENTORY_REQUIRED")
    if execution is not None:
        spec.validate_execution_export(
            kind, executions[0], prepared, job=seal,
            image=backend.image("app"), original_identity=execution)
    return {"job": job, "executions": executions}


def _terminal_execution(observed):
    status = observed.get("status")
    require(type(status) is dict
            and type(status.get("conditions")) is list,
            "DIRECT_EXECUTION_STATUS_REQUIRED")
    completed = [condition for condition in status["conditions"]
                 if type(condition) is dict and condition.get("type") == "Completed"]
    require(len(completed) == 1
            and completed[0].get("status") in {"Unknown", "True"}
            and all(type(status.get(key, 0)) is int and status.get(key, 0) == 0
                    for key in ("failedCount", "retriedCount", "cancelledCount")),
            "DIRECT_EXECUTION_FAILED_OR_AMBIGUOUS_NO_RETRY")
    if completed[0]["status"] == "True":
        require(type(status.get("succeededCount")) is int
                and status["succeededCount"] == 1
                and type(status.get("runningCount", 0)) is int
                and status.get("runningCount", 0) == 0,
                "INCOMPLETE_DIRECT_EXECUTION_SUCCESS")
        return True
    return False


def _evidence(kind, certificate, prepared):
    payload_sha = prepared["payload_sha256"]
    evidence = spec.verify_certificate(
        certificate, payload_sha256=payload_sha, stage=STAGES[kind])
    task_env = prepared["task"]["containers"][0]["env"]
    configured = {item["name"]: item["value"] for item in task_env
                  if "value" in item}
    bound = spec.payload_from_variables(configured)
    marker = direct.custody_marker(bound["window"])
    if kind == "capture":
        require(set(evidence) == ({"format", "helper_attestation",
                                   "before_snapshot", "packaged_source",
                                   "capture_sha256", "grant_authorized"}
                                  | ({"custody"} if marker is not None else set()))
                and evidence.get("format") ==
                ("mf-broad-reader-prewrite-capture-v2-no-freeze-nonexecutable"
                 if marker is not None else
                 "mf-broad-reader-prewrite-capture-v1-nonexecutable")
                and (marker is None or evidence.get("custody") == marker)
                and evidence.get("grant_authorized") is False
                and type(evidence.get("before_snapshot")) is dict
                and evidence["before_snapshot"].get("permission_enabled") is False
                and evidence["packaged_source"] == bound["packaged_source"]
                and evidence["capture_sha256"] ==
                fingerprint(evidence["before_snapshot"]),
                "INCOMPLETE_DIRECT_CAPTURE_CERTIFICATE")
    elif kind == "grant":
        receipt = evidence.get("transaction_receipt", {})
        plan = bound["before_snapshot"]["plan"]
        require(set(evidence) == {"transaction_receipt", "capacity_proof"}
                and type(receipt) is dict
                and set(receipt) == ({
                    "format", "stage", "request_sha256", "before_sha256",
                    "after_sha256", "source_sha256", "lock_proof",
                    "pregrant_equality", "postgrant_proof", "delta",
                    "materialized_unlocked", "postcommit_permanence_guarantee",
                    "authenticated_reader_verified", "ti_verified",
                    "permission_enabled"}
                    | ({"custody"} if marker is not None else set()))
                and receipt.get("format") ==
                ("mf-broad-reader-direct-transaction-receipt-v2-no-freeze"
                 if marker is not None else
                 "mf-broad-reader-direct-transaction-receipt-v1")
                and (marker is None or receipt.get("custody") == marker)
                and receipt.get("stage") == "COMMITTED_STATE_VERIFIED"
                and receipt.get("request_sha256") == payload_sha
                and receipt.get("before_sha256") == plan["full_before_sha256"]
                and receipt.get("after_sha256") == plan["full_after_sha256"]
                and receipt.get("source_sha256") ==
                fingerprint(bound["packaged_source"])
                and receipt.get("delta") == plan["delta"]
                and receipt.get("materialized_unlocked") ==
                plan["relation_scope"]["materialized_unlocked"]
                and receipt.get("postcommit_permanence_guarantee") is False
                and receipt.get("permission_enabled") is False
                and receipt.get("authenticated_reader_verified") is False
                and receipt.get("ti_verified") is False
                and receipt.get("pregrant_equality", {}).get("before_sha256") ==
                fingerprint(bound["before_snapshot"])
                and receipt.get("pregrant_equality", {}).get("after_sha256") ==
                fingerprint(bound["before_snapshot"])
                and receipt.get("postgrant_proof", {}).get(
                    "planned_after_sha256") == plan["full_after_sha256"]
                and evidence.get("capacity_proof") == capacity.prove_capacity(
                    bound, payload_sha256=payload_sha),
                "INCOMPLETE_DIRECT_GRANT_CERTIFICATE")
        lock = receipt["lock_proof"]
        require(lock == witness.verify_phases(
            plan["relation_scope"], baseline=lock["baseline"],
            tables=lock["tables"], views=lock["views"],
            target_pid=lock["target_pid"], observer_pid=lock["observer_pid"],
            database_oid=lock["database_oid"]),
            "UNMATCHED_DIRECT_GRANT_LOCK_WITNESS")
    else:
        reader = evidence.get("reader_result", {})
        require(set(evidence) == {"reader_result", "grant_receipt_sha256"}
                and type(reader) is dict
                and reader.get("representative_select_completed") is True
                and reader.get("permission_enabled") is False
                and reader.get("ti_verified") is False
                and evidence.get("grant_receipt_sha256") ==
                bound["grant_receipt_sha256"],
                "INCOMPLETE_DIRECT_READER_CERTIFICATE")
        checked = reader_check.validate(
            reader["context"], reader["namespaces"], reader["relations"],
            reader["columns"], planned_after=bound["planned_after"])
        require(reader == {**checked, "representative_select_completed": True,
                           **({"custody": marker} if marker is not None else {})},
                "UNMATCHED_AUTHENTICATED_READER_CAPABILITIES")
    return evidence


def run(approval, backend, *, source_root, claim=exclusive,
        clock=time.monotonic, sleep=time.sleep):
    """One immutable job and one execution, with protected state checked last."""
    prepared = validate_approval(approval, source_root=source_root)
    kind = approval["kind"]
    root = Path(approval["root"])
    require(root.is_dir() and {p.name for p in root.iterdir()} == {"approval.json"},
            "NEW_DIRECT_ROOT_MUST_CONTAIN_ONLY_APPROVAL")
    approval_hash = fingerprint(approval)
    start = ARTIFACTS / (spec.ROLES[kind].job + "-controller-start.json")
    claim(start, {"approval_sha256": approval_hash, "root": str(root),
                  "pid": os.getpid(), "kind": kind})
    before = None
    primary_error = None
    try:
        backend.preflight(approval, prepared)
        require(backend.image("app") == approval["image_binding"]["app"]
                and backend.image("tools") == approval["image_binding"]["tools"],
                "TWO_FINAL_IMAGE_PROOFS_REQUIRED")
        claim(root / "image-resolution.json", approval["image_binding"])
        before = backend.protected()
        claim(root / "protected-before.json", before)
        require(fingerprint(before) == approval["protected_hash"],
                "PROTECTED_BASELINE_DRIFT")
        require(backend.absent(kind) is True,
                "DIRECT_JOB_OR_EXECUTION_ALREADY_EXISTS")
        predecessor(approval, source_root=source_root)
        preimage, intent_sha = spec.require_intent(
            kind, approval["root"], approval["payload"])
        claim(root / "create-intent.json", {
            "approval_sha256": approval_hash,
            "request_sha256": prepared["request_sha256"],
            "job": spec.ROLES[kind].job, "root": str(root),
            "intent_preimage": preimage, "intent_sha256": intent_sha,
            "complete_request": prepared["request"]})
        created = backend.create(kind, prepared["body"])
        claim(root / "create-response.json", created)
        seal = spec.validate_job_export(kind, created, prepared,
                                        namespace=spec.PROJECT_NUMBER)
        _job_status(created)
        claim(root / "created-job-seal.json", seal)
        claim(root / "created-inventory.json",
              _inventory(backend, kind, prepared, seal))
        deadline = clock() + 300
        for index in range(31):
            job = backend.job(kind)
            claim(root / f"created-job-{index:02}.json", job)
            current = spec.validate_job_export(
                kind, job, prepared, namespace=spec.PROJECT_NUMBER)
            require(current == seal, "DIRECT_JOB_PROFILE_DRIFT_DURING_READINESS")
            _job_status(job)
            ready = [c for c in job.get("status", {}).get("conditions", [])
                     if type(c) is dict and c.get("type") == "Ready"]
            require(len(ready) == 1 and ready[0].get("status") in {"Unknown", "True"},
                    "DIRECT_JOB_READINESS_FAILED")
            if ready[0]["status"] == "True":
                break
            require(clock() < deadline, "DIRECT_JOB_READINESS_EXPIRED")
            sleep(10)
        else:
            raise DeliveryError("DIRECT_JOB_READINESS_EXHAUSTED")
        require(backend.protected() == before,
                "PROTECTED_DRIFT_BEFORE_DIRECT_EXECUTION")
        claim(root / "inventory-before-execute.json",
              _inventory(backend, kind, prepared, seal))
        claim(root / "execute-intent.json", {
            "approval_sha256": approval_hash, "job_uid": seal["job_uid"],
            "job": spec.ROLES[kind].job, "body": {}})
        executed = backend.execute(kind)
        claim(root / "original-execution.json", executed)
        identity = spec.validate_execution_export(
            kind, executed, prepared, job=seal, image=backend.image("app"))
        claim(root / "inventory-after-execute.json",
              _inventory(backend, kind, prepared, seal, execution=identity))
        deadline = clock() + 720
        for index in range(73):
            observed = backend.execution(kind, identity["name"])
            claim(root / f"execution-{index:02}.json", observed)
            spec.validate_execution_export(
                kind, observed, prepared, job=seal,
                image=backend.image("app"), original_identity=identity)
            if _terminal_execution(observed):
                break
            require(clock() < deadline, "DIRECT_EXECUTION_INSPECTION_EXPIRED")
            sleep(10)
        else:
            raise DeliveryError("DIRECT_EXECUTION_INSPECTION_EXHAUSTED")
        certificate = backend.certificate(kind, identity["name"])
        result = _evidence(kind, certificate, prepared)
    except BaseException as error:
        primary_error = error
        claim(root / "failure.json", {"error_type": type(error).__name__,
                                      "permission_enabled": False})
        raise
    finally:
        if before is not None:
            try:
                after = backend.protected()
                claim(root / "protected-after.json", after)
                require(after == before, "PROTECTED_RUNTIME_IAM_SCHEDULE_DRIFT")
            except BaseException as error:
                claim(root / "protected-failure.json", {
                    "error_type": type(error).__name__,
                    "permission_enabled": False})
                if primary_error is None:
                    raise
    inventory = _inventory(backend, kind, prepared, seal, execution=identity)
    claim(root / "inventory-before-result.json", inventory)
    final_protected = backend.protected()
    claim(root / "protected-final.json", final_protected)
    require(final_protected == before, "PROTECTED_FINAL_DRIFT")
    completion = {"format": "mf-broad-reader-direct-delivery-completion-v1",
                  "kind": kind, "approval_sha256": approval_hash,
                  "request_sha256": prepared["request_sha256"],
                  "job": inventory["job"],
                  "execution": inventory["executions"][0],
                  "certificate": certificate, "evidence": result,
                  "protected_sha256": fingerprint(final_protected),
                  "permission_enabled": False,
                  "ti_verified": False}
    claim(root / "result.json", completion)
    return completion
