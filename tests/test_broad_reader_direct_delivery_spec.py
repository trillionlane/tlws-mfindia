"""Provider-free exact request and payload boundary tests."""

from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from mfdataindia.review import broad_reader_direct_delivery_spec as spec


GIT = "a" * 40
HASH = "b" * 64
IMAGE = {
    "image": spec.IMAGE_PREFIX + "c" * 64,
    "resolved_image": spec.IMAGE_PREFIX + "c" * 64,
    "config_digest": "sha256:" + "d" * 64,
    "git_sha": GIT,
    "platform": "linux/amd64",
    "layers_downloaded": 0,
}


def _request(kind="capture", payload=None, image=None):
    root = str(spec.ARTIFACTS / (spec.ROOT_PREFIXES.get(kind, "unknown-") + "abcdef"))
    value = {"format": "synthetic-provider-free"} if payload is None else deepcopy(payload)
    value["create_only_intent_sha256"] = "0" * 64
    if kind in spec.ROLES:
        value["create_only_intent_sha256"] = spec.intent_preimage(kind, root, value)[1]
    return spec.request(kind, root=root, final_git_sha=GIT,
                        image=IMAGE if image is None else image, payload=value,
                        worker_sha256=HASH, controller_sha256=HASH,
                        spec_sha256=HASH, source_sha256=HASH)


@pytest.mark.parametrize("kind", ("capture", "grant", "reader"))
def test_exact_three_distinct_jobs_and_role_secrets(kind):
    result = _request(kind)
    document = result["request"]
    role = spec.ROLES[kind]
    assert document["metadata"] == {"name": role.job, "namespace": spec.PROJECT}
    assert role.job not in spec.OLD_CONSUMED_JOBS
    template = document["spec"]["template"]["spec"]
    assert template["taskCount"] == template["parallelism"] == 1
    task = template["template"]["spec"]
    assert task["serviceAccountName"] == role.service_account
    assert task["maxRetries"] == 0 and task["timeoutSeconds"] == 600
    container = task["containers"][0]
    assert container["image"] == IMAGE["image"]
    assert container["command"] == ["python"] and container["args"] == [role.worker]
    assert container["resources"] == {"limits": {"cpu": "1", "memory": "1Gi"}}
    assert container["env"][-1] == {"name": "MFDATAINDIA_DSN", "valueFrom": {
        "secretKeyRef": {"name": role.secret, "key": "1"}}}
    assert result["body"] == spec.canonical(document).encode()
    assert result["request_sha256"] == hashlib.sha256(result["body"]).hexdigest()
    assert result["request_bytes"] == len(result["body"]) <= spec.MAX_REQUEST_BYTES


def test_payload_roundtrip_is_complete_and_canonical():
    payload = {"z": list(range(1000)), "a": {"nested": [True, None, "x"]}}
    envelope = spec.encode_payload(payload)
    assert spec.decode_payload(envelope) == payload
    assert envelope["raw_sha256"] == hashlib.sha256(spec.canonical(payload).encode()).hexdigest()
    request = _request(payload=payload)["request"]
    env = request["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]["env"]
    values = {item["name"]: item["value"] for item in env if "value" in item}
    assert {k: v for k, v in spec.payload_from_variables(values).items()
            if k != "create_only_intent_sha256"} == payload
    assert "BROAD_DIRECT_PAYLOAD" not in values
    assert all(len(value.encode()) <= spec.PAYLOAD_CHUNK_BYTES
               for key, value in values.items()
               if key.startswith("BROAD_DIRECT_PAYLOAD_") and key[-1].isdigit())


def test_platform_resolved_manifest_may_differ_from_index_digest():
    image = deepcopy(IMAGE)
    image["resolved_image"] = spec.IMAGE_PREFIX + "e" * 64
    job = _request(image=image)["request"]
    task = job["spec"]["template"]["spec"]["template"]["spec"]
    assert task["containers"][0]["image"] == image["image"]


@pytest.mark.parametrize("mutation", (
    lambda value: value.update(git_sha="0" * 40),
    lambda value: value.update(platform="linux/arm64"),
    lambda value: value.update(layers_downloaded=1),
    lambda value: value.update(resolved_image="other.example.invalid/repo@sha256:" + "e" * 64),
    lambda value: value.update(config_digest="sha256:bad"),
    lambda value: value.update(secret="owner-dsn"),
))
def test_image_or_extra_binding_drift_rejected(mutation):
    image = deepcopy(IMAGE)
    mutation(image)
    with pytest.raises(spec.DeliverySpecError):
        _request(image=image)


def test_payload_duplicate_noncanonical_and_hash_mismatch_rejected():
    envelope = spec.encode_payload({"a": 1})
    bad = deepcopy(envelope)
    bad["raw_sha256"] = "0" * 64
    with pytest.raises(spec.DeliverySpecError):
        spec.decode_payload(bad)
    bad = deepcopy(envelope)
    bad["body"] += "!"
    with pytest.raises(spec.DeliverySpecError):
        spec.decode_payload(bad)


def test_no_truncation_for_unreportable_payload_or_request():
    # High-entropy-like deterministic content defeats the fixed compressed env
    # bound and must fail before a request or provider object exists.
    payload = {"rows": [hashlib.sha256(str(i).encode()).hexdigest()
                        for i in range(10000)]}
    with pytest.raises(spec.DeliverySpecError, match="COMPLETE_PAYLOAD_EXCEEDS_REQUEST_TRANSPORT"):
        _request(payload=payload)


@pytest.mark.parametrize("kind", ("capture", "grant", "reader"))
def test_complete_payload_is_split_into_fixed_contiguous_variables(kind):
    payload = {"rows": [hashlib.sha256(str(i).encode()).hexdigest()
                        for i in range(200)]}
    prepared = _request(kind, payload=payload)
    env = prepared["task"]["containers"][0]["env"]
    values = {row["name"]: row["value"] for row in env if "value" in row}
    assert {k: v for k, v in spec.payload_from_variables(values).items()
            if k != "create_only_intent_sha256"} == payload
    assert values["BROAD_DIRECT_PAYLOAD_COUNT"] in {"1", "2", "3", "4"}
    n = int(values["BROAD_DIRECT_PAYLOAD_COUNT"])
    prefix = "BROAD_DIRECT_PAYLOAD_"
    assert [k for k in values if k.startswith(prefix)
            and k[len(prefix):].isdigit()] == [f"{prefix}{i}" for i in range(n)]
    assert all(len(values[f"BROAD_DIRECT_PAYLOAD_{i}"].encode()) <= 25000
               for i in range(n))


@pytest.mark.parametrize("mutation", (
    lambda value: value.pop("BROAD_DIRECT_PAYLOAD_0"),
    lambda value: value.update(BROAD_DIRECT_PAYLOAD_COUNT="01"),
    lambda value: value.update(BROAD_DIRECT_PAYLOAD_SHA256="0" * 64),
    lambda value: value.update(BROAD_DIRECT_PAYLOAD_0=value["BROAD_DIRECT_PAYLOAD_0"] + "x"),
    lambda value: value.update(BROAD_DIRECT_PAYLOAD_1="extra"),
    lambda value: value.update(BROAD_DIRECT_PAYLOAD="legacy-single-variable"),
))
def test_missing_extra_or_mutated_payload_variable_fails_closed(mutation):
    values = spec.payload_variables(spec.encode_payload({"x": [1, 2, 3]}))
    mutation(values)
    with pytest.raises(spec.DeliverySpecError):
        spec.payload_from_variables(values)


def test_four_full_contiguous_cloud_run_values_and_order_drift():
    payload = {"rows": [hashlib.sha256(str(i).encode()).hexdigest()
                        for i in range(1500)]}
    envelope = spec.encode_payload(payload)
    values = spec.payload_variables(envelope)
    assert values["BROAD_DIRECT_PAYLOAD_COUNT"] == "4"
    assert spec.payload_from_variables(values) == payload
    assert all(len(values[f"BROAD_DIRECT_PAYLOAD_{i}"]) == 25000
               for i in range(3))
    swapped = deepcopy(values)
    swapped["BROAD_DIRECT_PAYLOAD_0"], swapped["BROAD_DIRECT_PAYLOAD_1"] = (
        swapped["BROAD_DIRECT_PAYLOAD_1"], swapped["BROAD_DIRECT_PAYLOAD_0"])
    with pytest.raises(spec.DeliverySpecError):
        spec.payload_from_variables(swapped)
    for key, changed in (("BROAD_DIRECT_PAYLOAD_3", ""),
                         ("BROAD_DIRECT_PAYLOAD_3", "é"),
                         ("BROAD_DIRECT_PAYLOAD_COUNT", "3")):
        bad = {**values, key: changed}
        with pytest.raises(spec.DeliverySpecError):
            spec.payload_from_variables(bad)


def test_no_unknown_job_kind_or_old_alias():
    with pytest.raises(spec.DeliverySpecError, match="UNKNOWN_DIRECT_DELIVERY_JOB"):
        _request("mf-reader-broad-access-261027t")
    original = spec.ROLES["grant"]
    try:
        spec.ROLES["grant"] = spec.Role(original.rollout_id,
                                         "mf-reader-broad-access-261027t",
                                         original.worker, original.service_account,
                                         original.secret)
        with pytest.raises(spec.DeliverySpecError, match="CONSUMED_OR_ALIASED_JOB_IDENTITY"):
            _request("grant")
    finally:
        spec.ROLES["grant"] = original


def test_worker_binding_rejects_identity_and_secret_injection_before_use():
    job = _request("reader")["request"]
    task = job["spec"]["template"]["spec"]["template"]["spec"]
    environment = {item["name"]: item["value"] for item in task["containers"][0]["env"]
                   if "value" in item}
    environment.update({"MFDATAINDIA_DSN": "opaque-private-value",
                        "CLOUD_RUN_JOB": spec.ROLES["reader"].job,
                        "CLOUD_RUN_EXECUTION": spec.ROLES["reader"].job + "-abcde",
                        "CLOUD_RUN_TASK_INDEX": "0", "CLOUD_RUN_TASK_ATTEMPT": "0",
                        "CLOUD_RUN_TASK_COUNT": "1", "PATH": "/usr/bin"})
    args = {"final_git_sha": GIT, "worker_sha256": HASH,
            "controller_sha256": HASH, "spec_sha256": HASH,
            "source_sha256": HASH}
    expected = spec.worker_environment("reader", environment, **args)
    assert expected == spec.payload_from_variables(environment)
    assert expected["format"] == "synthetic-provider-free"
    for field, changed in (("CLOUD_RUN_TASK_ATTEMPT", "1"),
                           ("CLOUD_RUN_JOB", spec.ROLES["grant"].job),
                           ("BROAD_DIRECT_SOURCE_SHA256", "0" * 64)):
        bad = {**environment, field: changed}
        with pytest.raises(spec.DeliverySpecError):
            spec.worker_environment("reader", bad, **args)
    with pytest.raises(spec.DeliverySpecError):
        spec.worker_environment("reader", {**environment, "PGHOST": "elsewhere"}, **args)
    with pytest.raises(spec.DeliverySpecError):
        spec.worker_environment("reader", {**environment, "BROAD_DIRECT_EXTRA": "1"}, **args)


@pytest.mark.parametrize("kind", ("capture", "grant", "reader"))
def test_intent_hash_binds_kind_root_complete_payload_before_worker_dsn(kind):
    prepared = _request(kind)
    task = prepared["task"]
    environment = {row["name"]: row["value"] for row in task["containers"][0]["env"]
                   if "value" in row}
    decoded = spec.payload_from_variables(environment)
    root = environment["BROAD_DIRECT_ROOT"]
    preimage, digest = spec.require_intent(kind, root, decoded)
    assert preimage["payload_without_create_only_intent_sha256"] == {
        "format": "synthetic-provider-free"}
    assert decoded["create_only_intent_sha256"] == digest
    for changed_root in (root + "x", root.replace("20261029", "20261028")):
        with pytest.raises(spec.DeliverySpecError):
            spec.require_intent(kind, changed_root, decoded)
    tampered = deepcopy(decoded)
    tampered["format"] = "different"
    with pytest.raises(spec.DeliverySpecError, match="DIRECT_CREATE_INTENT_HASH_DRIFT"):
        spec.require_intent(kind, root, tampered)
    wrong = deepcopy(decoded)
    wrong["create_only_intent_sha256"] = "0" * 64
    with pytest.raises(spec.DeliverySpecError, match="DIRECT_CREATE_INTENT_HASH_DRIFT"):
        spec.require_intent(kind, root, wrong)


@pytest.mark.parametrize("kind,principal", (
    ("capture", spec.OWNER_ROLE), ("grant", spec.OWNER_ROLE),
    ("reader", spec.READER_ROLE)))
def test_exact_dsn_parser_is_role_separated_and_does_not_connect(kind, principal):
    dsn = ("host=/cloudsql/" + spec.CONNECTION + " dbname=" + spec.DATABASE
           + " user=" + principal + " password=synthetic-only")
    parsed = spec.connection_arguments(kind, dsn)
    assert parsed["user"] == principal and parsed["connect_timeout"] == 5
    assert parsed["autocommit"] is True and parsed["password"] == "synthetic-only"
    wrong = spec.READER_ROLE if kind != "reader" else spec.OWNER_ROLE
    with pytest.raises(spec.DeliverySpecError):
        spec.connection_arguments(kind, dsn.replace("user=" + principal, "user=" + wrong))
    for injection in (" hostaddr=127.0.0.1", " sslmode=require", " port=55521",
                      " connect_timeout=999"):
        with pytest.raises(spec.DeliverySpecError):
            spec.connection_arguments(kind, dsn + injection)


def _exported_job(kind="capture"):
    prepared = _request(kind)
    actual = deepcopy(prepared["request"])
    actual["metadata"].update({
        "uid": "a" * 16,
        "creationTimestamp": "2026-10-29T22:00:00Z",
        "generation": 1,
        "labels": {"cloud.googleapis.com/location": spec.REGION},
        "annotations": {"run.googleapis.com/creator": spec.OPERATOR},
    })
    actual["status"] = {"executionCount": 0}
    return actual, prepared


@pytest.mark.parametrize("kind", ("capture", "grant", "reader"))
def test_complete_created_job_export_is_bound(kind):
    actual, prepared = _exported_job(kind)
    seal = spec.validate_job_export(kind, actual, prepared, namespace=spec.PROJECT_NUMBER)
    assert seal["job_uid"] == "a" * 16
    assert seal["full_job_spec_sha256"] == hashlib.sha256(
        spec.canonical(actual["spec"]).encode()).hexdigest()


@pytest.mark.parametrize("path,value", (
    (("metadata", "name"), "mf-reader-broad-access-261027t"),
    (("metadata", "generation"), 2),
    (("metadata", "annotations", "run.googleapis.com/creator"), "else@example.invalid"),
    (("spec", "template", "spec", "taskCount"), 2),
    (("spec", "template", "spec", "template", "spec", "maxRetries"), 1),
    (("spec", "template", "metadata", "annotations", "run.googleapis.com/vpc-access-egress"), "private-ranges-only"),
    (("status", "executionCount"), 1),
))
def test_created_job_export_drift_fails_before_execution(path, value):
    actual, prepared = _exported_job()
    cursor = actual
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    with pytest.raises(spec.DeliverySpecError):
        spec.validate_job_export("capture", actual, prepared, namespace=spec.PROJECT_NUMBER)


def test_exact_capture_certificate_seal_and_lossless_capacity():
    evidence = {"complete_before_snapshot": {"rows": [1, 2, 3]},
                "confirmed_rollback": True, "permission_enabled": False}
    certificate = spec.seal_certificate(
        payload_sha256=HASH, stage="direct-capture-readonly", evidence=evidence)
    assert spec.verify_certificate(certificate, payload_sha256=HASH,
                                   stage="direct-capture-readonly") == evidence
    forged = deepcopy(certificate)
    forged["evidence"]["permission_enabled"] = True
    with pytest.raises(spec.DeliverySpecError, match="DIRECT_CERTIFICATE_HASH_DRIFT"):
        spec.verify_certificate(forged, payload_sha256=HASH,
                                stage="direct-capture-readonly")
    with pytest.raises(spec.DeliverySpecError):
        spec.verify_certificate(certificate, payload_sha256="0" * 64,
                                stage="direct-capture-readonly")
    with pytest.raises(spec.DeliverySpecError):
        spec.seal_certificate(payload_sha256=HASH, stage="write-anything",
                              evidence=evidence)


def _exported_execution(kind="capture"):
    actual_job, prepared = _exported_job(kind)
    job_seal = spec.validate_job_export(kind, actual_job, prepared,
                                        namespace=spec.PROJECT_NUMBER)
    name = spec.ROLES[kind].job + "-abcde"
    execution = {"apiVersion": "run.googleapis.com/v1", "kind": "Execution",
                 "metadata": {"name": name, "namespace": spec.PROJECT_NUMBER,
                              "uid": "b" * 16,
                              "creationTimestamp": "2026-10-29T22:01:00Z",
                              "generation": 1,
                              "labels": {"cloud.googleapis.com/location": spec.REGION,
                                         "run.googleapis.com/job": spec.ROLES[kind].job,
                                         "run.googleapis.com/jobUid": job_seal["job_uid"],
                                         "run.googleapis.com/jobGeneration": "1"},
                              "annotations": {"run.googleapis.com/creator": spec.OPERATOR,
                                              **prepared["annotations"]},
                              "ownerReferences": [{"apiVersion": "serving.knative.dev/v1",
                                                  "kind": "Job", "name": spec.ROLES[kind].job,
                                                  "uid": job_seal["job_uid"],
                                                  "controller": True,
                                                  "blockOwnerDeletion": True}]},
                 "spec": deepcopy(actual_job["spec"]["template"]["spec"]),
                 "status": {"conditions": [{"type": "Completed", "status": "Unknown"}]}}
    return execution, prepared, job_seal


@pytest.mark.parametrize("kind", ("capture", "grant", "reader"))
def test_complete_execution_identity_parent_and_task_binding(kind):
    actual, prepared, job = _exported_execution(kind)
    identity = spec.validate_execution_export(kind, actual, prepared,
                                              job=job, image=IMAGE)
    assert identity["uid"] == "b" * 16
    assert spec.validate_execution_export(kind, actual, prepared, job=job,
                                          image=IMAGE, original_identity=identity) == identity
    resolved = deepcopy(actual)
    resolved["spec"]["template"]["spec"]["containers"][0]["image"] = (
        spec.IMAGE_PREFIX + "e" * 64)
    image = {**IMAGE, "resolved_image": spec.IMAGE_PREFIX + "e" * 64}
    assert spec.validate_execution_export(kind, resolved, prepared,
                                          job=job, image=image) == identity


@pytest.mark.parametrize("path,value", (
    (("metadata", "labels", "run.googleapis.com/jobUid"), "other-uid"),
    (("metadata", "name"), "mf-reader-broad-access-261027t-abcde"),
    (("metadata", "uid"), "a" * 16),
    (("metadata", "ownerReferences"), [{"apiVersion": "serving.knative.dev/v1",
                                        "kind": "Job", "name": spec.ROLES["capture"].job,
                                        "uid": "wrong-parent", "controller": True,
                                        "blockOwnerDeletion": True}]),
    (("metadata", "annotations", "run.googleapis.com/vpc-access-egress"), "private-ranges-only"),
    (("spec", "template", "spec", "maxRetries"), 1),
))
def test_execution_drift_rejected_before_certificate(path, value):
    actual, prepared, job = _exported_execution()
    cursor = actual
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    with pytest.raises(spec.DeliverySpecError):
        spec.validate_execution_export("capture", actual, prepared,
                                       job=job, image=IMAGE)


def test_original_execution_identity_is_immutable_against_export_mutation():
    actual, prepared, job = _exported_execution()
    original = spec.validate_execution_export("capture", actual, prepared,
                                              job=job, image=IMAGE)
    actual["metadata"]["labels"]["run.googleapis.com/jobUid"] = "drifted"
    assert original["labels"]["run.googleapis.com/jobUid"] == job["job_uid"]
    with pytest.raises(spec.DeliverySpecError):
        spec.validate_execution_export("capture", actual, prepared,
                                       job=job, image=IMAGE,
                                       original_identity=original)


def _capture_payload():
    root = Path(__file__).resolve().parents[1]
    return root, {
        "format": "mf-broad-reader-direct-capture-v1",
        "packaged_source": spec.source_authority.verify_packaged_source(root),
        "git_sha": GIT,
        "image_binding": {
            "app": deepcopy(IMAGE),
            "tools": {**IMAGE, "image": IMAGE["image"].replace(
                "/mfdataindia@", "/mfdataindia-review-tools@"),
                "resolved_image": IMAGE["resolved_image"].replace(
                    "/mfdataindia@", "/mfdataindia-review-tools@")},
        },
        "create_only_intent_sha256": HASH,
        "window": {"starts_at": "2026-10-29T22:00:00+00:00",
                   "ends_at": "2026-10-29T22:10:00+00:00",
                   "exclusive_administration": True,
                   "data_schema_custody": True,
                   "future_owner_provisioning": True},
    }


def test_capture_payload_rechecks_real_complete_packaged_source_bytes():
    root, payload = _capture_payload()
    checked = spec.validate_capture_payload(payload, final_git_sha=GIT,
                                            source_root=root)
    assert checked == payload and checked is not payload
    assert checked["packaged_source"]["base_files"] == 44
    assert checked["packaged_source"]["variant_files"] == 9


@pytest.mark.parametrize("mutation", (
    lambda value: value["packaged_source"]["migration_ledger"][0].update(
        content_sha256="0" * 64),
    lambda value: value["image_binding"]["tools"].update(
        image=value["image_binding"]["app"]["image"]),
    lambda value: value["image_binding"]["app"].update(git_sha="0" * 40),
    lambda value: value["window"].update(data_schema_custody=False),
    lambda value: value.update(create_only_intent_sha256="not-a-sha"),
    lambda value: value.update(unapproved="extra"),
))
def test_capture_payload_drift_fails_before_owner_io(mutation):
    root, payload = _capture_payload()
    mutation(payload)
    with pytest.raises((spec.DeliverySpecError, spec.source_authority.SourceAuthorityError,
                        spec.broad_policy.PolicyError)):
        spec.validate_capture_payload(payload, final_git_sha=GIT,
                                      source_root=root)


def _grant_payload():
    root, capture = _capture_payload()
    return root, {
        "format": "mf-broad-reader-direct-grant-v1",
        "window": deepcopy(capture["window"]),
        "before_snapshot": {"plan": {"permission_acceptance": False},
                            "permission_enabled": False},
        "helper_attestation": {"format": "synthetic-only"},
        "packaged_source": deepcopy(capture["packaged_source"]),
        "git_sha": GIT,
        "image_binding": deepcopy(capture["image_binding"]),
        "create_only_intent_sha256": HASH,
    }


def test_grant_payload_is_purely_prechecked_without_consuming_real_permit():
    root, payload = _grant_payload()
    checked = spec.validate_grant_payload(payload, final_git_sha=GIT,
                                          source_root=root)
    assert checked == payload and checked is not payload
    assert payload["before_snapshot"]["permission_enabled"] is False


@pytest.mark.parametrize("mutation", (
    lambda value: value["before_snapshot"].update(permission_enabled=True),
    lambda value: value["before_snapshot"]["plan"].update(permission_acceptance=True),
    lambda value: value["packaged_source"]["migration_ledger"][0].update(
        content_sha256="0" * 64),
    lambda value: value.update(git_sha="0" * 40),
    lambda value: value.update(create_only_intent_sha256="bad"),
    lambda value: value.update(unapproved="extra"),
))
def test_grant_payload_drift_is_rejected_before_owner_io(mutation):
    root, payload = _grant_payload()
    mutation(payload)
    with pytest.raises((spec.DeliverySpecError, spec.broad_policy.PolicyError,
                        spec.source_authority.SourceAuthorityError)):
        spec.validate_grant_payload(payload, final_git_sha=GIT,
                                    source_root=root)
