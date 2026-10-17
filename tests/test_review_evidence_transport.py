"""Hermetic log transport tests; no cloud or PostgreSQL access."""

import base64
import hashlib
import json
import runpy
import sys
import zlib
from copy import deepcopy
from pathlib import Path

import pytest

from mfdataindia.review import evidence_transport as transport
from mfdataindia.review.rollout import CONNECTION, seal_evidence, verify_evidence
from mfdataindia.review.rollout_cloud import Cloud
from test_verified_review_rollout import manifest

ROOT = Path(__file__).resolve().parents[1]


def certificate():
    return seal_evidence(manifest(), "inspect", {"large": "preserved data\n" * 70000})


def envelope():
    return json.loads(transport.render_evidence(certificate()))


def test_large_certificate_roundtrips_below_log_limit_with_unchanged_seal():
    original = certificate()
    rendered = transport.render_evidence(original)
    assert len(json.dumps(original).encode()) > 102400
    assert len(rendered.encode()) < transport.MAX_LOG_BYTES
    assert rendered == transport.render_evidence(original)
    decoded = transport.decode_evidence(json.loads(rendered))
    assert decoded == original
    assert verify_evidence(manifest(), "inspect", decoded) == original["evidence"]
    with pytest.raises(ValueError, match="binding/hash"):
        verify_evidence(manifest(), "activate", decoded)
    with pytest.raises(ValueError, match="binding/hash"):
        verify_evidence(manifest(git_sha="c" * 40), "inspect", decoded)


@pytest.mark.parametrize("payload", ["jsonPayload", "textPayload"])
@pytest.mark.parametrize("large", [False, True])
def test_legacy_and_compressed_cloud_entries(payload, large):
    doc = certificate() if large else seal_evidence(manifest(), "inspect", {"passed": True})
    rendered = transport.render_evidence(doc)
    value = json.loads(rendered) if payload == "jsonPayload" else rendered
    assert transport.execution_evidence([{payload: value}]) == doc


@pytest.mark.parametrize(
    "change",
    [
        {"raw_sha256": "0" * 64},
        {"raw_bytes": 1},
        {"raw_bytes": True},
        {"raw_bytes": transport.MAX_RAW_BYTES + 1},
        {"codec": "gzip"},
        {"body": "!invalid!"},
        {"body": "a" * (transport.MAX_LOG_BYTES + 1)},
        {"extra": "not allowed"},
    ],
)
def test_malformed_recognized_envelope_never_falls_back_to_another_valid_log(change):
    bad = {**envelope(), **change}
    good = seal_evidence(manifest(), "inspect", {"passed": True})
    with pytest.raises(ValueError):
        transport.execution_evidence([{"jsonPayload": bad}, {"jsonPayload": good}])


@pytest.mark.parametrize(
    "kind", ["truncated", "trailing", "bomb", "duplicate_json_key", "wrong_inner"]
)
def test_complete_single_bounded_stream_and_unambiguous_inner_json_required(kind):
    doc = envelope()
    compressed = base64.b64decode(doc["body"])
    if kind == "truncated":
        compressed = compressed[:-2]
    elif kind == "trailing":
        compressed += zlib.compress(b"another stream")
    else:
        raw = (
            b"x" * (transport.MAX_RAW_BYTES + 1)
            if kind == "bomb"
            else b'{"format":"a","format":"b"}'
            if kind == "duplicate_json_key"
            else b"[]"
        )
        compressed = zlib.compress(raw)
        if kind != "bomb":
            doc.update(raw_bytes=len(raw), raw_sha256=hashlib.sha256(raw).hexdigest())
    doc["body"] = base64.b64encode(compressed).decode()
    with pytest.raises(ValueError):
        transport.decode_evidence(doc)


def test_encoder_rejects_oversized_or_incompressible_evidence():
    for value in (
        "x" * (transport.MAX_RAW_BYTES + 1),
        "".join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(5000)),
    ):
        with pytest.raises(ValueError, match="bound"):
            transport.render_evidence(seal_evidence(manifest(), "inspect", {"large": value}))


def test_zero_or_duplicate_certificates_never_pass():
    legacy = seal_evidence(manifest(), "inspect", {"passed": True})
    for logs in (
        [],
        [{"textPayload": '{"format":'}],
        [{"jsonPayload": legacy}, {"jsonPayload": envelope()}],
    ):
        with pytest.raises(ValueError, match="exactly one"):
            transport.execution_evidence(logs)


def test_actual_owner_cli_emits_one_lossless_bounded_line(monkeypatch, capsys):
    from mfdataindia.enrichment import book_deployment

    original = certificate()
    monkeypatch.setenv("REVIEW_MANIFEST", manifest().model_dump_json())
    monkeypatch.setattr(sys, "argv", ["operate_amc_books_dev.py", "inspect"])
    monkeypatch.setattr(book_deployment, "execute", lambda *a, **k: original)
    runpy.run_path(str(ROOT / "scripts/operate_amc_books_dev.py"), run_name="__main__")
    captured = capsys.readouterr()
    assert captured.err == "" and len(captured.out.encode()) <= transport.MAX_LOG_BYTES + 1
    assert len(captured.out.splitlines()) == 1
    decoded = transport.execution_evidence([{"textPayload": captured.out}])
    assert decoded == original
    verify_evidence(manifest(), "inspect", decoded)


def test_owner_cli_transport_failure_has_no_partial_or_sensitive_stdout(monkeypatch, capsys):
    from mfdataindia.enrichment import book_deployment

    original = seal_evidence(manifest(), "inspect", {"large": "private-fixture" * 700000})
    monkeypatch.setenv("REVIEW_MANIFEST", manifest().model_dump_json())
    monkeypatch.setattr(sys, "argv", ["operate_amc_books_dev.py", "inspect"])
    monkeypatch.setattr(book_deployment, "execute", lambda *a, **k: original)
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(ROOT / "scripts/operate_amc_books_dev.py"), run_name="__main__")
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "AMC book operation failed: ValueError\n"


def execution():
    return {
        "metadata": {
            "name": "fixture-execution",
            "annotations": {
                "run.googleapis.com/cloudsql-instances": CONNECTION,
                "run.googleapis.com/vpc-access-egress": "all-traffic",
                "run.googleapis.com/network-interfaces": json.dumps(
                    [{"network": "tl-dev-vpc", "subnetwork": "tl-dev-mumbai"}]
                ),
            },
        },
        "status": {"conditions": [{"type": "Completed", "status": "True"}], "succeededCount": 1},
        "spec": {
            "taskCount": 1,
            "template": {
                "spec": {
                    "serviceAccountName": "fixture-reader",
                    "maxRetries": 0,
                    "timeoutSeconds": 600,
                    "containers": [
                        {
                            "image": manifest().image,
                            "command": ["python"],
                            "args": ["fixture.py"],
                            "env": [{"name": "DEPLOYMENT_GIT_SHA", "value": manifest().git_sha}],
                        }
                    ],
                }
            },
        },
    }


@pytest.mark.parametrize("drift", [None, "identity", "image", "sha", "failed", "network"])
def test_actual_execution_verifier_keeps_runtime_checks_before_log_decode(drift):
    observed = execution()
    task = observed["spec"]["template"]["spec"]
    if drift == "identity":
        task["serviceAccountName"] = "privileged"
    elif drift == "image":
        task["containers"][0]["image"] = "different-image"
    elif drift == "sha":
        task["containers"][0]["env"][0]["value"] = "f" * 40
    elif drift == "failed":
        observed["status"]["failedCount"] = 1
    elif drift == "network":
        observed["metadata"]["annotations"]["run.googleapis.com/network-interfaces"] = "[]"

    class Boundary(Cloud):
        logs_read = 0

        def command(self, *args, **kwargs):
            if args[:2] == ("logging", "read"):
                self.logs_read += 1
                return [{"jsonPayload": envelope()}]
            assert args[:4] == ("run", "jobs", "executions", "describe")
            return deepcopy(observed)

    boundary = Boundary()
    args = (
        manifest(),
        "fixture-execution",
        "fixture-reader",
        ["fixture.py"],
        {"DEPLOYMENT_GIT_SHA": manifest().git_sha},
        [],
    )
    if drift:
        with pytest.raises(ValueError):
            boundary.inspect_execution(*args)
        assert boundary.logs_read == 0
    else:
        actual, doc = boundary.inspect_execution(*args)
        assert actual == observed and doc == certificate() and boundary.logs_read == 1
