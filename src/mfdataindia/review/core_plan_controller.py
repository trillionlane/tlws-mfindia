"""Create-once diagnostic orchestration; no service/data/IAM/schedule mutations.

The injected backend is a narrow provider adapter, not an arbitrary command
executor. A fixed global start record must be claimed before provider reads.
Failed or ambiguous create/run requests are NEVER retried.
"""

import hashlib
import os
from pathlib import Path
import re
import time

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from mfdataindia.api import queries
from mfdataindia.review.certification_input_transport import (
    MAX_REQUEST_BYTES, submission_budget,
)
from mfdataindia.review.policy import canonical, fingerprint
from mfdataindia.review.rollout import CALLER_SA, CONNECTION, Manifest, PROJECT, REGION, verify_evidence
from mfdataindia.review.rollout_cloud import verify_network

ACCOUNT = "trillionlaneassetmanagement@gmail.com"
NAME = "mf-core-plan-diagnostic-261021a"
IDENTITY = "dev-core-plan-261021a"
WORKER = "scripts/diagnose_core_fund_plan_dev.py"
WORKER_SHA = "7ef6110a5032566b8ab42418134c9bbdd45780bb0978624c336ebac5ae4484ab"
QUERY_SHA = "c2043667ab261be1ae02eb9e5f32658c142e325739487582a555530f0e3f691e"
STAGE = "core-plan-diagnostic-not-acceptance"
ARTIFACTS = Path("/Users/deviojha/projects/codex/artifacts")
START = ARTIFACTS / (NAME + "-controller-start.json")
BASE_URL = f"https://{REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/{PROJECT}"


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    manifest: Manifest
    controller_git_sha: StrictStr = Field(pattern=r"^[0-9a-f]{40}$")
    controller_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    protected_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    root: StrictStr

    @model_validator(mode="after")
    def exact_scope(self):
        m = self.manifest
        if (m.rollout_id != IDENTITY or m.cohort != [154117] or m.canary_code != 154117
                or m.git_sha != self.controller_git_sha):
            raise ValueError("fresh exact diagnostic-only identity/Git/cohort required")
        root = Path(self.root)
        if (root.parent != ARTIFACTS or root.is_symlink() or root.resolve() != root
                or not re.fullmatch(r"mf-core-plan-diagnostic-20261021-[A-Za-z0-9]{6,16}", root.name)):
            raise ValueError("fresh diagnostic evidence root required, never a failed rollout root")
        return self


def exclusive(path, value):
    """Create-only durable JSON; never overwrite/resume/remove a prior attempt."""
    raw = (canonical(value) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def inputs(approval):
    m = approval.manifest
    environment = {"DEPLOYMENT_GIT_SHA": m.git_sha,
                   "REVIEW_MANIFEST": m.model_dump_json(), "CORE_PLAN_PROGRAM_SHA256": WORKER_SHA}
    env = [{"name": key, "value": value} for key, value in environment.items()]
    env.append({"name": "MFDATAINDIA_DSN", "valueFrom": {
        "secretKeyRef": {"name": "tlws-mf-review-read-dsn-dev", "key": "1"}}})
    task = {"serviceAccountName": CALLER_SA, "timeoutSeconds": "600", "maxRetries": 0,
            "containers": [{"image": m.image, "command": ["python"], "args": [WORKER],
                            "env": env, "resources": {"limits": {"cpu": "1", "memory": "1Gi"}}}]}
    annotations = {"run.googleapis.com/cloudsql-instances": CONNECTION,
                   "run.googleapis.com/vpc-access-egress": "all-traffic",
                   "run.googleapis.com/network-interfaces": canonical([
                       {"network": "tl-dev-vpc", "subnetwork": "tl-dev-mumbai"}]),
                   "run.googleapis.com/execution-environment": "gen2"}
    request = {"apiVersion": "run.googleapis.com/v1", "kind": "Job",
               "metadata": {"name": NAME, "namespace": PROJECT},
               "spec": {"template": {"metadata": {"annotations": annotations}, "spec": {
                   "taskCount": 1, "parallelism": 1, "template": {"spec": task}}}}}
    body = canonical(request).encode()
    budget = submission_budget(environment, ["python", WORKER])
    # Count the entire ACTUAL HTTP body, including secret references, profile,
    # network and metadata. No optimistic approximation of a CLI request.
    if len(body) > MAX_REQUEST_BYTES:
        raise ValueError("complete diagnostic request exceeds unchanged budget")
    return {"body": body, "request": request, "task": task, "annotations": annotations,
            "environment": environment, "budget": {**budget, "request_bytes": len(body)}}


def validate_task(actual, expected):
    if set(actual) - {"containers", "maxRetries", "serviceAccountName", "timeoutSeconds", "volumes"}:
        raise ValueError("unexpected task capability")
    if (actual.get("serviceAccountName") != CALLER_SA or actual.get("volumes", [])
            or type(actual.get("maxRetries")) is not int or actual["maxRetries"] != 0
            or str(actual.get("timeoutSeconds")) != "600" or len(actual.get("containers", [])) != 1):
        raise ValueError("diagnostic task identity/resources/volumes differ")
    container = actual["containers"][0]
    wanted = expected["containers"][0]
    if (set(container) != set(wanted)
            or any(container[k] != wanted[k] for k in ("image", "command", "args"))
            or container["resources"] not in ({"limits": {"cpu": "1", "memory": "1Gi"}},
                                               {"limits": {"cpu": "1000m", "memory": "1Gi"}})):
        raise ValueError("whole diagnostic container/profile differs")
    rows = container["env"]
    if (len(rows) != len(wanted["env"]) or len({r["name"] for r in rows}) != len(rows)
            or {r["name"]: r for r in rows} != {r["name"]: r for r in wanted["env"]}):
        raise ValueError("all environment values and exact secret references required")


def deployed(job, prepared):
    if job["metadata"]["name"] != NAME or str(job["metadata"]["generation"]) != "1":
        raise ValueError("new exact first-generation diagnostic job required")
    template = job["spec"]["template"]
    spec = template["spec"]
    if spec.get("taskCount") != 1 or spec.get("parallelism") != 1:
        raise ValueError("exact one task and parallelism required")
    annotations = template["metadata"]["annotations"]
    verify_network(annotations)
    if annotations.get("run.googleapis.com/execution-environment") != "gen2":
        raise ValueError("explicit gen2 diagnostic task required")
    task = spec["template"]["spec"]
    validate_task(task, prepared["task"])
    return {"task": task, "task_hash": fingerprint(task),
            "network": {k: annotations[k] for k in prepared["annotations"]},
            "whole_spec": spec, "generation": job["metadata"]["generation"], "before_execute": True}


def validate_result(approval, certificate):
    result = verify_evidence(approval.manifest, STAGE, certificate)
    if (result.get("code") != 154117 or result.get("query_file_sha256") != QUERY_SHA
            or result.get("worker_binding") != {"packaged_program_sha256": WORKER_SHA,
                "manifest_image": approval.manifest.image, "manifest_git_sha": approval.manifest.git_sha}
            or result.get("current_facts_acl_verified_before_planning") is not True
            or result.get("sealed_active_projection", {}).get("complete_capture", {}).get("projection_active") is not True
            or result.get("actual_sibling_queries_executed") != 0
            or any(result.get(k) is not False for k in (
                "historical_K_plan_or_core_role_proved", "performance_acceptance", "rollout_acceptance"))
            or any(result.get(k) != 0 for k in ("data_writes", "source_requests", "human_events_created"))):
        raise ValueError("complete diagnostic-only worker bindings/results required")
    plans = result.get("plans", [])
    expected_queries = [(queries._FACTS_SELECT, {"code": 154117}),
                        (queries._SIBLING_SOURCE_SQL, {"code": 154117}),
                        (queries._BATCH_SIBLING_SOURCE_SQL, {"codes": [154117]})]
    if ([p["query"] for p in plans] != ["own_facts", "single_sibling", "batch_sibling"]
            or any(p.get("ANALYZE") is not False
                   or fingerprint(p["complete_plan"]) != p["complete_plan_hash"] for p in plans)):
        raise ValueError("three complete non-executing diagnostic plans required")
    if any(p.get("SQL_sha256") != hashlib.sha256(sql.encode()).hexdigest()
           or p.get("bindings") != bindings for p, (sql, bindings) in zip(plans, expected_queries)):
        raise ValueError("exact original SQL and entire requested bindings required")
    context = result.get("context", {})
    if ({k: context.get(k) for k in ("effective", "authenticated", "database", "read_only",
        "isolation", "statement_timeout", "lock_timeout", "transaction_timeout")}
            != {"effective": "mfdata_verified_reader", "authenticated": "mfdata_verified_reader",
                "database": "mfdataindia", "read_only": "on", "isolation": "repeatable read",
                "statement_timeout": "5s", "lock_timeout": "5s", "transaction_timeout": "15s"}
            or not isinstance(context.get("snapshot"), str) or not context["snapshot"]):
        raise ValueError("exact restricted bounded read-only snapshot required")
    return result


def run(approval, backend, *, claim=exclusive, clock=time.monotonic, sleep=time.sleep):
    """Backend preflight is READ ONLY; .create/.execute are sole mutations.

    The backend MUST enforce exact request bodies/URLs and explicit account.
    Every record is create-only, including the global precheck-process guard.
    """
    root = Path(approval.root)
    if not root.is_dir() or set(p.name for p in root.iterdir()) != {"approval.json"}:
        raise ValueError("fresh root must contain only its approved input")
    prepared = inputs(approval)
    approval_hash = fingerprint(approval.model_dump(mode="json"))
    claim(START, {"pid": os.getpid(), "approval_hash": approval_hash, "root": str(root)})
    before = None
    try:
        backend.preflight(approval)  # Git/operator/OCI + empty jobs AND executions.
        before = backend.protected()
        claim(root / "protected-before.json", before)
        if fingerprint(before) != approval.protected_hash:
            raise ValueError("approved protected state differs")
        claim(root / "intent.json", {"approval_hash": approval_hash, "request": prepared["request"],
                                     "budget": prepared["budget"], "request_sha256": hashlib.sha256(prepared["body"]).hexdigest()})
        created = backend.create(prepared["body"])
        claim(root / "create-response.json", created)
        deadline = clock() + 300
        for index in range(31):
            job = backend.job()
            claim(root / f"created-job-{index:02}.json", job)
            seal = deployed(job, prepared)
            ready = [c for c in job.get("status", {}).get("conditions", []) if c["type"] == "Ready"]
            if len(ready) == 1 and ready[0]["status"] == "True":
                break
            if len(ready) != 1 or ready[0]["status"] not in ("Unknown", "True") or clock() >= deadline:
                raise ValueError("diagnostic job creation failed/ambiguous")
            sleep(10)
        else:
            raise TimeoutError("diagnostic readiness inspection exhausted")
        claim(root / "deployed-before-execute.json", seal)
        # Recheck current immutable-profile candidate immediately before run.
        if deployed(backend.job(), prepared) != seal or backend.protected() != before:
            raise ValueError("deployed/protected profile drift before execute")
        claim(root / "execute-intent.json", {"job": NAME, "deployed_hash": fingerprint(seal), "body": {}})
        execution = backend.execute()
        name = execution["metadata"]["name"]
        if not re.fullmatch(re.escape(NAME) + r"-[a-z0-9]{5,10}", name):
            raise ValueError("sole execution response identity ambiguous")
        claim(root / "original-execution.json", execution)
        deadline = clock() + 720
        for index in range(73):
            observed = backend.execution(name)
            claim(root / f"execution-{index:02}.json", observed)
            verify_network(observed["metadata"].get("annotations", {}))
            if {k: observed["metadata"]["annotations"].get(k) for k in seal["network"]} != seal["network"]:
                raise ValueError("immutable whole network/environment differs from preexecute seal")
            spec, status = observed["spec"], observed["status"]
            if (observed["metadata"]["name"] != name or spec.get("taskCount") != 1
                    or spec.get("parallelism") != 1 or spec["template"]["spec"] != seal["task"]):
                raise ValueError("WHOLE immutable execution differs from preexecute seal")
            completed = [c for c in status.get("conditions", []) if c["type"] == "Completed"]
            if (len(completed) != 1 or any(status.get(k, 0) for k in ("failedCount", "retriedCount", "cancelledCount"))
                    or completed[0]["status"] == "False"):
                raise ValueError("original diagnostic execution failed/ambiguous; never retry")
            if completed[0]["status"] == "True":
                if status.get("succeededCount") != 1 or status.get("runningCount", 0):
                    raise ValueError("incomplete terminal diagnostic success")
                break
            if completed[0]["status"] != "Unknown" or clock() >= deadline:
                raise TimeoutError("bounded exact execution inspection exhausted")
            sleep(10)
        else:
            raise TimeoutError("bounded exact execution inspection exhausted")
        certificate = backend.certificate(name)  # Bounded log delivery reads only.
        result = validate_result(approval, certificate)
    except Exception as error:
        claim(root / "failure.json", {"error_type": type(error).__name__, "acceptance": False})
        raise
    finally:
        try:
            after = backend.protected()
            claim(root / "protected-after.json", after)
            if before is not None and after != before:
                raise ValueError("protected runtime/IAM/schedule drift; diagnostic not accepted")
        except Exception as error:
            claim(root / "protected-failure.json", {
                "error_type": type(error).__name__, "acceptance": False})
            raise
    # No completed diagnostic receipt exists until the final full protected
    # snapshot succeeds and matches. Even this result is NOT rollout acceptance.
    claim(root / "result.json", {"execution": name, "certificate": certificate,
                                  "diagnostic_only": True, "acceptance": False})
    return result
