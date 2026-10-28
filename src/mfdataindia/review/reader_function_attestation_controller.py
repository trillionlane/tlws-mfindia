"""Create-once diagnostic orchestration; no service/data/IAM/schedule mutations.

The injected backend is a narrow provider adapter, not an arbitrary command
executor. A fixed global start record must be claimed before provider reads.
Failed or ambiguous create/run requests are NEVER retried.
"""

import hashlib
from copy import deepcopy
from datetime import datetime
import os
from pathlib import Path
import re
import time

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from mfdataindia.review import reader_function_attestation_spec as permission
from mfdataindia.review.certification_input_transport import (
    MAX_REQUEST_BYTES, submission_budget,
)
from mfdataindia.review.policy import canonical, fingerprint
from mfdataindia.review.rollout import CONNECTION, Manifest, PROJECT, REGION, verify_evidence
from mfdataindia.review.rollout_cloud import verify_network

ACCOUNT = "trillionlaneassetmanagement@gmail.com"
OWNER_SA = "tlws-mf-data-migrate-dev@trillionlane-dev.iam.gserviceaccount.com"
NAME = "mf-reader-function-attest-261028a"
IDENTITY = "dev-reader-fa-261028a"
WORKER = "scripts/collect_reader_function_attestation_dev.py"
WORKER_SHA = "6b0bd0afa08a123d1da6b509ff03b92acb64b292c35f1ebeba06a2fb477d2d14"
SPEC_SHA = "0456c8bb5f4642c083663e2569856503aabbc35b0b9c88cf4cd331feda28d523"
INVENTORY_SHA = "bfb0513c883c780994f96476d481c6d2d8623d61692cde3e8a5f6da58b6b230b"
STAGE = "reader-function-attestation-not-permission-acceptance"
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
                or not re.fullmatch(r"mf-reader-function-attestation-20261028-[A-Za-z0-9]{6,16}", root.name)):
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
                   "REVIEW_MANIFEST": m.model_dump_json(), "READER_FUNCTION_ATTESTATION_PROGRAM_SHA256": WORKER_SHA,
                   "READER_FUNCTION_ATTESTATION_SPEC_SHA256": SPEC_SHA,
                   "READER_FUNCTION_ATTESTATION_INVENTORY_SHA256": INVENTORY_SHA}
    environment["READER_FUNCTION_ATTESTATION_APPROVAL"] = canonical(approval.model_dump(mode="json"))
    environment["READER_FUNCTION_ATTESTATION_CONTROLLER_SHA256"] = approval.controller_sha256
    env = [{"name": key, "value": value} for key, value in environment.items()]
    env.append({"name": "MFDATAINDIA_DSN", "valueFrom": {
        "secretKeyRef": {"name": "tlws-mf-data-admin-dsn-dev", "key": "1"}}})
    task = {"serviceAccountName": OWNER_SA, "timeoutSeconds": "600", "maxRetries": 0,
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
    if (actual.get("serviceAccountName") != OWNER_SA or actual.get("volumes", [])
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


INFO_ANNOTATIONS = {"run.googleapis.com/client-name", "run.googleapis.com/client-version"}


def informational_annotations(annotations):
    values = {k: v for k, v in annotations.items() if k in INFO_ANNOTATIONS}
    if any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", v) for v in values.values()):
        raise ValueError("invalid informational client provenance")
    return values


def provider_labels(labels, *, parent=None):
    wanted = {"cloud.googleapis.com/location": REGION}
    optional = {"run.googleapis.com/satisfiesPzs", "client.knative.dev/nonce"}
    if parent is not None:
        wanted.update({"run.googleapis.com/job": NAME,
                       "run.googleapis.com/jobUid": parent["identity"]["uid"],
                       "run.googleapis.com/jobGeneration": "1"})
        optional.add("run.googleapis.com/jobResourceVersion")
    else:
        optional.add("run.googleapis.com/lastUpdatedTime")
    if set(labels) - set(wanted) - optional or any(labels.get(k) != v for k, v in wanted.items()):
        raise ValueError("exact location and immutable parent labels required")
    for key, value in labels.items():
        if key in wanted:
            continue
        if key.endswith("/satisfiesPzs"):
            valid = value in ("true", "false")
            if parent and key in parent["labels"]:
                valid = valid and value == parent["labels"][key]
        elif key.endswith("/nonce"):
            valid = isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value)
            if parent and key in parent["template_labels"]:
                valid = valid and value == parent["template_labels"][key]
        elif key.endswith("/lastUpdatedTime"):
            valid = isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z", value)
            if valid:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            # Provider jobResourceVersion is opaque, not an execution override.
            # Retain it in the original immutable Execution identity, then bind
            # every subsequent inventory/poll to that exact original value.
            valid = isinstance(value, str) and 0 < len(value) <= 256 and value.isprintable()
        if not valid:
            raise ValueError("invalid provider-generated informational label")


def resource_identity(document, *, namespace, name, kind, prepared=None, parent=None):
    """Reject unapproved metadata capabilities, retain stable provider identity.

    ObjectMeta docs say ownerReferences is unsupported, but actual v1 metadata
    emits it. If present it must agree with the mandatory parent UID labels.
    """
    if (set(document) - {"apiVersion", "kind", "metadata", "spec", "status"}
            or document.get("apiVersion") != "run.googleapis.com/v1"
            or document.get("kind") != kind):
        raise ValueError("exact resource kind/schema required")
    metadata = document["metadata"]
    allowed = {"name", "namespace", "uid", "creationTimestamp", "generation",
               "resourceVersion", "selfLink", "labels", "annotations"}
    if kind == "Execution":
        allowed.add("ownerReferences")
    if (set(metadata) - allowed or metadata.get("name") != name
            or metadata.get("namespace") not in (namespace, PROJECT)
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]{15,127}", metadata.get("uid", ""))
            or isinstance(metadata.get("generation"), bool)
            or str(metadata.get("generation")) != "1"):
        raise ValueError("new exact provider resource identity required")
    created = metadata.get("creationTimestamp", "")
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z", created):
        raise ValueError("provider UTC creation timestamp required")
    datetime.fromisoformat(created.replace("Z", "+00:00"))
    if "selfLink" in metadata:
        plural = "jobs" if kind == "Job" else "executions"
        if metadata["selfLink"] != f"/apis/run.googleapis.com/v1/namespaces/{metadata['namespace']}/{plural}/{name}":
            raise ValueError("resource selfLink differs from sealed identity")
    annotations = metadata.get("annotations", {})
    generated = {"run.googleapis.com/creator", "run.googleapis.com/lastModifier",
                 "run.googleapis.com/operation-id"} | INFO_ANNOTATIONS
    informational_annotations(annotations)
    expected = prepared["annotations"] if kind == "Execution" else {}
    if (set(annotations) - generated - set(expected)
            or any(annotations.get(k) != v for k, v in expected.items())
            or annotations.get("run.googleapis.com/creator") != ACCOUNT
            or ("run.googleapis.com/lastModifier" in annotations
                and annotations["run.googleapis.com/lastModifier"] != ACCOUNT)):
        raise ValueError("unapproved resource annotation/creator capability")
    operation = annotations.get("run.googleapis.com/operation-id")
    if operation is not None and not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", operation):
        raise ValueError("unexpected provider operation identity")
    labels = metadata.get("labels", {})
    provider_labels(labels, parent=parent if kind == "Execution" else None)
    identity = {k: metadata[k] for k in ("name", "namespace", "uid", "creationTimestamp", "generation")}
    if kind == "Execution":
        owners = metadata.get("ownerReferences", [])
        expected_owner = {"apiVersion": "serving.knative.dev/v1", "kind": "Job", "name": NAME,
                          "uid": parent["identity"]["uid"], "controller": True, "blockOwnerDeletion": True}
        if (owners not in ([], [expected_owner])
                or any(type(owner.get(k)) is not bool for owner in owners
                       for k in ("controller", "blockOwnerDeletion"))):
            raise ValueError("provider ownerReference differs from sealed parent UID")
        if informational_annotations(annotations) != parent["template_provider_annotations"]:
            raise ValueError("execution client provenance differs from sealed template")
        identity.update({"labels": labels, "annotations": annotations, "ownerReferences": owners})
    return deepcopy(identity)


def deployed(job, prepared, *, namespace):
    identity = resource_identity(job, namespace=namespace, name=NAME, kind="Job")
    if set(job["spec"]) != {"template"}:
        raise ValueError("template-only JobSpec required; execution tokens forbidden")
    if str(job["metadata"]["generation"]) != "1":
        raise ValueError("new exact first-generation diagnostic job required")
    template = job["spec"]["template"]
    if (set(template) != {"metadata", "spec"}
            or set(template["metadata"]) - {"annotations", "labels"}
            or "annotations" not in template["metadata"]):
        raise ValueError("whole approved execution template metadata required")
    spec = template["spec"]
    if (set(spec) != {"taskCount", "parallelism", "template"}
            or any(type(spec.get(k)) is not int or spec[k] != 1 for k in ("taskCount", "parallelism"))
            or set(spec["template"]) != {"spec"}):
        raise ValueError("exact one task and parallelism required")
    annotations = template["metadata"]["annotations"]
    client = informational_annotations(annotations)
    labels = template["metadata"].get("labels", {})
    if (set(labels) - {"client.knative.dev/nonce"}
            or any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", v) for v in labels.values())):
        raise ValueError("unapproved template label")
    verify_network(annotations)
    if annotations != {**prepared["annotations"], **client}:
        raise ValueError("WHOLE exact approved annotations required; no extra capabilities")
    task = spec["template"]["spec"]
    validate_task(task, prepared["task"])
    return deepcopy({"task": task, "task_hash": fingerprint(task),
            "network": annotations, "whole_spec": spec, "whole_job_spec": job["spec"],
            "template_labels": labels, "template_provider_annotations": client,
            "identity": identity, "labels": job["metadata"]["labels"],
            "provider_annotations": {k: v for k, v in job["metadata"]["annotations"].items()
                                     if k != "run.googleapis.com/operation-id"},
            "before_execute": True})


def validated_image_resolution(proof, prepared):
    """Validate the narrow fresh backend receipt, never a caller child override."""
    keys = {"image", "resolved_image", "config_digest", "git_sha", "platform", "layers_downloaded"}
    image = prepared["task"]["containers"][0]["image"]
    repository = image.rsplit("@", 1)[0]
    if (type(proof) is not dict or set(proof) != keys
            or proof["image"] != image
            or proof["git_sha"] != prepared["environment"]["DEPLOYMENT_GIT_SHA"]
            or proof["platform"] != "linux/amd64"
            or type(proof["layers_downloaded"]) is not int or proof["layers_downloaded"] != 0
            or not isinstance(proof["resolved_image"], str)
            or not re.fullmatch(re.escape(repository) + r"@sha256:[0-9a-f]{64}", proof["resolved_image"])
            or not isinstance(proof["config_digest"], str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", proof["config_digest"])):
        raise ValueError("exact authenticated image resolution receipt required")
    return deepcopy(proof)


def immutable_execution(execution, prepared, seal, *, namespace, identity=None):
    name = execution["metadata"]["name"]
    if not re.fullmatch(re.escape(NAME) + r"-[a-z0-9]{5,10}", name):
        raise ValueError("sole execution response identity ambiguous")
    actual = resource_identity(execution, namespace=namespace, name=name, kind="Execution",
                               prepared=prepared, parent=seal)
    if identity is not None and actual != identity:
        raise ValueError("original immutable execution identity changed")
    comparable = execution["spec"]
    if "image_resolution" in prepared:
        proof = validated_image_resolution(prepared["image_resolution"], prepared)
        comparable = deepcopy(comparable)
        container = comparable["template"]["spec"]["containers"][0]
        if container["image"] == proof["resolved_image"]:
            container["image"] = proof["image"]
    if (comparable != seal["whole_spec"]
            or actual["namespace"] != seal["identity"]["namespace"]
            or actual["uid"] == seal["identity"]["uid"]):
        raise ValueError("WHOLE immutable execution/parent differs from seal")
    return actual


def job_execution_status(job, execution=None):
    status = job.get("status", {})
    count, latest = status.get("executionCount", 0), status.get("latestCreatedExecution")
    expected_count = 1 if execution is not None else 0
    if type(count) is not int or count != expected_count:
        raise ValueError("exact sole job execution count required")
    if execution is None:
        if latest not in (None, {}):
            raise ValueError("job already has a latest-created execution")
    else:
        if (not isinstance(latest, dict) or latest.get("name") != execution["name"]
                or latest.get("creationTimestamp") != execution["creationTimestamp"]
                or latest.get("deletionTimestamp")):
            raise ValueError("latest execution does not bind sole run response")


def execution_inventory(backend, prepared, seal, *, namespace, execution=None):
    """Fail closed on unavailable/lagging/extra executions; never submit a retry."""
    job, executions = backend.job(), backend.executions()
    if deployed(job, prepared, namespace=namespace) != seal:
        raise ValueError("whole created job identity/profile drift")
    job_execution_status(job, execution)
    expected_count = 1 if execution is not None else 0
    if not isinstance(executions, list) or len(executions) != expected_count:
        raise ValueError("exact sole execution inventory required")
    if execution is not None:
        immutable_execution(executions[0], prepared, seal, namespace=namespace, identity=execution)
    return {"job": job, "executions": executions}


def validate_result(approval, certificate):
    result = verify_evidence(approval.manifest, STAGE, certificate)
    permission.validate_result(result)
    if result["worker_binding"] != {
        "packaged_program_sha256": WORKER_SHA, "packaged_spec_sha256": SPEC_SHA,
        "packaged_inventory_sha256": INVENTORY_SHA, "packaged_controller_sha256": approval.controller_sha256,
        "manifest_image": approval.manifest.image, "manifest_git_sha": approval.manifest.git_sha,
    }:
        raise ValueError("complete packaged permission worker/spec/Git binding required")
    wanted = {"root": approval.root,
              "approval_sha256": fingerprint(approval.model_dump(mode="json")),
              "request_sha256": hashlib.sha256(inputs(approval)["body"]).hexdigest(),
              "controller_git_sha": approval.controller_git_sha,
              "controller_sha256": approval.controller_sha256}
    if result["input_binding"] != wanted:
        raise ValueError("capture approval/request/root binding differs")
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
        namespace = backend.preflight(approval)  # Verified projectNumber + Git/operator/OCI/absence.
        if not isinstance(namespace, str) or not re.fullmatch(r"[0-9]{6,20}", namespace):
            raise ValueError("verified explicit project namespace required")
        prepared["image_resolution"] = validated_image_resolution(backend.image_resolution(), prepared)
        claim(root / "image-resolution.json", prepared["image_resolution"])
        before = backend.protected()
        claim(root / "protected-before.json", before)
        if fingerprint(before) != approval.protected_hash:
            raise ValueError("approved protected state differs")
        claim(root / "intent.json", {"approval_hash": approval_hash, "request": prepared["request"],
                                     "budget": prepared["budget"], "request_sha256": hashlib.sha256(prepared["body"]).hexdigest()})
        created = backend.create(prepared["body"])
        claim(root / "create-response.json", created)
        created_seal = deployed(created, prepared, namespace=namespace)
        job_execution_status(created)
        claim(root / "created-identity.json", created_seal)
        claim(root / "created-inventory.json", execution_inventory(
            backend, prepared, created_seal, namespace=namespace))
        deadline = clock() + 300
        for index in range(31):
            job = backend.job()
            claim(root / f"created-job-{index:02}.json", job)
            seal = deployed(job, prepared, namespace=namespace)
            job_execution_status(job)
            if seal != created_seal:
                raise ValueError("created Job identity/profile changed during readiness")
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
        if backend.protected() != before:
            raise ValueError("deployed/protected profile drift before execute")
        claim(root / "inventory-before-execute.json", execution_inventory(
            backend, prepared, seal, namespace=namespace))
        claim(root / "execute-intent.json", {"job": NAME, "deployed_hash": fingerprint(seal), "body": {}})
        execution = backend.execute()
        claim(root / "original-execution.json", execution)
        execution_id = immutable_execution(execution, prepared, seal, namespace=namespace)
        name = execution_id["name"]
        claim(root / "inventory-after-execute.json", execution_inventory(
            backend, prepared, seal, namespace=namespace, execution=execution_id))
        deadline = clock() + 720
        for index in range(73):
            observed = backend.execution(name)
            claim(root / f"execution-{index:02}.json", observed)
            immutable_execution(observed, prepared, seal, namespace=namespace, identity=execution_id)
            status = observed["status"]
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
    try:
        last_inventory = execution_inventory(backend, prepared, seal, namespace=namespace, execution=execution_id)
        claim(root / "inventory-before-result.json", last_inventory)
        final_protected = backend.protected()
        claim(root / "protected-final.json", final_protected)
        if final_protected != before:
            raise ValueError("protected drift during final inventory; no result")
    except Exception as error:
        claim(root / "failure.json", {"error_type": type(error).__name__, "acceptance": False})
        raise
    completion = {"approval": approval.model_dump(mode="json"), "namespace": namespace,
                  "execution": name, "certificate": certificate,
                  "job": last_inventory["job"], "execution_document": last_inventory["executions"][0],
                  "image_resolution": prepared["image_resolution"],
                  "approval_sha256": approval_hash,
                  "request_sha256": hashlib.sha256(prepared["body"]).hexdigest(),
                  "diagnostic_only": True, "acceptance": False}
    validate_completion(completion)
    claim(root / "result.json", completion)
    return result

def validate_completion(value):
    """Full fixed capture package. Hashes bind bytes, never authenticate a human."""
    permission.keys(value, ("approval", "namespace", "execution", "certificate", "job",
                            "execution_document", "approval_sha256", "request_sha256",
                            "diagnostic_only", "acceptance", "image_resolution"))
    approval = Approval.model_validate(value["approval"])
    if canonical(value["approval"]) != canonical(approval.model_dump(mode="json")):
        raise ValueError("capture approval normalization forbidden")
    if value["approval_sha256"] != fingerprint(value["approval"]):
        raise ValueError("capture approval seal differs")
    prepared = inputs(approval)
    if value["request_sha256"] != hashlib.sha256(prepared["body"]).hexdigest():
        raise ValueError("capture request seal differs")
    prepared["image_resolution"] = validated_image_resolution(value["image_resolution"], prepared)
    namespace = value["namespace"]
    if type(namespace) is not str or not re.fullmatch(r"[0-9]{6,20}", namespace):
        raise ValueError("capture projectNumber required")
    seal = deployed(value["job"], prepared, namespace=namespace)
    document = value["execution_document"]
    identity = immutable_execution(document, prepared, seal, namespace=namespace)
    job_execution_status(value["job"], identity)
    status = document["status"]
    completed = [c for c in status.get("conditions", []) if c.get("type") == "Completed"]
    if (len(completed) != 1 or completed[0].get("status") != "True"
            or status.get("succeededCount") != 1 or type(status.get("succeededCount")) is not int
            or any(status.get(k, 0) for k in ("runningCount", "failedCount", "retriedCount", "cancelledCount"))
            or value["execution"] != identity["name"]
            or value["diagnostic_only"] is not True or value["acceptance"] is not False):
        raise ValueError("sole original successful capture completion required")
    result = validate_result(approval, value["certificate"])
    return deepcopy(result)
