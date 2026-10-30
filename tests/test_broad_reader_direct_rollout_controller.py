"""Pure fake-backend create-once lifecycle tests; no provider/DB calls."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import hashlib
import json

import pytest

from mfdataindia.review import broad_reader_direct_delivery_controller as prepare
from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review import broad_reader_direct_rollout_controller as rollout
from mfdataindia.review import broad_reader_source_authority as source
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.policy import canonical
_SCRIPT_SPEC = spec_from_file_location(
    "_phasec_operate_broad_reader_direct_dev",
    Path(__file__).resolve().parents[1] / "scripts" / "operate_broad_reader_direct_dev.py")
assert _SCRIPT_SPEC is not None and _SCRIPT_SPEC.loader is not None
operator = module_from_spec(_SCRIPT_SPEC)
_SCRIPT_SPEC.loader.exec_module(operator)


SOURCE = Path(__file__).resolve().parents[1]
GIT = "a" * 40
APP = {"image": spec.IMAGE_PREFIX + "b" * 64,
       "resolved_image": spec.IMAGE_PREFIX + "b" * 64,
       "config_digest": "sha256:" + "c" * 64,
       "git_sha": GIT, "platform": "linux/amd64", "layers_downloaded": 0}
TOOLS = {**APP, "image": APP["image"].replace(
    "/mfdataindia@", "/mfdataindia-review-tools@"),
    "resolved_image": APP["resolved_image"].replace(
        "/mfdataindia@", "/mfdataindia-review-tools@")}


def _approval(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    payload = {"format": "mf-broad-reader-direct-capture-v1",
               "packaged_source": source.verify_packaged_source(SOURCE),
               "git_sha": GIT,
               "image_binding": {"app": deepcopy(APP), "tools": deepcopy(TOOLS)},
               "create_only_intent_sha256": "d" * 64,
               "window": {"starts_at": (now - timedelta(seconds=5)).isoformat(),
                          "ends_at": (now + timedelta(minutes=5)).isoformat(),
                          "exclusive_administration": True,
                          "data_schema_custody": True,
                          "future_owner_provisioning": True}}
    root = tmp_path / (rollout.ROOT_NAMES["capture"] + "abcdef")
    root.mkdir()
    (root / "approval.json").write_text("synthetic-only")
    payload["create_only_intent_sha256"] = spec.intent_preimage(
        "capture", str(root), payload)[1]
    prepared = prepare.prepare_capture(
        payload=payload, root=str(root), final_git_sha=GIT,
        image=APP, source_root=SOURCE)
    protected = {"fixed-protected-profile": True}
    approval = {"format": "mf-broad-reader-direct-delivery-v1",
                "kind": "capture", "operator": spec.OPERATOR,
                "project": spec.PROJECT, "region": spec.REGION,
                "final_git_sha": GIT,
                "image_binding": {"app": deepcopy(APP), "tools": deepcopy(TOOLS)},
                "payload": payload, "root": str(root), "predecessor": None,
                "protected_hash": fingerprint(protected),
                "request_sha256": prepared["request_sha256"]}
    return approval, prepared, protected


class FakeBackend:
    def __init__(self, approval, prepared, protected):
        self.approval = approval
        self.prepared = prepared
        self.protected_state = protected
        self.calls = []
        self.created = False
        self.executed = False
        self.name = spec.ROLES["capture"].job + "-abcde"
        self.created_at = "2026-10-29T22:00:00Z"
        self.executed_at = "2026-10-29T22:01:00Z"

    def preflight(self, approval, prepared):
        assert approval == self.approval and prepared == self.prepared
        self.calls.append("preflight")

    def image(self, kind):
        return self.approval["image_binding"][kind]

    def protected(self):
        self.calls.append("protected")
        return deepcopy(self.protected_state)

    def absent(self, kind):
        assert kind == "capture"
        self.calls.append("absent")
        return not self.created

    def _job(self):
        value = deepcopy(self.prepared["request"])
        value["metadata"].update({
            "namespace": spec.PROJECT_NUMBER, "uid": "a" * 16,
            "creationTimestamp": self.created_at, "generation": 1,
            "labels": {"cloud.googleapis.com/location": spec.REGION},
            "annotations": {"run.googleapis.com/creator": spec.OPERATOR}})
        value["status"] = {"executionCount": 1 if self.executed else 0,
                           "conditions": [{"type": "Ready", "status": "True"}]}
        if self.executed:
            value["status"]["latestCreatedExecution"] = {
                "name": self.name, "creationTimestamp": self.executed_at}
        return value

    def _execution(self):
        return {"apiVersion": "run.googleapis.com/v1", "kind": "Execution",
                "metadata": {"name": self.name, "namespace": spec.PROJECT_NUMBER,
                             "uid": "b" * 16,
                             "creationTimestamp": self.executed_at,
                             "generation": 1,
                             "labels": {
                                 "cloud.googleapis.com/location": spec.REGION,
                                 "run.googleapis.com/job": spec.ROLES["capture"].job,
                                 "run.googleapis.com/jobUid": "a" * 16,
                                 "run.googleapis.com/jobGeneration": "1"},
                             "annotations": {"run.googleapis.com/creator": spec.OPERATOR,
                                             **self.prepared["annotations"]}},
                "spec": deepcopy(self._job()["spec"]["template"]["spec"]),
                "status": {"conditions": [{"type": "Completed", "status": "True"}],
                           "succeededCount": 1}}

    def create(self, kind, body):
        assert kind == "capture" and body == self.prepared["body"]
        assert not self.created
        self.calls.append("create")
        self.created = True
        return self._job()

    def job(self, kind):
        assert kind == "capture" and self.created
        return self._job()

    def executions(self, kind):
        assert kind == "capture"
        return [self._execution()] if self.executed else []

    def execute(self, kind):
        assert kind == "capture" and self.created and not self.executed
        self.calls.append("execute")
        self.executed = True
        return self._execution()

    def execution(self, kind, name):
        assert kind == "capture" and name == self.name
        return self._execution()

    def certificate(self, kind, name):
        assert kind == "capture" and name == self.name
        self.calls.append("certificate")
        before = {"permission_enabled": False}
        return spec.seal_certificate(
            payload_sha256=self.prepared["payload_sha256"],
            stage="direct-capture-readonly",
            evidence={"format": "mf-broad-reader-prewrite-capture-v1-nonexecutable",
                      "grant_authorized": False,
                      "before_snapshot": before,
                      "helper_attestation": {"synthetic": True},
                      "packaged_source": self.approval["payload"]["packaged_source"],
                      "capture_sha256": fingerprint(before)})


def test_exact_single_job_and_execution_are_intent_ordered(monkeypatch, tmp_path):
    monkeypatch.setattr(rollout, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)
    approval, prepared, protected = _approval(tmp_path)
    backend = FakeBackend(approval, prepared, protected)
    records = {}

    def claim(path, value):
        assert path not in records
        records[path] = deepcopy(value)
        if path.name == "create-intent.json":
            assert "create" not in backend.calls
        if path.name == "execute-intent.json":
            assert "execute" not in backend.calls

    completion = rollout.run(approval, backend, source_root=SOURCE,
                             claim=claim, sleep=lambda _: pytest.fail("no wait needed"))
    assert completion["permission_enabled"] is False
    assert completion["ti_verified"] is False
    assert backend.calls.count("create") == backend.calls.count("execute") == 1
    assert backend.calls.count("certificate") == 1
    assert [path.name for path in records][-1] == "result.json"
    assert records[tmp_path / (spec.ROLES["capture"].job +
                               "-controller-start.json")]["kind"] == "capture"
    intent = records[tmp_path / (rollout.ROOT_NAMES["capture"] +
                                 "abcdef") / "create-intent.json"]
    preimage, digest = spec.require_intent("capture", approval["root"], approval["payload"])
    assert intent == {"approval_sha256": fingerprint(approval),
                      "request_sha256": prepared["request_sha256"],
                      "job": spec.ROLES["capture"].job,
                      "root": approval["root"],
                      "intent_preimage": preimage,
                      "intent_sha256": digest,
                      "complete_request": prepared["request"]}


def test_completed_capture_is_required_for_grant_and_exact_snapshot(
        monkeypatch, tmp_path):
    monkeypatch.setattr(rollout, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)
    capture, prepared, protected = _approval(tmp_path)
    root = Path(capture["root"])
    (root / "approval.json").write_bytes((canonical(capture) + "\n").encode())
    backend = FakeBackend(capture, prepared, protected)
    completion = rollout.run(capture, backend, source_root=SOURCE,
                             sleep=lambda _: pytest.fail("no wait needed"))
    grant_root = tmp_path / (rollout.ROOT_NAMES["grant"] + "abcdef")
    grant = deepcopy(capture)
    grant["kind"] = "grant"
    grant["root"] = str(grant_root)
    grant["predecessor"] = {
        "root": str(root), "result_sha256": hashlib.sha256(
            (canonical(completion) + "\n").encode()).hexdigest()}
    grant["payload"] = {
        "packaged_source": capture["payload"]["packaged_source"],
        "git_sha": GIT, "image_binding": capture["image_binding"],
        "window": capture["payload"]["window"],
        "before_snapshot": completion["evidence"]["before_snapshot"],
        "helper_attestation": completion["evidence"]["helper_attestation"]}
    assert rollout.predecessor(grant, source_root=SOURCE)["completion"] == completion
    mixed = deepcopy(grant)
    mixed["payload"]["window"].update(
        exclusive_administration=False, data_schema_custody=False,
        future_owner_provisioning=False,
        dev_no_freeze_risk_accepted=True)
    with pytest.raises(rollout.DeliveryError,
                       match="DIRECT_PREDECESSOR_CUSTODY_MODE_DRIFT"):
        rollout.predecessor(mixed, source_root=SOURCE)
    changed = deepcopy(grant)
    changed["predecessor"]["result_sha256"] = "0" * 64
    with pytest.raises(rollout.DeliveryError,
                       match="UNVERIFIED_DIRECT_PREDECESSOR_COMPLETION"):
        rollout.predecessor(changed, source_root=SOURCE)
    changed = deepcopy(grant)
    changed["payload"]["before_snapshot"] = {"permission_enabled": True}
    with pytest.raises(rollout.DeliveryError,
                       match="GRANT_NOT_DERIVED_FROM_COMPLETED_CAPTURE"):
        rollout.predecessor(changed, source_root=SOURCE)
    copied = tmp_path / (rollout.ROOT_NAMES["capture"] + "ghijkl")
    copied.mkdir()
    for name in ("approval.json", "result.json"):
        (copied / name).write_bytes((root / name).read_bytes())
    changed = deepcopy(grant)
    changed["predecessor"]["root"] = str(copied)
    with pytest.raises(rollout.DeliveryError,
                       match="DIRECT_PREDECESSOR_INTERNAL_ROOT_DRIFT"):
        rollout.predecessor(changed, source_root=SOURCE)


def test_protected_drift_stops_before_create(monkeypatch, tmp_path):
    monkeypatch.setattr(rollout, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)
    approval, prepared, protected = _approval(tmp_path)
    approval["protected_hash"] = "0" * 64
    backend = FakeBackend(approval, prepared, protected)
    records = {}
    with pytest.raises(rollout.DeliveryError, match="PROTECTED_BASELINE_DRIFT"):
        rollout.run(approval, backend, source_root=SOURCE,
                    claim=lambda path, value: records.setdefault(path, value))
    assert "create" not in backend.calls and "execute" not in backend.calls
    assert any(path.name == "failure.json" for path in records)


def test_forged_capture_certificate_never_becomes_a_result(monkeypatch, tmp_path):
    monkeypatch.setattr(rollout, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)
    approval, prepared, protected = _approval(tmp_path)
    backend = FakeBackend(approval, prepared, protected)
    original = backend.certificate

    def forged(kind, name):
        value = original(kind, name)
        value["evidence"]["grant_authorized"] = True
        return value

    backend.certificate = forged
    records = {}
    with pytest.raises(spec.DeliverySpecError, match="DIRECT_CERTIFICATE_HASH_DRIFT"):
        rollout.run(approval, backend, source_root=SOURCE,
                    claim=lambda path, value: records.setdefault(path, value),
                    sleep=lambda _: pytest.fail("no wait needed"))
    assert backend.calls.count("create") == backend.calls.count("execute") == 1
    assert not any(path.name == "result.json" for path in records)
    assert any(path.name == "failure.json" for path in records)


def test_preflight_failure_does_not_start_unapproved_protected_reads(monkeypatch, tmp_path):
    monkeypatch.setattr(rollout, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)
    approval, prepared, protected = _approval(tmp_path)
    backend = FakeBackend(approval, prepared, protected)

    def denied(*_):
        backend.calls.append("preflight")
        raise RuntimeError("EXACT_OPERATOR_UNAVAILABLE")

    backend.preflight = denied
    with pytest.raises(RuntimeError, match="EXACT_OPERATOR_UNAVAILABLE"):
        rollout.run(approval, backend, source_root=SOURCE,
                    claim=lambda *_: None)
    assert backend.calls == ["preflight"]


def test_ambiguous_execution_dominates_failed_protected_final_read(
        monkeypatch, tmp_path):
    monkeypatch.setattr(rollout, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)
    approval, prepared, protected = _approval(tmp_path)
    backend = FakeBackend(approval, prepared, protected)
    original_protected = backend.protected
    records = {}

    def ambiguous(kind):
        backend.calls.append("execute")
        raise TimeoutError("EXECUTE_COMMIT_UNKNOWN_NO_RETRY")

    def protected_then_unavailable():
        if backend.calls.count("execute"):
            raise RuntimeError("PROTECTED_INSPECTION_UNAVAILABLE")
        return original_protected()

    backend.execute = ambiguous
    backend.protected = protected_then_unavailable
    with pytest.raises(TimeoutError, match="EXECUTE_COMMIT_UNKNOWN_NO_RETRY"):
        rollout.run(approval, backend, source_root=SOURCE,
                    claim=lambda path, value: records.setdefault(path, value),
                    sleep=lambda _: pytest.fail("no wait needed"))
    assert backend.calls.count("execute") == 1
    assert any(path.name == "execute-intent.json" for path in records)
    assert any(path.name == "protected-failure.json" for path in records)
    assert not any(path.name == "result.json" for path in records)


def test_operator_loads_only_exact_root_bound_approval(monkeypatch, tmp_path):
    monkeypatch.setattr(rollout, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)
    approval, _, _ = _approval(tmp_path)
    path = Path(approval["root"]) / "approval.json"
    path.write_text(json.dumps(approval))
    assert operator.load_approval(
        path, fingerprint(approval), source_root=SOURCE) == approval
    with pytest.raises(ValueError, match="DIRECT_APPROVAL_PATH_OR_CONTENT_DRIFT"):
        operator.load_approval(path, "0" * 64, source_root=SOURCE)
    duplicate = path.read_text().replace('"kind": "capture"',
                                        '"kind": "capture", "kind": "capture"')
    path.write_text(duplicate)
    with pytest.raises(ValueError, match="DUPLICATE_DIRECT_APPROVAL_MEMBER"):
        operator.load_approval(path, fingerprint(approval), source_root=SOURCE)
