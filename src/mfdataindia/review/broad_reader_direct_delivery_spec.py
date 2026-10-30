"""Provider-free, fixed DEV identities and bounded complete request encoding.

This module cannot read a secret, access a database, create a job, or approve a
grant. Its output is only an exact candidate for later, separately reviewed
create-once transport. The complete payload must fit the old request budget;
there is no truncation or external mutable object fallback.
"""

import base64
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import zlib

from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from mfdataindia.review.evidence_transport import render_evidence
from mfdataindia.review import broad_reader_policy as broad_policy
from mfdataindia.review import broad_reader_direct_grant as direct_grant
from mfdataindia.review import broad_reader_source_authority as source_authority
from mfdataindia.review.policy import canonical


PROJECT = "trillionlane-dev"
PROJECT_NUMBER = "135678656281"
REGION = "asia-south1"
CONNECTION = f"{PROJECT}:{REGION}:tlws-mf-data-dev"
OPERATOR = "trillionlaneassetmanagement@gmail.com"
DATABASE = "mfdataindia"
OWNER_SA = f"tlws-mf-data-migrate-dev@{PROJECT}.iam.gserviceaccount.com"
READER_SA = f"trillion-insights-runtime-dev@{PROJECT}.iam.gserviceaccount.com"
OWNER_ROLE = "mfdata_admin"
READER_ROLE = "mfdata_verified_reader"
MAX_REQUEST_BYTES = 131072
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_ENCODED_BYTES = 100000
PAYLOAD_CHUNK_BYTES = 25000
MAX_PAYLOAD_CHUNKS = 4
IMAGE_PREFIX = (f"{REGION}-docker.pkg.dev/{PROJECT}/tlws-mf-data-dev/"
                "mfdataindia@sha256:")


@dataclass(frozen=True)
class Role:
    rollout_id: str
    job: str
    worker: str
    service_account: str
    secret: str


ROLES = {
    "capture": Role("dev-bread-direct-cap-261029a",
                    "mf-reader-direct-capture-261029a",
                    "scripts/collect_broad_reader_direct_dev.py",
                    OWNER_SA, "tlws-mf-data-admin-dsn-dev"),
    "grant": Role("dev-bread-direct-grant-261029a",
                  "mf-reader-direct-grant-261029a",
                  "scripts/apply_broad_reader_direct_dev.py",
                  OWNER_SA, "tlws-mf-data-admin-dsn-dev"),
    "reader": Role("dev-bread-direct-read-261029a",
                   "mf-reader-direct-check-261029a",
                   "scripts/check_broad_reader_direct_dev.py",
                   READER_SA, "tlws-mf-review-read-dsn-dev"),
}
ARTIFACTS = Path("/Users/deviojha/projects/codex/artifacts")
ROOT_PREFIXES = {"capture": "mf-reader-direct-capture-20261029-",
                 "grant": "mf-reader-direct-grant-20261029-",
                 "reader": "mf-reader-direct-check-20261029-"}

OLD_CONSUMED_JOBS = frozenset({
    "mf-reader-broad-capture-261026r",
    "mf-reader-broad-capture-261027s",
    "mf-reader-broad-capture-261027t",
    "mf-reader-broad-access-261027t",
    "mf-reader-function-identify-261028a",
    "mf-reader-function-attest-261028a",
})


class DeliverySpecError(ValueError):
    """No runtime permission may be inferred from an invalid candidate."""


def require(condition, reason):
    if not condition:
        raise DeliverySpecError(reason)


def sha(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value),
            "EXACT_SHA256_REQUIRED")
    return value


def git_sha(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{40}", value),
            "EXACT_GIT_SHA_REQUIRED")
    return value


def intent_preimage(kind, root, payload):
    """Cycle-free leaf: exact root and all payload bytes except its own hash."""
    require(kind in ROLES and type(root) is str and type(payload) is dict
            and "create_only_intent_sha256" in payload,
            "EXACT_DIRECT_CREATE_INTENT_PREIMAGE_REQUIRED")
    path = Path(root)
    require(path.is_absolute() and path.parent == ARTIFACTS
            and not path.is_symlink() and path.resolve() == path
            and re.fullmatch(re.escape(ROOT_PREFIXES[kind]) +
                             r"[A-Za-z0-9]{6,16}", path.name),
            "EXACT_NEW_DIRECT_EVIDENCE_ROOT_REQUIRED")
    preimage = {
        "format": "mf-broad-reader-direct-create-intent-preimage-v1",
        "kind": kind, "job": ROLES[kind].job, "root": root,
        "payload_without_create_only_intent_sha256": {
            key: value for key, value in payload.items()
            if key != "create_only_intent_sha256"},
    }
    digest = hashlib.sha256(canonical(preimage).encode()).hexdigest()
    return preimage, digest


def require_intent(kind, root, payload):
    preimage, digest = intent_preimage(kind, root, payload)
    require(payload["create_only_intent_sha256"] == digest,
            "DIRECT_CREATE_INTENT_HASH_DRIFT")
    return preimage, digest


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_PAYLOAD_MEMBER")
        result[key] = value
    return result


def encode_payload(payload):
    """Complete canonical payload, never a subset or optimistic size guess."""
    require(type(payload) is dict, "EXACT_PAYLOAD_OBJECT_REQUIRED")
    raw = canonical(payload).encode("utf-8")
    require(0 < len(raw) <= MAX_PAYLOAD_BYTES, "COMPLETE_PAYLOAD_TOO_LARGE")
    body = base64.b64encode(zlib.compress(raw, 9)).decode("ascii")
    require(0 < len(body) <= MAX_ENCODED_BYTES,
            "COMPLETE_PAYLOAD_EXCEEDS_REQUEST_TRANSPORT")
    return {"codec": "zlib-base64", "raw_bytes": len(raw),
            "raw_sha256": hashlib.sha256(raw).hexdigest(), "body": body}


def decode_payload(envelope):
    require(type(envelope) is dict
            and set(envelope) == {"codec", "raw_bytes", "raw_sha256", "body"}
            and envelope["codec"] == "zlib-base64"
            and type(envelope["raw_bytes"]) is int
            and 0 < envelope["raw_bytes"] <= MAX_PAYLOAD_BYTES
            and type(envelope["body"]) is str
            and 0 < len(envelope["body"]) <= MAX_ENCODED_BYTES,
            "EXACT_BOUNDED_PAYLOAD_ENVELOPE_REQUIRED")
    sha(envelope["raw_sha256"])
    try:
        compressed = base64.b64decode(envelope["body"], validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, MAX_PAYLOAD_BYTES + 1)
        require(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail
                and len(raw) == envelope["raw_bytes"]
                and hashlib.sha256(raw).hexdigest() == envelope["raw_sha256"],
                "INCOMPLETE_PAYLOAD_BYTES")
        value = json.loads(raw, object_pairs_hook=_unique)
    except (ValueError, UnicodeError, zlib.error, RecursionError):
        raise DeliverySpecError("INVALID_COMPLETE_PAYLOAD") from None
    require(type(value) is dict and canonical(value).encode("utf-8") == raw,
            "NONCANONICAL_COMPLETE_PAYLOAD")
    return value


def payload_variables(envelope):
    """Four fixed sub-32-KB values, never one oversized Cloud Run variable."""
    decode_payload(envelope)
    serialized = canonical(envelope)
    require(serialized.isascii() and 0 < len(serialized) <=
            PAYLOAD_CHUNK_BYTES * MAX_PAYLOAD_CHUNKS,
            "COMPLETE_ENVELOPE_EXCEEDS_FIXED_ENV_TRANSPORT")
    chunks = [serialized[i:i + PAYLOAD_CHUNK_BYTES]
              for i in range(0, len(serialized), PAYLOAD_CHUNK_BYTES)]
    return {"BROAD_DIRECT_PAYLOAD_COUNT": str(len(chunks)),
            "BROAD_DIRECT_PAYLOAD_SHA256": hashlib.sha256(serialized.encode()).hexdigest(),
            **{f"BROAD_DIRECT_PAYLOAD_{i}": part
               for i, part in enumerate(chunks)}}


def payload_from_variables(environment):
    """Reassemble exact contiguous bytes before JSON or secret use."""
    require(type(environment) is dict
            and all(type(key) is str for key in environment),
            "EXACT_BOUNDED_PAYLOAD_VARIABLES_REQUIRED")
    count = environment.get("BROAD_DIRECT_PAYLOAD_COUNT")
    require(type(count) is str and count in {"1", "2", "3", "4"},
            "EXACT_BOUNDED_PAYLOAD_VARIABLES_REQUIRED")
    n = int(count)
    names = {"BROAD_DIRECT_PAYLOAD_COUNT", "BROAD_DIRECT_PAYLOAD_SHA256"} | {
        f"BROAD_DIRECT_PAYLOAD_{i}" for i in range(n)}
    present = {name for name in environment if name.startswith("BROAD_DIRECT_PAYLOAD")}
    require(present == names, "EXACT_BOUNDED_PAYLOAD_VARIABLES_REQUIRED")
    parts = [environment[f"BROAD_DIRECT_PAYLOAD_{i}"] for i in range(n)]
    require(all(type(part) is str and part.isascii()
                and len(part) == (PAYLOAD_CHUNK_BYTES if i < n - 1 else len(part))
                and 0 < len(part) <= PAYLOAD_CHUNK_BYTES
                for i, part in enumerate(parts)),
            "EXACT_BOUNDED_PAYLOAD_VARIABLES_REQUIRED")
    serialized = "".join(parts)
    require(0 < len(serialized) <= PAYLOAD_CHUNK_BYTES * MAX_PAYLOAD_CHUNKS
            and sha(environment["BROAD_DIRECT_PAYLOAD_SHA256"])
            == hashlib.sha256(serialized.encode()).hexdigest(),
            "COMPLETE_PAYLOAD_VARIABLE_HASH_DRIFT")
    try:
        envelope = json.loads(serialized, object_pairs_hook=_unique)
    except (ValueError, UnicodeError, RecursionError):
        raise DeliverySpecError("INVALID_COMPLETE_PAYLOAD") from None
    require(canonical(envelope) == serialized,
            "NONCANONICAL_COMPLETE_PAYLOAD_ENVELOPE")
    return decode_payload(envelope)


def image_binding(image, *, expected_git_sha, repository="mfdataindia"):
    git_sha(expected_git_sha)
    require(repository in {"mfdataindia", "mfdataindia-review-tools"},
            "EXACT_IMAGE_REPOSITORY_REQUIRED")
    prefix = (f"{REGION}-docker.pkg.dev/{PROJECT}/tlws-mf-data-dev/"
              f"{repository}@sha256:")
    require(type(image) is dict and set(image) == {
        "image", "resolved_image", "config_digest", "git_sha", "platform",
        "layers_downloaded"}, "EXACT_IMAGE_BINDING_REQUIRED")
    require(all(type(image[key]) is str
                and re.fullmatch(re.escape(prefix) + r"[0-9a-f]{64}", image[key])
                for key in ("image", "resolved_image"))
            and type(image["config_digest"]) is str
            and re.fullmatch(r"sha256:[0-9a-f]{64}", image["config_digest"])
            and image["git_sha"] == expected_git_sha
            and image["platform"] == "linux/amd64"
            and type(image["layers_downloaded"]) is int
            and image["layers_downloaded"] == 0,
            "EXACT_FINAL_IMAGE_REQUIRED")
    return image["image"]


def validate_capture_payload(payload, *, final_git_sha, source_root):
    """Attest complete fixed capture input before owner secret or connection I/O.

    This is a read-only candidate. The window and image seals do not themselves
    authorize a job, a database read, or a later permission write.
    """
    require(type(payload) is dict and set(payload) == {
        "format", "packaged_source", "git_sha", "image_binding",
        "create_only_intent_sha256", "window"},
        "EXACT_DIRECT_CAPTURE_PAYLOAD_REQUIRED")
    require(payload["format"] == "mf-broad-reader-direct-capture-v1"
            and payload["git_sha"] == git_sha(final_git_sha),
            "EXACT_DIRECT_CAPTURE_PAYLOAD_REQUIRED")
    sha(payload["create_only_intent_sha256"])
    images = payload["image_binding"]
    require(type(images) is dict and set(images) == {"app", "tools"},
            "EXACT_DIRECT_CAPTURE_IMAGES_REQUIRED")
    image_binding(images["app"], expected_git_sha=final_git_sha)
    image_binding(images["tools"], expected_git_sha=final_git_sha,
                  repository="mfdataindia-review-tools")
    direct_grant.direct_window(payload["window"])
    source_authority.require_packaged_source(payload["packaged_source"], source_root)
    return deepcopy(payload)


def validate_grant_payload(payload, *, final_git_sha, source_root):
    """Consume a disposable copy of the frozen Permit, without owner I/O.

    The real single-use Permit remains unconsumed until the bounded worker
    enters direct_grant.run; this precheck only rejects impossible requests
    before even parsing the versioned owner DSN.
    """
    require(type(payload) is dict, "EXACT_DIRECT_GRANT_PAYLOAD_REQUIRED")
    candidate = direct_grant.Permit(
        deepcopy(payload), broad_policy.fingerprint(payload))
    checked = candidate.consume(source_root=source_root)
    require(candidate.used and checked["git_sha"] == git_sha(final_git_sha),
            "EXACT_DIRECT_GRANT_PAYLOAD_REQUIRED")
    return checked


def validate_reader_payload(payload, *, final_git_sha, source_root):
    """Bind a read-only postgrant check to one completed grant receipt."""
    require(type(payload) is dict, "EXACT_DIRECT_READER_PAYLOAD_REQUIRED")
    marker = direct_grant.custody_marker(payload.get("window"))
    require(set(payload) == ({
        "format", "planned_after", "grant_receipt_sha256", "packaged_source",
        "git_sha", "image_binding", "create_only_intent_sha256", "window"}
        | ({"grant_custody"} if marker is not None else set())),
        "EXACT_DIRECT_READER_PAYLOAD_REQUIRED")
    if marker is not None:
        require(payload["grant_custody"] == marker,
                "UNMATCHED_DIRECT_GRANT_CUSTODY_MODE")
    require(payload["format"] == "mf-broad-reader-direct-reader-v1"
            and payload["git_sha"] == git_sha(final_git_sha)
            and type(payload["planned_after"]) is dict,
            "EXACT_DIRECT_READER_PAYLOAD_REQUIRED")
    sha(payload["grant_receipt_sha256"])
    sha(payload["create_only_intent_sha256"])
    images = payload["image_binding"]
    require(type(images) is dict and set(images) == {"app", "tools"},
            "EXACT_DIRECT_READER_IMAGES_REQUIRED")
    image_binding(images["app"], expected_git_sha=final_git_sha)
    image_binding(images["tools"], expected_git_sha=final_git_sha,
                  repository="mfdataindia-review-tools")
    direct_grant.direct_window(payload["window"])
    source_authority.require_packaged_source(payload["packaged_source"], source_root)
    broad_policy.require_current_policy(payload["planned_after"])
    return deepcopy(payload)


def request(kind, *, root, final_git_sha, image, payload, worker_sha256,
            controller_sha256, spec_sha256, source_sha256):
    """Build one complete fixed job candidate; provider I/O is elsewhere."""
    require(kind in ROLES, "UNKNOWN_DIRECT_DELIVERY_JOB")
    role = ROLES[kind]
    require(role.job not in OLD_CONSUMED_JOBS
            and len({item.job for item in ROLES.values()}) == len(ROLES),
            "CONSUMED_OR_ALIASED_JOB_IDENTITY")
    git_sha(final_git_sha)
    require_intent(kind, root, payload)
    image_ref = image_binding(image, expected_git_sha=final_git_sha)
    hashes = {"BROAD_DIRECT_WORKER_SHA256": sha(worker_sha256),
              "BROAD_DIRECT_CONTROLLER_SHA256": sha(controller_sha256),
              "BROAD_DIRECT_SPEC_SHA256": sha(spec_sha256),
              "BROAD_DIRECT_SOURCE_SHA256": sha(source_sha256)}
    encoded = encode_payload(payload)
    environment = {"DEPLOYMENT_GIT_SHA": final_git_sha,
                   "BROAD_DIRECT_ROLE": kind,
                   "BROAD_DIRECT_ROOT": root,
                   **payload_variables(encoded), **hashes}
    env = [{"name": name, "value": value} for name, value in sorted(environment.items())]
    env.append({"name": "MFDATAINDIA_DSN", "valueFrom": {
        "secretKeyRef": {"name": role.secret, "key": "1"}}})
    annotations = {
        "run.googleapis.com/cloudsql-instances": CONNECTION,
        "run.googleapis.com/vpc-access-egress": "all-traffic",
        "run.googleapis.com/network-interfaces": canonical([
            {"network": "tl-dev-vpc", "subnetwork": "tl-dev-mumbai"}]),
        "run.googleapis.com/execution-environment": "gen2",
    }
    document = {
        "apiVersion": "run.googleapis.com/v1", "kind": "Job",
        "metadata": {"name": role.job, "namespace": PROJECT},
        "spec": {"template": {"metadata": {"annotations": annotations},
                             "spec": {"taskCount": 1, "parallelism": 1,
                                      "template": {"spec": {
                                          "serviceAccountName": role.service_account,
                                          "timeoutSeconds": 600, "maxRetries": 0,
                                          "containers": [{
                                              "image": image_ref,
                                              "command": ["python"],
                                              "args": [role.worker],
                                              "env": env,
                                              "resources": {"limits": {
                                                  "cpu": "1", "memory": "1Gi"}},
                                          }],
                                      }}}}},
    }
    raw = canonical(document).encode("utf-8")
    require(len(raw) <= MAX_REQUEST_BYTES, "COMPLETE_REQUEST_TOO_LARGE")
    return {"request": document, "body": raw,
            "request_sha256": hashlib.sha256(raw).hexdigest(),
            "payload_sha256": encoded["raw_sha256"],
            "request_bytes": len(raw),
            "task": document["spec"]["template"]["spec"]["template"]["spec"],
            "annotations": annotations}


def worker_environment(kind, environment, *, final_git_sha, worker_sha256,
                       controller_sha256, spec_sha256, source_sha256):
    """Validate the entire fixed injected environment before reading its DSN.

    The Cloud Run variables are service metadata, not authorization. The
    controller must still reconcile the actual job and sole execution.
    """
    require(kind in ROLES and type(environment) is dict,
            "UNKNOWN_DIRECT_DELIVERY_JOB")
    expected = {"DEPLOYMENT_GIT_SHA": git_sha(final_git_sha),
                "BROAD_DIRECT_ROLE": kind,
                "BROAD_DIRECT_ROOT": environment.get("BROAD_DIRECT_ROOT"),
                "BROAD_DIRECT_WORKER_SHA256": sha(worker_sha256),
                "BROAD_DIRECT_CONTROLLER_SHA256": sha(controller_sha256),
                "BROAD_DIRECT_SPEC_SHA256": sha(spec_sha256),
                "BROAD_DIRECT_SOURCE_SHA256": sha(source_sha256)}
    require(all(type(key) is str for key in environment),
            "EXACT_DIRECT_WORKER_ENVIRONMENT_REQUIRED")
    count = environment.get("BROAD_DIRECT_PAYLOAD_COUNT")
    require(type(count) is str and count in {"1", "2", "3", "4"},
            "EXACT_DIRECT_WORKER_ENVIRONMENT_REQUIRED")
    payload_names = {"BROAD_DIRECT_PAYLOAD_COUNT", "BROAD_DIRECT_PAYLOAD_SHA256"} | {
        f"BROAD_DIRECT_PAYLOAD_{i}" for i in range(int(count))}
    configured = {key for key in environment
                  if key.startswith("BROAD_DIRECT_") or key in (
                      "DEPLOYMENT_GIT_SHA", "MFDATAINDIA_DSN")}
    require(configured == set(expected) | payload_names | {"MFDATAINDIA_DSN"}
            and not any(key.startswith("PG") for key in environment)
            and all(environment.get(key) == value for key, value in expected.items())
            and environment.get("CLOUD_RUN_JOB") == ROLES[kind].job
            and type(environment.get("CLOUD_RUN_EXECUTION")) is str
            and re.fullmatch(re.escape(ROLES[kind].job) + r"-[a-z0-9]{5,10}",
                             environment["CLOUD_RUN_EXECUTION"])
            and environment.get("CLOUD_RUN_TASK_INDEX") == "0"
            and environment.get("CLOUD_RUN_TASK_ATTEMPT") == "0"
            and environment.get("CLOUD_RUN_TASK_COUNT") == "1"
            and type(environment.get("MFDATAINDIA_DSN")) is str
            and 0 < len(environment["MFDATAINDIA_DSN"].encode()) <= 32768,
            "EXACT_DIRECT_WORKER_ENVIRONMENT_REQUIRED")
    payload = payload_from_variables(environment)
    require_intent(kind, environment["BROAD_DIRECT_ROOT"], payload)
    return payload


def connection_arguments(kind, dsn):
    """Parse only the fixed versioned DEV secret value, without connecting.

    The caller must never log the input or returned password, and must close
    the one connection (two for the grant's target and observer) without SQL
    cleanup after an ambiguous commit.
    """
    require(kind in ROLES and type(dsn) is str and "\0" not in dsn
            and 0 < len(dsn.encode()) <= 32768,
            "EXACT_VERSIONED_DEV_DSN_REQUIRED")
    try:
        parsed = conninfo_to_dict(dsn)
    except (ValueError, TypeError):
        raise DeliverySpecError("INVALID_DEV_DSN") from None
    principal = READER_ROLE if kind == "reader" else OWNER_ROLE
    require(set(parsed) <= {"host", "dbname", "user", "password", "port", "sslmode"}
            and parsed.get("host") == "/cloudsql/" + CONNECTION
            and parsed.get("dbname") == DATABASE
            and parsed.get("user") == principal
            and type(parsed.get("password")) is str
            and bool(parsed["password"])
            and parsed.get("port", "5432") == "5432"
            and parsed.get("sslmode", "disable") == "disable",
            "EXACT_VERSIONED_DEV_DSN_REQUIRED")
    return {"host": "/cloudsql/" + CONNECTION, "dbname": DATABASE,
            "user": principal, "password": parsed["password"],
            "port": "5432", "sslmode": "disable", "connect_timeout": 5,
            "autocommit": True, "prepare_threshold": None,
            "row_factory": dict_row}


def validate_task(actual, wanted):
    """Reject extra task capabilities and compare every authored container value."""
    require(type(actual) is dict and type(wanted) is dict
            and set(actual) <= {"containers", "maxRetries", "serviceAccountName",
                                "timeoutSeconds", "volumes"}
            and actual.get("serviceAccountName") == wanted["serviceAccountName"]
            and actual.get("volumes", []) == []
            and type(actual.get("maxRetries")) is int
            and actual["maxRetries"] == 0
            and str(actual.get("timeoutSeconds")) == "600"
            and type(actual.get("containers")) is list
            and len(actual["containers"]) == 1,
            "EXACT_DIRECT_TASK_REQUIRED")
    container = actual["containers"][0]
    expected = wanted["containers"][0]
    require(type(container) is dict and set(container) == set(expected)
            and all(container[key] == expected[key] for key in ("image", "command", "args"))
            and container.get("resources") in (
                {"limits": {"cpu": "1", "memory": "1Gi"}},
                {"limits": {"cpu": "1000m", "memory": "1Gi"}}),
            "EXACT_DIRECT_CONTAINER_REQUIRED")
    rows = container["env"]
    required = expected["env"]
    require(type(rows) is list and len(rows) == len(required)
            and all(type(row) is dict and set(row) in (
                {"name", "value"}, {"name", "valueFrom"})
                for row in rows)
            and len({row["name"] for row in rows}) == len(rows)
            and {row["name"]: row for row in rows}
            == {row["name"]: row for row in required},
            "EXACT_DIRECT_ENV_AND_SECRET_REFERENCE_REQUIRED")


def _timestamp(value):
    require(type(value) is str and re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z", value),
        "PROVIDER_UTC_TIMESTAMP_REQUIRED")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise DeliverySpecError("PROVIDER_UTC_TIMESTAMP_REQUIRED") from None
    return value


def validate_job_export(kind, actual, prepared, *, namespace,
                        expected_executions=0):
    """Full first-generation created JobSpec, not a name-only comparison."""
    require(kind in ROLES and type(actual) is dict
            and set(actual) <= {"apiVersion", "kind", "metadata", "spec", "status"}
            and actual.get("apiVersion") == "run.googleapis.com/v1"
            and actual.get("kind") == "Job"
            and namespace == PROJECT_NUMBER,
            "EXACT_DIRECT_JOB_EXPORT_REQUIRED")
    metadata = actual.get("metadata")
    require(type(metadata) is dict and set(metadata) <= {
        "name", "namespace", "uid", "creationTimestamp", "generation",
        "resourceVersion", "selfLink", "labels", "annotations"}
        and metadata.get("name") == ROLES[kind].job
        and metadata.get("namespace") in (namespace, PROJECT)
        and type(metadata.get("uid")) is str
        and re.fullmatch(r"[a-z0-9][a-z0-9-]{15,127}", metadata["uid"])
        and type(metadata.get("generation")) in (str, int)
        and str(metadata["generation"]) == "1",
        "EXACT_DIRECT_JOB_IDENTITY_REQUIRED")
    _timestamp(metadata.get("creationTimestamp"))
    if "selfLink" in metadata:
        require(metadata["selfLink"] == (
            f"/apis/run.googleapis.com/v1/namespaces/{metadata['namespace']}"
            f"/jobs/{ROLES[kind].job}"), "DIRECT_JOB_LINK_DRIFT")
    labels = metadata.get("labels", {})
    require(type(labels) is dict
            and labels.get("cloud.googleapis.com/location") == REGION
            and set(labels) <= {"cloud.googleapis.com/location",
                                "run.googleapis.com/satisfiesPzs",
                                "run.googleapis.com/lastUpdatedTime"},
            "DIRECT_JOB_LABEL_DRIFT")
    annotations = metadata.get("annotations", {})
    require(type(annotations) is dict
            and annotations.get("run.googleapis.com/creator") == OPERATOR
            and set(annotations) <= {"run.googleapis.com/creator",
                                    "run.googleapis.com/lastModifier",
                                    "run.googleapis.com/operation-id",
                                    "run.googleapis.com/client-name",
                                    "run.googleapis.com/client-version"}
            and annotations.get("run.googleapis.com/lastModifier", OPERATOR) == OPERATOR,
            "DIRECT_JOB_CREATOR_DRIFT")
    template = actual.get("spec", {}).get("template")
    require(type(template) is dict and set(template) == {"metadata", "spec"},
            "EXACT_DIRECT_JOB_TEMPLATE_REQUIRED")
    template_metadata = template["metadata"]
    require(type(template_metadata) is dict
            and set(template_metadata) <= {"annotations", "labels"}
            and "annotations" in template_metadata,
            "EXACT_DIRECT_TEMPLATE_METADATA_REQUIRED")
    actual_annotations = template_metadata["annotations"]
    require(type(actual_annotations) is dict,
            "EXACT_DIRECT_NETWORK_AND_TEMPLATE_REQUIRED")
    informational = {key: value for key, value in actual_annotations.items()
                     if key in {"run.googleapis.com/client-name",
                                "run.googleapis.com/client-version"}}
    require(all(type(value) is str and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", value)
                    for value in informational.values())
            and actual_annotations == {**prepared["annotations"], **informational}
            and set(template_metadata.get("labels", {})) <= {"client.knative.dev/nonce"},
            "EXACT_DIRECT_NETWORK_AND_TEMPLATE_REQUIRED")
    spec = template["spec"]
    require(type(spec) is dict and set(spec) == {"taskCount", "parallelism", "template"}
            and all(type(spec[key]) is int and spec[key] == 1
                    for key in ("taskCount", "parallelism"))
            and type(spec["template"]) is dict
            and set(spec["template"]) == {"spec"},
            "EXACT_DIRECT_SINGLE_TASK_REQUIRED")
    validate_task(spec["template"]["spec"], prepared["task"])
    status = actual.get("status", {})
    require(type(expected_executions) is int and expected_executions in (0, 1)
            and type(status) is dict
            and type(status.get("executionCount", 0)) is int
            and status.get("executionCount", 0) == expected_executions
            and (expected_executions == 1 or
                 status.get("latestCreatedExecution") in (None, {})),
            "DIRECT_JOB_NOT_PRISTINE")
    return {"job_uid": metadata["uid"],
            "job_created_utc": metadata["creationTimestamp"],
            "full_job_spec_sha256": hashlib.sha256(canonical(actual["spec"]).encode()).hexdigest(),
            "task_sha256": hashlib.sha256(canonical(spec["template"]["spec"]).encode()).hexdigest(),
            "whole_spec": deepcopy(spec),
            "provider_annotations": deepcopy(annotations),
            "provider_labels": deepcopy(labels)}


def seal_certificate(*, payload_sha256, stage, evidence):
    """Inner complete self-seal before the old bounded lossless log transport."""
    sha(payload_sha256)
    require(stage in {"direct-capture-readonly", "direct-grant-transaction",
                      "direct-authenticated-reader"}
            and type(evidence) is dict, "EXACT_DIRECT_CERTIFICATE_REQUIRED")
    body = {"format": "mfdata-review-rollout-evidence-v1",
            "manifest_hash": payload_sha256, "stage": stage,
            "evidence": evidence}
    document = {**body,
                "sha256": hashlib.sha256(canonical(body).encode()).hexdigest()}
    # Capacity check is complete and deterministic; never truncate.
    render_evidence(document)
    return document


def verify_certificate(document, *, payload_sha256, stage):
    sha(payload_sha256)
    require(type(document) is dict and set(document) == {
        "format", "manifest_hash", "stage", "evidence", "sha256"}
        and document["format"] == "mfdata-review-rollout-evidence-v1"
        and document["manifest_hash"] == payload_sha256
        and document["stage"] == stage
        and type(document["evidence"]) is dict,
        "EXACT_DIRECT_CERTIFICATE_REQUIRED")
    body = {key: value for key, value in document.items() if key != "sha256"}
    require(document["sha256"] == hashlib.sha256(canonical(body).encode()).hexdigest(),
            "DIRECT_CERTIFICATE_HASH_DRIFT")
    render_evidence(document)
    return document["evidence"]


def validate_execution_export(kind, actual, prepared, *, job, image,
                              original_identity=None):
    """Bind one Execution's immutable UID, parent and complete task profile."""
    require(kind in ROLES and type(actual) is dict
            and set(actual) <= {"apiVersion", "kind", "metadata", "spec", "status"}
            and actual.get("apiVersion") == "run.googleapis.com/v1"
            and actual.get("kind") == "Execution", "EXACT_DIRECT_EXECUTION_REQUIRED")
    image_binding(image, expected_git_sha=_git_from_prepared(prepared))
    metadata = actual.get("metadata")
    require(type(metadata) is dict
            and set(metadata) <= {"name", "namespace", "uid", "creationTimestamp",
                                  "generation", "resourceVersion", "selfLink", "labels",
                                  "annotations", "ownerReferences"}
            and type(metadata.get("name")) is str
            and re.fullmatch(re.escape(ROLES[kind].job) + r"-[a-z0-9]{5,10}",
                             metadata["name"])
            and metadata.get("namespace") in (PROJECT, PROJECT_NUMBER)
            and type(metadata.get("uid")) is str
            and re.fullmatch(r"[a-z0-9][a-z0-9-]{15,127}", metadata["uid"])
            and metadata["uid"] != job["job_uid"]
            and type(metadata.get("generation")) in (str, int)
            and str(metadata["generation"]) == "1",
            "EXACT_DIRECT_EXECUTION_IDENTITY_REQUIRED")
    _timestamp(metadata.get("creationTimestamp"))
    expected_owner = {"apiVersion": "serving.knative.dev/v1", "kind": "Job",
                      "name": ROLES[kind].job, "uid": job["job_uid"],
                      "controller": True, "blockOwnerDeletion": True}
    owners = metadata.get("ownerReferences", [])
    require(owners in ([], [expected_owner]), "DIRECT_EXECUTION_PARENT_DRIFT")
    labels = metadata.get("labels", {})
    require(type(labels) is dict
            and labels.get("cloud.googleapis.com/location") == REGION
            and labels.get("run.googleapis.com/job") == ROLES[kind].job
            and labels.get("run.googleapis.com/jobUid") == job["job_uid"]
            and labels.get("run.googleapis.com/jobGeneration") == "1"
            and set(labels) <= {"cloud.googleapis.com/location", "run.googleapis.com/job",
                                "run.googleapis.com/jobUid", "run.googleapis.com/jobGeneration",
                                "run.googleapis.com/satisfiesPzs",
                                "run.googleapis.com/jobResourceVersion",
                                "client.knative.dev/nonce"},
            "DIRECT_EXECUTION_PARENT_LABEL_DRIFT")
    annotations = metadata.get("annotations", {})
    require(type(annotations) is dict
            and annotations.get("run.googleapis.com/creator") == OPERATOR
            and set(annotations) <= set(prepared["annotations"]) | {
                "run.googleapis.com/creator", "run.googleapis.com/lastModifier",
                "run.googleapis.com/operation-id", "run.googleapis.com/client-name",
                "run.googleapis.com/client-version"}
            and all(annotations.get(key) == value for key, value
                    in prepared["annotations"].items()),
            "DIRECT_EXECUTION_ANNOTATION_DRIFT")
    if "selfLink" in metadata:
        require(metadata["selfLink"] == (
            f"/apis/run.googleapis.com/v1/namespaces/{metadata['namespace']}"
            f"/executions/{metadata['name']}"), "DIRECT_EXECUTION_LINK_DRIFT")
    comparable = actual.get("spec")
    expected = job["whole_spec"]
    require(type(comparable) is dict and set(comparable) == set(expected),
            "EXACT_DIRECT_EXECUTION_SPEC_REQUIRED")
    comparable = json.loads(canonical(comparable))
    container = comparable["template"]["spec"]["containers"][0]
    if container["image"] == image["resolved_image"]:
        container["image"] = image["image"]
    require(comparable == expected, "EXACT_DIRECT_EXECUTION_SPEC_REQUIRED")
    identity = {key: metadata[key] for key in (
        "name", "namespace", "uid", "creationTimestamp", "generation")}
    identity.update({"labels": deepcopy(labels),
                     "annotations": deepcopy(annotations),
                     "ownerReferences": deepcopy(owners)})
    if original_identity is not None:
        require(identity == original_identity, "DIRECT_EXECUTION_IDENTITY_DRIFT")
    return identity


def _git_from_prepared(prepared):
    env = prepared["task"]["containers"][0]["env"]
    values = [row["value"] for row in env if row["name"] == "DEPLOYMENT_GIT_SHA"]
    require(len(values) == 1, "EXACT_GIT_SHA_REQUIRED")
    return values[0]
