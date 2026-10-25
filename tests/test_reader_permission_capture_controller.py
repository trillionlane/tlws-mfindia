"""Pure controller/profile/duplicate/failure proofs; no provider/DB operations."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest
import requests

from mfdataindia.review import reader_permission_capture_controller as core
from mfdataindia.review.reader_permission_capture_backend import Backend, metadata_arguments, load_approval
from mfdataindia.review import reader_permission_capture_backend
from mfdataindia.review.rollout import seal_evidence
from test_verified_review_rollout import manifest
from test_reader_permission_capture import result_fixture

NAMESPACE = "123456789012"  # Provider-free synthetic project number ONLY.
JOB_UID = "11111111-1111-4111-8111-111111111111"
EXEC_UID = "22222222-2222-4222-8222-222222222222"


def test_all_controller_backend_operator_template_bytes_restore_exactly():
    from test_reader_permission_capture_preservation import test_full_template_copies_reverse_only_counted_identity_source_and_owner_literals
    test_full_template_copies_reverse_only_counted_identity_source_and_owner_literals()


def metadata(name, *, execution=False, annotations=None):
    result = {"name": name, "namespace": NAMESPACE,
              "uid": EXEC_UID if execution else JOB_UID, "generation": 1,
              "creationTimestamp": "2026-10-21T20:00:01Z" if execution else "2026-10-21T20:00:00Z",
              "labels": {"cloud.googleapis.com/location": core.REGION},
              "annotations": {"run.googleapis.com/creator": core.ACCOUNT,
                              "run.googleapis.com/lastModifier": core.ACCOUNT,
                              **(annotations or {})}}
    if execution:
        result["labels"].update({"run.googleapis.com/job": core.NAME,
            "run.googleapis.com/jobUid": JOB_UID, "run.googleapis.com/jobGeneration": "1"})
    return result


def approval(**changes):
    m = manifest(rollout_id=core.IDENTITY, actor="rollout-certification:" + core.IDENTITY,
                 cohort=[154117], canary_code=154117)
    return core.Approval.model_validate({"manifest": m, "controller_git_sha": m.git_sha,
        "controller_sha256": "e" * 64, "protected_hash": core.fingerprint({"protected": True}),
        "root": str(core.ARTIFACTS / "mf-reader-permission-capture-20261025-aBc123"), **changes})


@pytest.mark.parametrize("change", [
    {"root": "/tmp/other"}, {"root": str(core.ARTIFACTS / "mf-fs13k-dev-20261021-ZdM43H")},
    {"root": str(core.ARTIFACTS / "mf-reader-permission-capture-20261025-aBc123/../other")},
    {"controller_git_sha": "b" * 40}, {"extra": "unapproved"},
])
def test_approval_rejects_other_roots_git_and_extra_fields(change):
    with pytest.raises(ValueError):
        approval(**change)


def test_whole_request_contains_all_values_secret_profile_and_budget():
    a = approval()
    value = core.inputs(a)
    assert json.loads(value["body"]) == value["request"]
    assert value["budget"]["request_bytes"] == len(value["body"])
    assert value["environment"]["READER_PERMISSION_CAPTURE_PROGRAM_SHA256"] == core.WORKER_SHA
    assert value["environment"]["READER_PERMISSION_CAPTURE_SPEC_SHA256"] == core.SPEC_SHA
    assert len(value["task"]["containers"][0]["env"]) == 6
    assert value["environment"]["READER_PERMISSION_CAPTURE_INVENTORY_SHA256"] == core.INVENTORY_SHA
    assert value["task"]["containers"][0]["env"][-1] == {"name": "MFDATAINDIA_DSN",
        "valueFrom": {"secretKeyRef": {"name": "tlws-mf-data-admin-dsn-dev", "key": "1"}}}
    assert value["task"]["serviceAccountName"] == core.OWNER_SA
    assert value["task"]["maxRetries"] == 0 and value["task"]["timeoutSeconds"] == "600"
    assert value["budget"]["startup_required_bytes"] < 1918605


@pytest.mark.parametrize("defect", ["image", "args", "command", "extra_container_field",
                                     "secret", "extra_env", "duplicate_env", "volume", "cpu",
                                     "memory", "retry", "timeout", "account", "extra_task"])
def test_whole_profile_rejects_every_runtime_capability_drift(defect):
    expected = core.inputs(approval())["task"]
    task = deepcopy(expected)
    c = task["containers"][0]
    if defect in ("image", "args", "command"):
        c[defect] = "unexpected"
    elif defect == "extra_container_field":
        c["workingDir"] = "/other"
    elif defect == "secret":
        c["env"][-1]["valueFrom"]["secretKeyRef"]["key"] = "latest"
    elif defect == "extra_env":
        c["env"].append({"name": "OTHER", "value": "bad"})
    elif defect == "duplicate_env":
        c["env"][-1] = c["env"][0]
    elif defect == "volume":
        task["volumes"] = [{"name": "bad"}]
    elif defect in ("cpu", "memory"):
        c["resources"]["limits"][defect] = "8"
    elif defect == "retry":
        task["maxRetries"] = 1
    elif defect == "timeout":
        task["timeoutSeconds"] = "900"
    elif defect == "account":
        task["serviceAccountName"] = "owner"
    else:
        task["nodeSelector"] = {"unknown": "override"}
    with pytest.raises(ValueError):
        core.validate_task(task, expected)


@pytest.mark.parametrize("kind,name", [("delete", core.NAME), ("job", "prod"),
                                      ("service", "other"), ("schedule", core.NAME),
                                      ("execution", "other-abcde"), ("logs", "x\" OR true"),
                                      ("auth", "other@account"), ("bucket", "gs://other")])
def test_metadata_adapter_denies_out_of_scope_commands_targets_and_injection(kind, name):
    with pytest.raises(ValueError):
        metadata_arguments(kind, name)


@pytest.mark.parametrize("kind,name", [("service", "tlws-mf-data-dev"),
                                      ("job", core.NAME), ("executions", None),
                                      ("schedule", "tlws-mf-official-facts-refresh-dev"),
                                      ("operator", None), ("bucket", None), ("project", None)])
def test_metadata_resource_reads_pin_project_region_and_have_no_write_verb(kind, name):
    args = metadata_arguments(kind, name)
    assert "--project=trillionlane-dev" in args
    if args[0] == "run":
        assert "--region=asia-south1" in args
    if args[0] == "scheduler":
        assert "--location=asia-south1" in args
    assert not set(args) & {"create", "execute", "deploy", "update", "delete", "access"}


def test_execution_inventory_uses_exact_provider_job_selector_before_client_filter_and_limit():
    assert metadata_arguments("executions") == [
        "run", "jobs", "executions", "list", "--job=" + core.NAME,
        "--limit=2",
        "--region=" + core.REGION, "--project=" + core.PROJECT, "--format=json",
    ]


@pytest.mark.parametrize("target", [core.NAME, "other-job", "other-job --project=other"])
def test_execution_inventory_cannot_accept_caller_job_override(target):
    with pytest.raises(ValueError, match="unapproved diagnostic metadata"):
        metadata_arguments("executions", target)


def test_execution_inventory_metadata_timeout_is_once_without_create_or_execute(monkeypatch):
    backend = Backend(approval(), Path.cwd())
    calls = []

    def timeout(argv, **kwargs):
        calls.append((argv, kwargs))
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr("mfdataindia.review.reader_permission_capture_backend.subprocess.run", timeout)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            backend.metadata("executions")
        assert len(calls) == 1
        assert calls[0][0] == [
            "gcloud", "--account=" + core.ACCOUNT, *metadata_arguments("executions")]
        assert "--job=" + core.NAME in calls[0][0]
        assert calls[0][1]["timeout"] == 60
        assert not backend.created and not backend.executed and not backend.sent_urls
    finally:
        backend.session.close()


@pytest.mark.parametrize("warning", ["", "unexpected provider warning"])
def test_server_scoped_empty_execution_inventory_keeps_stderr_fail_closed(monkeypatch, warning):
    backend = Backend(approval(), Path.cwd())
    calls = []

    def empty_provider(argv, **kwargs):
        calls.append(argv)
        # The installed SDK warns on an empty result when a client filter is
        # present. A server-scoped job selection requires no client filter.
        stderr = warning or ("missing metadata.name" if any(
            arg.startswith("--filter=") for arg in argv) else "")
        return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr=stderr)

    monkeypatch.setattr("mfdataindia.review.reader_permission_capture_backend.subprocess.run", empty_provider)
    try:
        if warning:
            with pytest.raises(RuntimeError, match="unavailable or ambiguous"):
                backend.metadata("executions")
        else:
            assert backend.metadata("executions") == []
        assert len(calls) == 1 and "--job=" + core.NAME in calls[0]
        assert not backend.created and not backend.executed and not backend.sent_urls
    finally:
        backend.session.close()


def test_backend_consumes_ambiguous_create_without_retry(monkeypatch):
    backend = Backend(approval(), Path.cwd())
    monkeypatch.setattr(backend, "command", lambda *_: "SYNTHETIC_NOT_A_CREDENTIAL")
    calls = []
    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        raise requests.Timeout("synthetic ambiguous provider failure")
    monkeypatch.setattr(backend.session, "post", fail)
    with pytest.raises(requests.Timeout):
        backend.create(backend.prepared["body"])
    with pytest.raises(ValueError):
        backend.create(backend.prepared["body"])
    assert len(calls) == 1
    assert calls[0][1]["allow_redirects"] is False and calls[0][1]["timeout"] == 30
    with pytest.raises(ValueError):
        backend._post("https://other.invalid", b"{}")
    backend.session.close()


class FakeBackend:
    def __init__(self, a, *, defect=None):
        self.a = a
        self.value = core.inputs(a)
        self.events = []
        self.defect = defect
        self.protected_reads = 0

    def preflight(self, a):
        self.events.append("preflight")
        if self.defect == "preflight":
            raise ValueError("synthetic existing job")
        return NAMESPACE

    def protected(self):
        self.protected_reads += 1
        return {"protected": False if self.defect == "protected" and self.protected_reads > 2 else True}

    def image_resolution(self):
        return {"image": self.a.manifest.image, "resolved_image": self.a.manifest.image,
                "config_digest": "sha256:" + "d" * 64, "git_sha": self.a.manifest.git_sha,
                "platform": "linux/amd64", "layers_downloaded": 0}

    def create(self, body):
        self.events.append("create")
        assert body == self.value["body"]
        if self.defect == "create":
            raise TimeoutError("synthetic ambiguous create")
        return self.job()

    def job(self):
        spec = deepcopy(self.value["request"]["spec"])
        if self.defect == "deployed":
            spec["template"]["spec"]["template"]["spec"]["maxRetries"] = 1
        status = {"conditions": [{"type": "Ready", "status": "True"}], "executionCount": 0}
        if "execute" in self.events:
            status.update({"executionCount": 1, "latestCreatedExecution": {
                "name": core.NAME + "-abcde", "creationTimestamp": "2026-10-21T20:00:01Z"}})
        return {"apiVersion": "run.googleapis.com/v1", "kind": "Job",
                "metadata": metadata(core.NAME), "spec": spec, "status": status}

    def execute(self):
        self.events.append("execute")
        return self.execution(core.NAME + "-abcde")

    def execution(self, name):
        task = deepcopy(self.value["task"])
        if self.defect == "immutable":
            task["containers"][0]["args"] = ["other-script"]
        return {"apiVersion": "run.googleapis.com/v1", "kind": "Execution",
                "metadata": metadata(name, execution=True, annotations=self.value["annotations"]),
                "spec": {"taskCount": 1, "parallelism": 1, "template": {"spec": task}},
                "status": {"succeededCount": 1, "conditions": [{"type": "Completed",
                    "status": "False" if self.defect == "terminal" else "True"}]}}

    def executions(self):
        return [self.execution(core.NAME + "-abcde")] if "execute" in self.events else []

    def certificate(self, name):
        result = result_fixture()
        result["worker_binding"] = {
            "packaged_program_sha256": core.WORKER_SHA, "packaged_spec_sha256": core.SPEC_SHA,
            "packaged_inventory_sha256": core.INVENTORY_SHA,
            "manifest_image": self.a.manifest.image, "manifest_git_sha": self.a.manifest.git_sha,
        }
        if self.defect == "certificate":
            result["worker_binding"]["packaged_program_sha256"] = "0" * 64
        return seal_evidence(self.a.manifest, core.STAGE, result)


def test_run_one_create_one_execute_full_preexecute_seal_and_global_duplicate_guard(tmp_path):
    # Only this provider-free fixture bypasses the production absolute root
    # validator. No file under actual artifacts, no provider operation occurs.
    a = approval().model_copy(update={"root": str(tmp_path)})
    (tmp_path / "approval.json").write_text("{}")
    records = {}
    def claim(path, value):
        if path in records:
            raise FileExistsError("synthetic global duplicate")
        records[path] = deepcopy(value)
    b = FakeBackend(a)
    core.run(a, b, claim=claim)
    assert b.events == ["preflight", "create", "execute"]
    assert records[tmp_path / "deployed-before-execute.json"]["before_execute"] is True
    assert records[tmp_path / "intent.json"]["request"] == core.inputs(a)["request"]
    assert records[tmp_path / "execute-intent.json"]["body"] == {}
    assert records[tmp_path / "protected-before.json"] == records[tmp_path / "protected-after.json"]
    assert list(records).index(tmp_path / "result.json") > list(records).index(tmp_path / "protected-after.json")
    assert list(records)[-2:] == [tmp_path / "protected-final.json", tmp_path / "result.json"]
    with pytest.raises(FileExistsError):
        core.run(a, b, claim=claim)
    assert b.events == ["preflight", "create", "execute"]


@pytest.mark.parametrize("defect,expected", [("preflight", ["preflight"]),
    ("create", ["preflight", "create"]), ("deployed", ["preflight", "create"]),
    ("immutable", ["preflight", "create", "execute"]), ("terminal", ["preflight", "create", "execute"]),
    ("certificate", ["preflight", "create", "execute"]), ("protected", ["preflight", "create", "execute"])])
def test_failures_retained_never_retry_create_or_execute(tmp_path, defect, expected):
    a = approval().model_copy(update={"root": str(tmp_path)})
    (tmp_path / "approval.json").write_text("{}")
    records = {}
    b = FakeBackend(a, defect=defect)
    with pytest.raises((ValueError, TimeoutError)):
        core.run(a, b, claim=lambda path, value: records.setdefault(path, deepcopy(value)))
    assert b.events == expected
    assert tmp_path / "protected-after.json" in records
    assert tmp_path / "result.json" not in records
    if defect != "protected":
        assert records[tmp_path / "failure.json"]["acceptance"] is False
    else:
        assert records[tmp_path / "protected-failure.json"]["acceptance"] is False


def test_actual_create_only_records_never_overwrite_or_follow_existing_symlink(tmp_path):
    path = tmp_path / "intent.json"
    core.exclusive(path, {"first": True})
    with pytest.raises(FileExistsError):
        core.exclusive(path, {"second": True})
    assert json.loads(path.read_text()) == {"first": True}
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(FileExistsError):
        core.exclusive(link, {})


def test_duplicate_approval_members_rejected_before_provider_or_claim(tmp_path):
    path = tmp_path / "approval.json"
    path.write_text('{"root":"one","root":"two"}')
    with pytest.raises(ValueError, match="duplicate"):
        load_approval(path, "a" * 64)


def test_protected_snapshot_includes_full_project_and_bucket_iam_and_normal_profiles(monkeypatch):
    backend = Backend(approval(), Path.cwd())
    calls = []
    def metadata(kind, name=None):
        calls.append((kind, name))
        if kind == "service":
            return {"spec": {"whole": name}, "status": {"traffic": [{"whole": name}]}}
        if kind == "job":
            return {"spec": {"whole": name}, "status": {"volatile": True}}
        if kind == "schedule":
            return {"schedule": "unchanged", "httpTarget": {"whole": name},
                    "status": {}, "lastAttemptTime": "volatile", "scheduleTime": "volatile"}
        return {"bindings": [{"all": kind, "condition": {"whole": True}}], "etag": "unchanged"}
    monkeypatch.setattr(backend, "metadata", metadata)
    result = backend.protected()
    assert len(calls) == len(result) == 15
    assert ("operator", None) in calls and ("bucket", None) in calls
    assert result["operator:None"]["bindings"][0]["condition"] == {"whole": True}
    assert result["bucket:None"]["etag"] == "unchanged"
    for name in ("tlws-mf-official-facts-refresh-dev", "tlws-mf-data-nav-refresh-dev", "tlws-mf-data-nav-audit-dev"):
        assert result["schedule:" + name] == {"schedule": "unchanged", "httpTarget": {"whole": name}}
        assert result["job:" + name] == {"spec": {"whole": name}}
    backend.session.close()


@pytest.mark.parametrize("field,value", [
    ("application_queries_executed", 1), ("application_functions_executed", 1),
    ("permission_changes", 1), ("role_changes", 1), ("data_writes", 1),
    ("historical_denied_statement_proved", True), ("performance_acceptance", True),
    ("catalogue_hash", "0"*64), ("source_hashes", {}),
])
def test_result_rejects_any_query_grant_historical_claim_or_incomplete_catalogue(field, value):
    a = approval()
    result = deepcopy(FakeBackend(a).certificate(core.NAME + "-abcde")["evidence"])
    result[field] = value
    with pytest.raises(ValueError):
        core.validate_result(a, seal_evidence(a.manifest, core.STAGE, result))


@pytest.mark.parametrize("token", ["startExecutionToken", "runExecutionToken"])
def test_job_spec_execution_tokens_never_admitted_even_if_empty(token):
    b = FakeBackend(approval())
    job = b.job()
    job["spec"][token] = ""
    with pytest.raises(ValueError, match="template-only"):
        core.deployed(job, b.value, namespace=NAMESPACE)


@pytest.mark.parametrize("defect", ["template_connector", "top_breakglass", "execution_template_label",
                                    "top_extra_metadata", "wrong_creator", "wrong_namespace",
                                    "wrong_location", "bool_task_count", "extra_execution_spec"])
def test_whole_metadata_and_spec_reject_extra_capabilities(defect):
    b = FakeBackend(approval())
    job = b.job()
    if defect == "template_connector":
        job["spec"]["template"]["metadata"]["annotations"]["run.googleapis.com/vpc-access-connector"] = "other"
    elif defect == "top_breakglass":
        job["metadata"]["annotations"]["run.googleapis.com/binary-authorization-breakglass"] = "unapproved"
    elif defect == "execution_template_label":
        job["spec"]["template"]["metadata"]["labels"] = {"unexpected": "capability"}
    elif defect == "top_extra_metadata":
        job["metadata"]["ownerReferences"] = [{"unapproved": True}]
    elif defect == "wrong_creator":
        job["metadata"]["annotations"]["run.googleapis.com/creator"] = "other@example.com"
    elif defect == "wrong_namespace":
        job["metadata"]["namespace"] = "999999999999"
    elif defect == "wrong_location":
        job["metadata"]["labels"]["cloud.googleapis.com/location"] = "other"
    elif defect == "bool_task_count":
        job["spec"]["template"]["spec"]["taskCount"] = True
    else:
        job["spec"]["template"]["spec"]["extraCapability"] = "unapproved"
    with pytest.raises(ValueError):
        core.deployed(job, b.value, namespace=NAMESPACE)


@pytest.mark.parametrize("field,value", [("executionCount", 1), ("executionCount", True),
    ("latestCreatedExecution", {"name": core.NAME + "-prior"})])
def test_unstarted_job_status_must_have_no_prior_executions(field, value):
    b = FakeBackend(approval())
    job = b.job()
    job["status"][field] = value
    with pytest.raises(ValueError):
        core.job_execution_status(job)


@pytest.mark.parametrize("field,value", [("uid", "33333333-3333-4333-8333-333333333333"),
                                       ("creationTimestamp", "2026-10-21T20:00:02Z")])
def test_resource_replacement_changes_seal_even_when_profile_identical(field, value):
    b = FakeBackend(approval())
    job = b.job()
    sealed = core.deployed(job, b.value, namespace=NAMESPACE)
    job["metadata"][field] = value
    assert core.deployed(job, b.value, namespace=NAMESPACE) != sealed


@pytest.mark.parametrize("defect", ["parent_uid", "parent_name", "parent_generation", "extra_annotation",
                                    "execution_uid", "execution_created", "whole_spec"])
def test_immutable_execution_binds_original_identity_parent_and_entire_input(defect):
    b = FakeBackend(approval())
    seal = core.deployed(b.job(), b.value, namespace=NAMESPACE)
    e = b.execute()
    identity = core.immutable_execution(e, b.value, seal, namespace=NAMESPACE)
    if defect.startswith("parent"):
        key = {"parent_uid": "jobUid", "parent_name": "job", "parent_generation": "jobGeneration"}[defect]
        e["metadata"]["labels"]["run.googleapis.com/" + key] = "other"
    elif defect == "extra_annotation":
        e["metadata"]["annotations"]["run.googleapis.com/encryption-key"] = "unapproved"
    elif defect == "execution_uid":
        e["metadata"]["uid"] = "33333333-3333-4333-8333-333333333333"
    elif defect == "execution_created":
        e["metadata"]["creationTimestamp"] = "2026-10-21T20:00:02Z"
    else:
        e["spec"]["extraCapability"] = "unapproved"
    with pytest.raises(ValueError):
        core.immutable_execution(e, b.value, seal, namespace=NAMESPACE, identity=identity)


@pytest.mark.parametrize("injected_at,expected_executes", [(1, 0), (2, 0), (3, 1), (4, 1)])
def test_sibling_inventory_before_run_after_run_or_immediately_before_result_never_admitted(tmp_path, injected_at, expected_executes):
    a = approval().model_copy(update={"root": str(tmp_path)})
    (tmp_path / "approval.json").write_text("{}")
    b = FakeBackend(a)
    original = b.executions
    inventory_reads = 0
    def executions():
        nonlocal inventory_reads
        inventory_reads += 1
        rows = original()
        if inventory_reads == injected_at:
            rows.append(b.execution(core.NAME + "-other"))
        return rows
    b.executions = executions
    records = {}
    with pytest.raises(ValueError):
        core.run(a, b, claim=lambda p, v: records.setdefault(p, deepcopy(v)))
    assert b.events.count("execute") == expected_executes
    assert b.events.count("create") == 1
    assert tmp_path / "result.json" not in records
    assert tmp_path / "failure.json" in records


def test_already_started_create_response_rejected_before_fresh_read_or_explicit_run(tmp_path):
    a = approval().model_copy(update={"root": str(tmp_path)})
    (tmp_path / "approval.json").write_text("{}")
    b = FakeBackend(a)
    original = b.create
    def create(body):
        job = original(body)
        job["status"]["executionCount"] = 1
        return job
    b.create = create
    with pytest.raises(ValueError, match="execution count"):
        core.run(a, b, claim=lambda *_: None)
    assert b.events == ["preflight", "create"]


@pytest.mark.parametrize("changed_read,expected_executes", [(2, 0), (3, 0), (4, 0), (5, 1), (6, 1)])
def test_recreated_job_uid_at_any_read_rejects_without_duplicate_execution(tmp_path, changed_read, expected_executes):
    a = approval().model_copy(update={"root": str(tmp_path)})
    (tmp_path / "approval.json").write_text("{}")
    b = FakeBackend(a)
    original = b.job
    reads = 0
    def job():
        nonlocal reads
        reads += 1
        value = original()
        if reads == changed_read:
            value["metadata"]["uid"] = "33333333-3333-4333-8333-333333333333"
        return value
    b.job = job
    records = {}
    with pytest.raises(ValueError):
        core.run(a, b, claim=lambda p, v: records.setdefault(p, deepcopy(v)))
    assert b.events.count("execute") == expected_executes
    assert tmp_path / "result.json" not in records


@pytest.mark.parametrize("project", [None, {"projectId": "other", "projectNumber": NAMESPACE},
    {"projectId": core.PROJECT, "projectNumber": "ambiguous"},
    {"projectId": core.PROJECT, "projectNumber": NAMESPACE}])
def test_preflight_binds_fresh_project_number_before_any_image_or_write(monkeypatch, project):
    a = approval(controller_sha256=core.hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest())
    backend = Backend(a, Path.cwd())
    events = []
    monkeypatch.setattr(reader_permission_capture_backend, "verify_checkout", lambda *_: events.append("clean_git"))
    def image(*args, **kwargs):
        events.append("OCI_read")
        assert kwargs == {"include_resolution": True}
        return FakeBackend(a).image_resolution()
    monkeypatch.setattr(reader_permission_capture_backend, "verify_image", image)
    def read(kind, name=None):
        events.append(kind)
        return {"auth": [{"account": core.ACCOUNT}],
                "operator": {"bindings": [{"role": "roles/owner", "members": ["user:" + core.ACCOUNT]}]},
                "jobs": [], "executions": [], "project": project or {}}[kind]
    monkeypatch.setattr(backend, "metadata", read)
    if project and project.get("projectId") == core.PROJECT and project.get("projectNumber") == NAMESPACE:
        assert backend.preflight(a) == NAMESPACE
        assert events[-3:] == ["project", "OCI_read", "OCI_read"]
    else:
        with pytest.raises(ValueError, match="projectNumber"):
            backend.preflight(a)
        assert "OCI_read" not in events
    assert not backend.created and not backend.executed
    backend.session.close()


def test_preflight_rejects_shadow_spec_module_before_any_provider_call(monkeypatch):
    a = approval(controller_sha256=core.hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest())
    b = Backend(a, Path.cwd())
    monkeypatch.setattr(reader_permission_capture_backend, "verify_checkout", lambda *_: None)
    monkeypatch.setattr(core.permission, "__file__", "/tmp/not-the-reviewed-spec.py")
    monkeypatch.setattr(b, "metadata", lambda *_: pytest.fail("provider read on shadow specification"))
    try:
        with pytest.raises(ValueError, match="bytes differ"):
            b.preflight(a)
    finally:
        b.session.close()


def realistic_backend(a):
    """Synthetic identities with metadata keys observed on an existing v1 GET.

    Not reused source/failed-attempt state, and not new diagnostic acceptance.
    """
    b = FakeBackend(a)
    job, execution = b.job, b.execution
    client = {"run.googleapis.com/client-name": "gcloud",
              "run.googleapis.com/client-version": "580.0.0"}
    def realistic_job():
        j = job()
        j["metadata"]["annotations"].update(client)
        j["metadata"]["labels"].update({"run.googleapis.com/satisfiesPzs": "true",
            "run.googleapis.com/lastUpdatedTime": "2026-10-21T20:00:00Z"})
        j["metadata"]["selfLink"] = f"/apis/run.googleapis.com/v1/namespaces/{NAMESPACE}/jobs/{core.NAME}"
        j["spec"]["template"]["metadata"]["annotations"].update(client)
        j["spec"]["template"]["metadata"]["labels"] = {"client.knative.dev/nonce": "SYNTHETIC_nonce"}
        return j
    def realistic_execution(name):
        e = execution(name)
        e["metadata"]["annotations"].update(client)
        e["metadata"]["labels"].update({"client.knative.dev/nonce": "SYNTHETIC_nonce",
            "run.googleapis.com/satisfiesPzs": "true", "run.googleapis.com/jobResourceVersion": "SYNTHETIC_opaque_v1"})
        e["metadata"]["selfLink"] = f"/apis/run.googleapis.com/v1/namespaces/{NAMESPACE}/executions/{name}"
        e["metadata"]["ownerReferences"] = [{"apiVersion": "serving.knative.dev/v1",
            "kind": "Job", "name": core.NAME, "uid": JOB_UID,
            "controller": True, "blockOwnerDeletion": True}]
        return e
    b.job, b.execution = realistic_job, realistic_execution
    return b


def test_provider_shaped_informational_metadata_is_sealed_without_extra_capabilities(tmp_path):
    a = approval().model_copy(update={"root": str(tmp_path)})
    (tmp_path / "approval.json").write_text("{}")
    b = realistic_backend(a)
    records, provider_reads = {}, []
    original = b.protected
    def protected():
        provider_reads.append("protected")
        return original()
    b.protected = protected
    original_inventory = b.executions
    def inventory():
        provider_reads.append("inventory")
        return original_inventory()
    b.executions = inventory
    def claim(p, v):
        if p.name == "result.json":
            assert provider_reads[-1] == "protected"
            assert records[tmp_path / "protected-final.json"] == {"protected": True}
        records[p] = deepcopy(v)
    core.run(a, b, claim=claim)
    assert b.events == ["preflight", "create", "execute"]
    assert records[tmp_path / "deployed-before-execute.json"]["template_labels"] == {"client.knative.dev/nonce": "SYNTHETIC_nonce"}
    assert list(records)[-2:] == [tmp_path / "protected-final.json", tmp_path / "result.json"]


@pytest.mark.parametrize("defect", ["owner_uid", "owner_kind", "owner_bool", "client", "nonce",
                                    "job_version", "extra_label", "capability", "self_link"])
def test_realistic_metadata_still_rejects_identity_provenance_and_capability_drift(defect):
    b = realistic_backend(approval())
    seal = core.deployed(b.job(), b.value, namespace=NAMESPACE)
    e = b.execute()
    identity = core.immutable_execution(e, b.value, seal, namespace=NAMESPACE)
    if defect.startswith("owner"):
        key, value = {"owner_uid": ("uid", "other"), "owner_kind": ("kind", "Service"),
                      "owner_bool": ("controller", 1)}[defect]
        e["metadata"]["ownerReferences"][0][key] = value
    elif defect == "client":
        e["metadata"]["annotations"]["run.googleapis.com/client-version"] = "other"
    elif defect == "nonce":
        e["metadata"]["labels"]["client.knative.dev/nonce"] = "other"
    elif defect == "job_version":
        e["metadata"]["labels"]["run.googleapis.com/jobResourceVersion"] = "different_opaque"
    elif defect == "extra_label":
        e["metadata"]["labels"]["unexpected"] = "unreviewed"
    elif defect == "self_link":
        e["metadata"]["selfLink"] = "/other-resource"
    else:
        e["metadata"]["annotations"]["run.googleapis.com/binary-authorization-breakglass"] = "unapproved"
    with pytest.raises(ValueError):
        core.immutable_execution(e, b.value, seal, namespace=NAMESPACE, identity=identity)


def test_protected_drift_during_final_inventory_denies_result(tmp_path):
    a = approval().model_copy(update={"root": str(tmp_path)})
    (tmp_path / "approval.json").write_text("{}")
    b = FakeBackend(a)
    original = b.executions
    reads, drifted = 0, False
    def inventory():
        nonlocal reads, drifted
        reads += 1
        if reads == 4:  # The last pre-result inventory, AFTER protected-after.
            drifted = True
        return original()
    b.executions = inventory
    b.protected = lambda: {"protected": not drifted}
    records = {}
    with pytest.raises(ValueError, match="protected drift during final inventory"):
        core.run(a, b, claim=lambda p, v: records.setdefault(p, deepcopy(v)))
    assert records[tmp_path / "protected-after.json"] == {"protected": True}
    assert records[tmp_path / "protected-final.json"] == {"protected": False}
    assert tmp_path / "result.json" not in records
    assert records[tmp_path / "failure.json"]["acceptance"] is False
    assert b.events.count("execute") == 1
