"""Whole request/profile and actual-capture denial gates; provider-free only."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from mfdataindia.review import broad_reader_capture_controller as cc
from mfdataindia.review import broad_reader_transport_controller as core
from mfdataindia.review import broad_reader_transport_backend as backend
from mfdataindia.review import broad_reader_transport_spec as spec
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.rollout import seal_evidence
from broad_reader_fixtures import capture_completion, approval, payload, worker, ROOT


@pytest.mark.parametrize(
    "defect",
    [
        "uid",
        "generation",
        "parent",
        "extra_env",
        "secret",
        "argv",
        "volume",
        "retry",
        "namespace",
        "stage",
        "root",
    ],
)
def test_capture_completion_cannot_drop_or_relabel_provider_profile(defect):
    c = capture_completion()
    if defect == "uid":
        c["job"]["metadata"]["uid"] = "33333333-3333-4333-8333-333333333333"
    elif defect == "generation":
        c["job"]["metadata"]["generation"] = 2
    elif defect == "parent":
        c["execution_document"]["metadata"]["labels"]["run.googleapis.com/jobUid"] = (
            "33333333-3333-4333-8333-333333333333"
        )
    elif defect == "namespace":
        c["namespace"] = "000000000000"
    elif defect == "stage":
        c["certificate"]["stage"] = "old-narrow-capture"
    elif defect == "root":
        c["approval"]["root"] = "/tmp/other"
    else:
        t = c["execution_document"]["spec"]["template"]["spec"]
        if defect == "extra_env":
            t["containers"][0]["env"].append({"name": "OTHER", "value": "bad"})
        elif defect == "secret":
            next(r for r in t["containers"][0]["env"] if r["name"] == "MFDATAINDIA_DSN")[
                "valueFrom"
            ]["secretKeyRef"]["key"] = "latest"
        elif defect == "argv":
            t["containers"][0]["args"] = ["other.py"]
        elif defect == "volume":
            t["volumes"] = [{"name": "bad"}]
        elif defect == "retry":
            t["maxRetries"] = 1
    with pytest.raises(ValueError):
        cc.validate_completion(c)


@pytest.mark.parametrize(
    "field",
    [
        "payload_sha256",
        "request_sha256",
        "approval_sha256",
        "materialized_unlocked",
        "capture_completion_sha256",
        "worker_binding",
    ],
)
def test_exact_write_terminal_binds_all_inputs_and_materialized_caveat(field):
    a = approval()
    w = worker()
    result = w.expected_result(a.payload, {"synthetic_binding": True})
    binding = {
        "packaged_program_sha256": core.WORKER_SHA,
        "packaged_spec_sha256": core.SPEC_SHA,
        "packaged_adapter_sha256": core.ADAPTER_SHA,
        "packaged_controller_sha256": a.controller_sha256,
        "packaged_engine_sha256": core.ENGINE_SHA,
        "profile_sha256": core.PROFILE_SHA,
        "packaged_capture_controller_sha256": spec.CAPTURE_CONTROLLER_SHA,
        "manifest_image": a.manifest.image,
        "manifest_git_sha": a.manifest.git_sha,
    }
    result["worker_binding"] = binding
    assert core.validate_result(a, seal_evidence(a.manifest, core.STAGE, result)) == result
    del result[field]
    with pytest.raises(ValueError):
        core.validate_result(a, seal_evidence(a.manifest, core.STAGE, result))


@pytest.mark.parametrize(
    "defect", ["extra_env", "secret", "volume", "cpu", "retry", "argv", "account"]
)
def test_write_request_keeps_whole_fixed_capability_profile(defect):
    a = approval()
    prepared = core.inputs(a)
    actual = deepcopy(prepared["task"])
    if defect == "extra_env":
        actual["containers"][0]["env"].append({"name": "OTHER", "value": "bad"})
    elif defect == "secret":
        actual["containers"][0]["env"][-1]["valueFrom"]["secretKeyRef"]["key"] = "latest"
    elif defect == "volume":
        actual["volumes"] = [{"name": "bad"}]
    elif defect == "cpu":
        actual["containers"][0]["resources"]["limits"]["cpu"] = "2"
    elif defect == "retry":
        actual["maxRetries"] = 1
    elif defect == "argv":
        actual["containers"][0]["args"] = ["other.py"]
    else:
        actual["serviceAccountName"] = "another-owner"
    with pytest.raises(ValueError):
        core.validate_task(actual, prepared["task"])


@pytest.mark.parametrize("defect", ["sibling", "job_uid", "execution_uid", "certificate", "config"])
def test_actual_capture_source_must_match_before_any_write_job_POST(monkeypatch, defect):
    a = approval()
    b = backend.Backend(a, ROOT)
    c = a.payload["grant_proposal"]["capture_completion"]
    b._image_resolution = deepcopy(c["image_resolution"])
    b._namespace = c["namespace"]
    job, execution, cert = (
        deepcopy(c["job"]),
        deepcopy(c["execution_document"]),
        deepcopy(c["certificate"]),
    )
    if defect == "job_uid":
        job["metadata"]["uid"] = "33333333-3333-4333-8333-333333333333"
    elif defect == "execution_uid":
        execution["metadata"]["uid"] = "33333333-3333-4333-8333-333333333333"
    elif defect == "certificate":
        cert["evidence"]["candidate_semantics"] = "FAKE"
    elif defect == "config":
        b._image_resolution["config_digest"] = "sha256:" + "0" * 64

    def metadata(kind, name=None):
        if kind == "capture-job":
            return job
        if kind == "capture-executions":
            return [execution, deepcopy(execution)] if defect == "sibling" else [execution]
        if kind == "capture-execution":
            return execution
        return [{"jsonPayload": cert}]

    monkeypatch.setattr(b, "metadata", metadata)
    with pytest.raises(ValueError):
        b.verify_capture_source()
    assert not b.created and not b.executed and b.sent_urls == set()
    b.session.close()


def test_POST_expiry_after_token_is_consumed_once_and_never_sent(monkeypatch):
    a = approval()
    b = backend.Backend(a, ROOT)
    calls = []

    def live(*args, **kwargs):
        calls.append("window")
        if len(calls) == 2:
            raise ValueError("synthetic expired")

    monkeypatch.setattr(spec, "live_window", live)
    monkeypatch.setattr(b, "command", lambda *a: "SYNTHETIC_TOKEN")
    monkeypatch.setattr(b.session, "post", lambda *a, **k: pytest.fail("expired request sent"))
    with pytest.raises(ValueError):
        b.create(b.prepared["body"])
    with pytest.raises(ValueError):
        b.create(b.prepared["body"])
    assert b.created and len(b.sent_urls) == 1 and calls == ["window", "window"]
    b.session.close()


def test_finite_approval_has_no_request_or_final_approval_hash_inside_payload():
    p = payload()
    a = core.approval_document(p)
    assert set(a) == {"payload", "request_sha256"}
    assert not {"request_sha256", "approval_sha256"} & set(p)
    request = core.inputs(core.Approval.model_validate(a))
    assert json.loads(request["body"]) == request["request"]
    assert (
        request["task"]["serviceAccountName"]
        == "tlws-mf-data-migrate-dev@trillionlane-dev.iam.gserviceaccount.com"
    )
    assert request["task"]["timeoutSeconds"] == "600"
    assert request["task"]["maxRetries"] == 0
    assert fingerprint(a) != a["request_sha256"]


def test_every_new_source_pin_and_frozen_policy_profile_matches_actual_full_bytes():
    import hashlib
    from mfdataindia.review import broad_reader_capture_spec as cs

    assert core.WORKER_SHA == hashlib.sha256((ROOT / core.WORKER).read_bytes()).hexdigest()
    assert core.SPEC_SHA == hashlib.sha256(Path(spec.__file__).read_bytes()).hexdigest()
    assert cc.SPEC_SHA == hashlib.sha256(Path(cs.__file__).read_bytes()).hexdigest()
    assert spec.CAPTURE_CONTROLLER_SHA == hashlib.sha256(Path(cc.__file__).read_bytes()).hexdigest()
    for path, sha in cs.SOURCE_HASHES.items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == sha


def test_capture_source_namespace_must_equal_fresh_project_number(monkeypatch):
    a = approval()
    b = backend.Backend(a, ROOT)
    c = a.payload["grant_proposal"]["capture_completion"]
    b._namespace = "999999999999"
    b._image_resolution = deepcopy(c["image_resolution"])
    monkeypatch.setattr(
        b,
        "metadata",
        lambda kind, name=None: deepcopy(
            c["job"]
            if kind == "capture-job"
            else [c["execution_document"]]
            if kind == "capture-executions"
            else c["execution_document"]
            if kind == "capture-execution"
            else [{"jsonPayload": c["certificate"]}]
        ),
    )
    with pytest.raises(ValueError, match="capture namespace"):
        b.verify_capture_source()
    assert not b.created and not b.executed and b.sent_urls == set()
    b.session.close()
