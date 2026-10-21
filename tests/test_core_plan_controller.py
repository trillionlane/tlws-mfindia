"""Pure controller/profile/duplicate/failure proofs; no provider/DB operations."""

from copy import deepcopy
import json
from pathlib import Path

import pytest
import requests

from mfdataindia.review import core_plan_controller as core
from mfdataindia.review.core_plan_backend import Backend, metadata_arguments, load_approval
from mfdataindia.review.rollout import seal_evidence
from test_verified_review_rollout import manifest


def approval(**changes):
    m = manifest(rollout_id=core.IDENTITY, actor="rollout-certification:" + core.IDENTITY,
                 cohort=[154117], canary_code=154117)
    return core.Approval.model_validate({"manifest": m, "controller_git_sha": m.git_sha,
        "controller_sha256": "e" * 64, "protected_hash": core.fingerprint({"protected": True}),
        "root": str(core.ARTIFACTS / "mf-core-plan-diagnostic-20261021-aBc123"), **changes})


@pytest.mark.parametrize("change", [
    {"root": "/tmp/other"}, {"root": str(core.ARTIFACTS / "mf-fs13k-dev-20261021-ZdM43H")},
    {"root": str(core.ARTIFACTS / "mf-core-plan-diagnostic-20261021-aBc123/../other")},
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
    assert value["environment"]["CORE_PLAN_PROGRAM_SHA256"] == core.WORKER_SHA
    assert len(value["task"]["containers"][0]["env"]) == 4
    assert value["task"]["containers"][0]["env"][-1] == {"name": "MFDATAINDIA_DSN",
        "valueFrom": {"secretKeyRef": {"name": "tlws-mf-review-read-dsn-dev", "key": "1"}}}
    assert value["task"]["serviceAccountName"] == core.CALLER_SA
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
                                      ("operator", None), ("bucket", None)])
def test_metadata_resource_reads_pin_project_region_and_have_no_write_verb(kind, name):
    args = metadata_arguments(kind, name)
    assert "--project=trillionlane-dev" in args
    if args[0] == "run":
        assert "--region=asia-south1" in args
    if args[0] == "scheduler":
        assert "--location=asia-south1" in args
    assert not set(args) & {"create", "execute", "deploy", "update", "delete", "access"}


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

    def protected(self):
        self.protected_reads += 1
        return {"protected": False if self.defect == "protected" and self.protected_reads > 2 else True}

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
        return {"metadata": {"name": core.NAME, "generation": 1}, "spec": spec,
                "status": {"conditions": [{"type": "Ready", "status": "True"}]}}

    def execute(self):
        self.events.append("execute")
        return self.execution(core.NAME + "-abcde")

    def execution(self, name):
        task = deepcopy(self.value["task"])
        if self.defect == "immutable":
            task["containers"][0]["args"] = ["other-script"]
        return {"metadata": {"name": name, "annotations": self.value["annotations"]},
                "spec": {"taskCount": 1, "parallelism": 1, "template": {"spec": task}},
                "status": {"succeededCount": 1, "conditions": [{"type": "Completed",
                    "status": "False" if self.defect == "terminal" else "True"}]}}

    def certificate(self, name):
        result = {"code": 154117, "query_file_sha256": core.QUERY_SHA,
            "worker_binding": {"packaged_program_sha256": core.WORKER_SHA,
                "manifest_image": self.a.manifest.image, "manifest_git_sha": self.a.manifest.git_sha},
            "current_facts_acl_verified_before_planning": True,
            "sealed_active_projection": {"complete_capture": {"projection_active": True}},
            "actual_sibling_queries_executed": 0, "historical_K_plan_or_core_role_proved": False,
            "performance_acceptance": False, "rollout_acceptance": False,
            "data_writes": 0, "source_requests": 0, "human_events_created": 0,
            "context": {"effective": "mfdata_verified_reader", "authenticated": "mfdata_verified_reader",
                "database": "mfdataindia", "read_only": "on", "isolation": "repeatable read",
                "statement_timeout": "5s", "lock_timeout": "5s", "transaction_timeout": "15s", "snapshot": "100:101:"},
            "plans": [{"query": label, "ANALYZE": False, "complete_plan": [{"Plan": {}}],
                       "complete_plan_hash": core.fingerprint([{"Plan": {}}]),
                       "SQL_sha256": core.hashlib.sha256(sql.encode()).hexdigest(), "bindings": bindings}
                      for label, sql, bindings in [("own_facts", core.queries._FACTS_SELECT, {"code": 154117}),
                          ("single_sibling", core.queries._SIBLING_SOURCE_SQL, {"code": 154117}),
                          ("batch_sibling", core.queries._BATCH_SIBLING_SOURCE_SQL, {"codes": [154117]})]]}
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


@pytest.mark.parametrize("field,value", [("SQL_sha256", "0" * 64), ("bindings", {"code": 123}),
                                       ("ANALYZE", True)])
def test_result_rejects_changed_sql_bindings_or_executing_plan(field, value):
    a = approval()
    cert = FakeBackend(a).certificate(core.NAME + "-abcde")
    result = deepcopy(cert["evidence"])
    result["plans"][0][field] = value
    with pytest.raises(ValueError):
        core.validate_result(a, seal_evidence(a.manifest, core.STAGE, result))
