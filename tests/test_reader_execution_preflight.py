"""Complete pure candidate validation and genuine CLI/worker boundaries."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import os
import subprocess
import sys

import pytest

from mfdataindia.review import reader_execution_contract as contract
from mfdataindia.review import reader_execution_worker as worker
from mfdataindia.review import reader_execution_application as application
from mfdataindia.review import reader_execution_controller as controller
from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review.policy import canonical
from reader_execution_fixtures import ROOT, GIT, plan, app_context, authority
from reader_execution_fixtures import execution_custody as execution_custody


def test_complete_dry_run_has_no_identity_or_intent_or_database_side_effect(tmp_path, monkeypatch):
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)
    value, verifier, private = plan()
    checked = contract.dry_run(value, source_root=ROOT, expected_git=GIT)
    assert checked["dispatches"] == checked["mutation_intents_written"] == 0
    assert checked["consumer_acceptance"] == "PENDING_RO04"
    assert "database_and_application_readiness" in checked["unverified"]
    assert not list(tmp_path.iterdir())
    assert app_context(value, verifier, private).validate(value)["fresh_database_checks"] == "UNVERIFIED"
    controller.validate_authority(value, authority(value))


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(unknown=True), lambda p: p["target"].update(database="other"),
    lambda p: p.update(reader_profile="mf-review-restricted-read-v1"),
    lambda p: p.update(git_sha="f" * 40), lambda p: p["image_binding"]["app"].update(git_sha="f" * 40),
    lambda p: p["code_binding"]["files"].pop(), lambda p: p["code_binding"]["files"][0].update(sha256="f" * 64),
    lambda p: p["packaged_source"].update(base_files=43), lambda p: p["probe_codes"].update(monthly=True),
    lambda p: p.update(operation_id="mf-reader-broad-capture-261027t"),
    lambda p: p["window"].update(ends_at=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()),
])
def test_invalid_candidate_fails_before_operation_allocation(mutation, tmp_path, monkeypatch):
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)
    value, _, _ = plan()
    mutation(value)
    with pytest.raises(ValueError):
        contract.dry_run(value, source_root=ROOT, expected_git=GIT)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x": 1}',
                               b'{}truncated', b'\xff', b'', b'{}\n\n'])
def test_complete_canonical_payload_rejects_duplicates_partial_or_noncanonical(raw):
    with pytest.raises(ValueError):
        contract.parse_json(raw)


@pytest.mark.parametrize("kind", contract.KINDS)
def test_actual_main_snapshots_os_environ_for_every_stage(kind, monkeypatch, capsys):
    # Complete actual input/environment parser is exercised; predecessor data
    # are deliberately invalid for later stages and must fail BEFORE connect.
    value, _, _ = plan()
    predecessors = [] if kind == "capture" else [{"synthetic-invalid-certificate": True}]
    payload = contract.stage_payload(value, kind, predecessors)
    prepared = spec.request(kind, root=contract.operation_root(value), final_git_sha=GIT,
        image=value["image_binding"]["app"], payload=payload,
        source_sha256=contract.fingerprint(value["packaged_source"]),
        operation_id=value["operation_id"], **contract.package_hashes(ROOT))
    env = {row["name"]: row["value"] for row in prepared["task"]["containers"][0]["env"] if "value" in row}
    name = spec.role_for(kind, operation_id=value["operation_id"]).job
    env.update(MFDATAINDIA_DSN="synthetic-secret-marker", CLOUD_RUN_JOB=name,
        CLOUD_RUN_EXECUTION=name + "-abcde", CLOUD_RUN_TASK_COUNT="1", CLOUD_RUN_TASK_INDEX="0", CLOUD_RUN_TASK_ATTEMPT="0")
    for key in list(os.environ):
        if key.startswith(("BROAD_DIRECT_", "READER_EXECUTION_", "CLOUD_RUN_")):
            monkeypatch.delenv(key)
    for key, val in env.items():
        monkeypatch.setenv(key, val)
    seen = []
    actual = spec.worker_environment
    def observe(*args, **kwargs):
        assert type(args[1]) is dict and args[1] is not os.environ
        seen.append(args[1])
        return actual(*args, **kwargs)
    monkeypatch.setattr(spec, "worker_environment", observe)
    monkeypatch.setattr(worker, "_worker", worker.Worker())
    monkeypatch.setattr(worker.psycopg, "connect", lambda **kw: pytest.fail("invalid candidate connected"))
    with pytest.raises(SystemExit) as stopped:
        worker.main(source_root=ROOT)
    assert stopped.value.code == 1 and len(seen) == 1
    output = capsys.readouterr()
    assert "synthetic-secret-marker" not in output.out + output.err
    assert "EXCLUSIVE_WORKER_CONTEXT_REQUIRED" not in output.out + output.err


def test_actual_packaged_script_default_invokes_real_worker_boundary(monkeypatch):
    found = importlib.util.spec_from_file_location("_ro02_packaged_main", ROOT / "scripts/reader_execution_dev.py")
    module = importlib.util.module_from_spec(found)
    found.loader.exec_module(module)
    called = []
    monkeypatch.setattr(worker, "main", lambda **kw: called.append(kw["source_root"]))
    monkeypatch.setattr(module.sys, "argv", ["reader_execution_dev.py"])
    module.main()
    assert called == [ROOT]


def test_app_trust_and_assertion_not_a_profile_elevation_flag():
    value, verifier, private = plan()
    context = app_context(value, verifier, private)
    assert context.validate(value)
    changed = deepcopy(value)
    changed["application_trust_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="APPLICATION_TRUST_BINDING_MISMATCH"):
        context.validate(changed)
    assert "synthetic-private-marker" not in canonical(application.public_trust(verifier))


def test_mixed_operation_and_wrong_predecessor_cannot_become_a_request():
    value, _, _ = plan()
    payload = contract.stage_payload(value, "capture", [])
    payload["plan"]["operation_id"] = "op-fedcba9876543210"
    with pytest.raises(ValueError):
        contract.validate_stage(payload, source_root=ROOT, expected_git=GIT)
    with pytest.raises(ValueError):
        contract.prepare_request(value, "grant", [{"spent": True}], source_root=ROOT, expected_git=GIT)


def test_window_expiry_is_inspectable_but_never_an_execution_input():
    value, _, _ = plan()
    value["window"].update(starts_at="2026-01-01T00:00:00+00:00", ends_at="2026-01-01T00:10:00+00:00")
    assert contract.validate_plan(value, source_root=ROOT, expected_git=GIT, inspect_only=True) == value
    with pytest.raises(ValueError, match="OPERATION_WINDOW_EXPIRED"):
        contract.validate_plan(value, source_root=ROOT, expected_git=GIT)


def test_worker_is_one_use_even_when_validation_failed():
    value = worker.Worker()
    result = value.run({}, source_root=ROOT, connect=lambda **kw: pytest.fail("no connect"))
    assert result.exit_code == 1
    with pytest.raises(ValueError, match="CONSUMED_READER_EXECUTION_WORKER"):
        value.run({}, source_root=ROOT)


def test_invalid_command_line_does_not_echo_accidental_secret_arguments():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/reader_execution_dev.py"),
        "validate", "--unknown", "SYNTHETIC_SECRET_DO_NOT_EXPORT"], capture_output=True, text=True)
    assert result.returncode == 1
    assert "SYNTHETIC_SECRET" not in result.stdout + result.stderr
    assert contract.parse_json(result.stderr.encode())["state"] == "LOCAL_VALIDATION_FAILED_NO_DISPATCH"


def test_reader_unverified_grant_predecessor_never_claims_no_grant_before_connect():
    from mfdataindia.review import reader_execution_diagnostics as diagnostics
    value, _, _ = plan()
    payload = contract.stage_payload(value, "reader", [{"synthetic": "capture"}, {"synthetic": "grant"}])
    prepared = spec.request("reader", root=contract.operation_root(value), final_git_sha=GIT,
        image=value["image_binding"]["app"], payload=payload,
        source_sha256=contract.fingerprint(value["packaged_source"]),
        operation_id=value["operation_id"], **contract.package_hashes(ROOT))
    env = {row["name"]: row["value"] for row in prepared["task"]["containers"][0]["env"] if "value" in row}
    name = spec.role_for("reader", operation_id=value["operation_id"]).job
    env.update(MFDATAINDIA_DSN="synthetic-secret-marker", CLOUD_RUN_JOB=name,
        CLOUD_RUN_EXECUTION=name + "-abcde", CLOUD_RUN_TASK_COUNT="1", CLOUD_RUN_TASK_INDEX="0", CLOUD_RUN_TASK_ATTEMPT="0")
    result = worker.Worker().run(env, source_root=ROOT,
        connect=lambda **kw: pytest.fail("invalid predecessor connected"))
    assert result.exit_code == 1 and result.certificate is not None
    assert result.diagnostic["grant_effect"] == diagnostics.UNKNOWN
    assert "synthetic-secret-marker" not in canonical(result.diagnostic)
