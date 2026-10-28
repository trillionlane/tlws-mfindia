"""Full diagnostic controller with in-memory provider/filesystem doubles only."""
from copy import deepcopy
from pathlib import Path
import runpy

import pytest
import requests

from function_identification_fixtures import Connection
from mfdataindia.review import reader_function_identification as collector
from mfdataindia.review import reader_function_identification_controller as core
from mfdataindia.review import reader_function_identification_backend as adapter
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.rollout import seal_evidence

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "123456789012"
JOB_UID = "11111111-1111-4111-8111-111111111111"
EXEC_UID = "22222222-2222-4222-8222-222222222222"


def approval():
    smoke = runpy.run_path(str(ROOT / "scripts/smoke_reader_function_identification_binding.py"))
    return smoke["synthetic_inputs"]("a" * 40)[0]


def metadata(name, prepared, *, execution=False):
    value = {
        "name": name, "namespace": NAMESPACE, "uid": EXEC_UID if execution else JOB_UID,
        "generation": 1,
        "creationTimestamp": "2026-10-28T00:00:01Z" if execution else "2026-10-28T00:00:00Z",
        "labels": {"cloud.googleapis.com/location": core.REGION},
        "annotations": {"run.googleapis.com/creator": core.ACCOUNT,
                        "run.googleapis.com/lastModifier": core.ACCOUNT},
    }
    if execution:
        value["labels"].update({"run.googleapis.com/job": core.NAME,
                                "run.googleapis.com/jobUid": JOB_UID,
                                "run.googleapis.com/jobGeneration": "1"})
        value["annotations"].update(prepared["annotations"])
    return value


class FakeBackend:
    def __init__(self, a, defect=None):
        self.a, self.defect = a, defect
        self.prepared = core.inputs(a)
        self.events = []
        self.executed = False
        self.protected_reads = 0

    def preflight(self, a):
        assert a == self.a
        self.events.append("preflight")
        if self.defect == "preflight":
            raise ValueError("synthetic duplicate")
        return NAMESPACE

    def image_resolution(self):
        return {"image": self.a.manifest.image, "resolved_image": self.a.manifest.image,
                "config_digest": "sha256:" + "d" * 64,
                "git_sha": self.a.manifest.git_sha, "platform": "linux/amd64",
                "layers_downloaded": 0}

    def protected(self):
        self.events.append("protected")
        self.protected_reads += 1
        d = {"SYNTHETIC_NOT_READINESS_OR_APPROVAL": True}
        if ((self.defect == "protected" and self.protected_reads == 3)
                or (self.defect == "final_protected" and self.protected_reads == 4)):
            d["SYNTHETIC_NOT_READINESS_OR_APPROVAL"] = False
        return d

    def create(self, body):
        assert body == self.prepared["body"]
        self.events.append("create")
        if self.defect == "create":
            raise TimeoutError("synthetic ambiguous create")
        return self.job()

    def job(self):
        self.events.append("job")
        status = {"conditions": [{"type": "Ready", "status": "True"}], "executionCount": 0}
        if self.executed:
            status.update(executionCount=1, latestCreatedExecution={
                "name": core.NAME + "-abcde", "creationTimestamp": "2026-10-28T00:00:01Z"})
        value = {"apiVersion": "run.googleapis.com/v1", "kind": "Job",
                 "metadata": metadata(core.NAME, self.prepared),
                 "spec": deepcopy(self.prepared["request"]["spec"]), "status": status}
        if self.defect == "deployed":
            value["spec"]["template"]["spec"]["template"]["spec"]["maxRetries"] = 1
        return value

    def execute(self):
        self.events.append("execute")
        self.executed = True
        if self.defect == "execute":
            raise TimeoutError("synthetic ambiguous run")
        return self.execution(core.NAME + "-abcde")

    def execution(self, name):
        self.events.append("execution")
        value = {
            "apiVersion": "run.googleapis.com/v1", "kind": "Execution",
            "metadata": metadata(name, self.prepared, execution=True),
            "spec": deepcopy(self.prepared["request"]["spec"]["template"]["spec"]),
            "status": {"succeededCount": 1, "conditions": [
                {"type": "Completed", "status": "False" if self.defect == "terminal" else "True"}]},
        }
        if self.defect == "immutable":
            value["spec"]["template"]["spec"]["containers"][0]["args"] = ["unapproved"]
        return value

    def executions(self):
        self.events.append("executions")
        if not self.executed:
            return []
        result = [self.execution(core.NAME + "-abcde")]
        if self.defect == "extra_execution":
            result.append(deepcopy(result[0]))
        return result

    def certificate(self, name):
        assert name == core.NAME + "-abcde"
        self.events.append("certificate")
        result = collector.Capture().run(Connection())
        result["worker_binding"] = {
            "packaged_program_sha256": core.WORKER_SHA, "packaged_spec_sha256": core.SPEC_SHA,
            "packaged_inventory_sha256": core.INVENTORY_SHA,
            "packaged_controller_sha256": self.a.controller_sha256,
            "manifest_image": self.a.manifest.image, "manifest_git_sha": self.a.manifest.git_sha,
        }
        result["input_binding"] = {
            "root": self.a.root, "approval_sha256": fingerprint(self.a.model_dump(mode="json")),
            "request_sha256": core.hashlib.sha256(self.prepared["body"]).hexdigest(),
            "controller_git_sha": self.a.controller_git_sha,
            "controller_sha256": self.a.controller_sha256,
        }
        if self.defect == "certificate":
            result["worker_binding"]["packaged_program_sha256"] = "0" * 64
        return seal_evidence(self.a.manifest, core.STAGE, result)


def memory_filesystem(monkeypatch, a):
    # Emulate ONLY root-existence/listing in this new controller module.
    # The absolute production root validator, spec and request stay real.
    # No actual artifacts/START/runtime directory is created by this unit test.
    original = core.Path

    class SyntheticPath(type(Path())):
        def is_dir(self):
            return True if str(self) == a.root else super().is_dir()

        def iterdir(self):
            if str(self) == a.root:
                return iter([original(a.root) / "approval.json"])
            return super().iterdir()
    monkeypatch.setattr(core, "Path", SyntheticPath)
    records = {}

    def claim(path, value):
        if path in records:
            raise FileExistsError("synthetic global duplicate")
        records[path] = deepcopy(value)
    return records, claim


def test_full_new_controller_success_orders_final_inventory_then_protected_LAST(monkeypatch):
    a = approval()
    records, claim = memory_filesystem(monkeypatch, a)
    b = FakeBackend(a)
    core.run(a, b, claim=claim)
    result = records[Path(a.root) / "result.json"]
    core.validate_completion(result)
    assert b.events.count("create") == b.events.count("execute") == 1
    assert list(records)[-3:] == [
        Path(a.root) / "inventory-before-result.json",
        Path(a.root) / "protected-final.json", Path(a.root) / "result.json"]
    assert b.events[-1] == "protected" and b.protected_reads == 4
    assert result["diagnostic_only"] is True and result["acceptance"] is False
    with pytest.raises(FileExistsError):
        core.run(a, b, claim=claim)
    assert b.events.count("create") == b.events.count("execute") == 1
    from mfdataindia.review import broad_reader_transport_spec_v2 as write_spec
    with pytest.raises(ValueError):
        write_spec.proposal(result, {})  # Identification is not a full capture/grant authority.


@pytest.mark.parametrize("defect", ["preflight", "create", "deployed", "execute", "immutable",
                                   "terminal", "certificate", "extra_execution",
                                   "protected", "final_protected"])
def test_new_controller_failure_never_retries_or_accepts(monkeypatch, defect):
    a = approval()
    records, claim = memory_filesystem(monkeypatch, a)
    b = FakeBackend(a, defect)
    with pytest.raises((ValueError, TimeoutError)):
        core.run(a, b, claim=claim)
    assert Path(a.root) / "result.json" not in records
    assert Path(a.root) / "protected-after.json" in records
    assert b.events.count("create") <= 1 and b.events.count("execute") <= 1
    assert (Path(a.root) / "failure.json" in records
            or Path(a.root) / "protected-failure.json" in records)


@pytest.mark.parametrize("defect", ["args", "command", "image", "secret", "duplicate_env",
                                   "extra_env", "cpu", "memory", "retry", "timeout",
                                   "account", "volume", "extra_task", "extra_container"])
def test_exact_runtime_profile_has_no_capability_drift(defect):
    expected = core.inputs(approval())["task"]
    actual = deepcopy(expected)
    c = actual["containers"][0]
    if defect in ("args", "command", "image"):
        c[defect] = "unexpected"
    elif defect == "secret":
        c["env"][-1]["valueFrom"]["secretKeyRef"]["key"] = "latest"
    elif defect == "duplicate_env":
        c["env"][-1] = deepcopy(c["env"][0])
    elif defect == "extra_env":
        c["env"].append({"name": "OTHER", "value": "unapproved"})
    elif defect in ("cpu", "memory"):
        c["resources"]["limits"][defect] = "8"
    elif defect == "retry":
        actual["maxRetries"] = 1
    elif defect == "timeout":
        actual["timeoutSeconds"] = "900"
    elif defect == "account":
        actual["serviceAccountName"] = "other"
    elif defect == "volume":
        actual["volumes"] = [{"name": "unapproved"}]
    elif defect == "extra_task":
        actual["nodeSelector"] = {"other": "unapproved"}
    else:
        c["workingDir"] = "/other"
    with pytest.raises(ValueError):
        core.validate_task(actual, expected)


@pytest.mark.parametrize("old", ["mf-reader-broad-capture-261026r",
                                "mf-reader-broad-capture-261027s",
                                "mf-reader-broad-capture-261027t"])
@pytest.mark.parametrize("kind", ["job", "executions", "execution", "logs"])
def test_all_consumed_old_targets_denied_before_any_provider_call(old, kind):
    with pytest.raises(ValueError):
        adapter.metadata_arguments(kind, old + "-abcde" if kind in ("execution", "logs") else old)


def test_request_capacity_and_exact_existing_secret_reference():
    a = approval()
    value = core.inputs(a)
    assert value["budget"]["request_bytes"] == len(value["body"])
    assert value["task"]["containers"][0]["env"][-1] == {
        "name": "MFDATAINDIA_DSN",
        "valueFrom": {"secretKeyRef": {"name": "tlws-mf-data-admin-dsn-dev", "key": "1"}}}
    assert value["task"]["serviceAccountName"] == core.OWNER_SA
    assert value["task"]["timeoutSeconds"] == "600" and value["task"]["maxRetries"] == 0
    assert value["request"]["spec"]["template"]["spec"]["taskCount"] == 1
    assert value["request"]["spec"]["template"]["spec"]["parallelism"] == 1


def test_ambiguous_POST_is_consumed_before_IO_and_never_retried(monkeypatch):
    b = adapter.Backend(approval(), ROOT)
    calls = []
    monkeypatch.setattr(b, "command", lambda *_: "SYNTHETIC_NOT_A_CREDENTIAL")

    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        raise requests.Timeout("synthetic ambiguous POST")
    monkeypatch.setattr(b.session, "post", fail)
    try:
        with pytest.raises(requests.Timeout):
            b.create(b.prepared["body"])
        with pytest.raises(ValueError):
            b.create(b.prepared["body"])
        assert len(calls) == 1 and calls[0][1]["timeout"] == 30
        assert calls[0][1]["allow_redirects"] is False
    finally:
        b.session.close()
