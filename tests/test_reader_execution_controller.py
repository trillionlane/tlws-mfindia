"""Durability/ambiguity/inspection use a fake provider, no cloud or DB."""
from copy import deepcopy

import pytest

from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review import reader_execution_contract as contract
from mfdataindia.review import reader_execution_controller as controller
from mfdataindia.review import reader_execution_diagnostics as diagnostics
from mfdataindia.review import reader_execution_evidence as evidence
from mfdataindia.review.reader_execution_journal import Journal
from reader_execution_fixtures import plan, authority, app_context, FakeProvider, ROOT, GIT


@pytest.mark.parametrize("boundary", ["create", "execute", "certificate"])
def test_unknown_boundary_consumed_once_and_inspection_submits_zero_mutations(boundary, monkeypatch, tmp_path):
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)
    value, verifier, private = plan()
    adapters = []
    def factory(p, k, repo, prepared):
        adapter = FakeProvider(p, k, repo, prepared)
        adapter.fail_at = boundary
        adapters.append(adapter)
        return adapter
    result = controller.execute(value, authority(value), app_context(value, verifier, private),
        source_root=ROOT, expected_git=GIT, backend_factory=factory, journal_base=tmp_path,
        sleep=lambda _: pytest.fail("no waits"))
    assert result["state"] == "TRANSPORT_OUTCOME_UNCONFIRMED"
    assert result["grant_effect"] == diagnostics.NO_GRANT  # Capture cannot submit an ACL mutation.
    assert "synthetic-secret-marker" not in contract.canonical(result)
    adapter = adapters[0]
    assert adapter.calls.count("create") == 1
    assert adapter.calls.count("execute") == int(boundary != "create")
    with Journal(contract.operation_root(value)) as journal:
        rows = journal.events()
        assert rows[1]["state"] == "CREATE_OUTCOME_UNKNOWN"
        assert rows[1]["data"]["prepared"]["request_sha256"] == adapter.prepared["request_sha256"]
        assert rows[-1]["state"] == "STOPPED_UNRESOLVED"
    # Same operation cannot replay; a different identity cannot evade unresolved work.
    for candidate in (value, {**deepcopy(value), "operation_id": "op-fedcba9876543210"}):
        before = len(adapters)
        with pytest.raises(ValueError, match="PRIOR_OPERATION_REQUIRES_READ_ONLY_INSPECTION"):
            controller.execute(candidate, authority(candidate), app_context(candidate, verifier, private),
                source_root=ROOT, expected_git=GIT, backend_factory=factory, journal_base=tmp_path)
        assert len(adapters) == before
    if boundary == "create":
        # A create response lost before RUN does not magically mean execution.
        inspected = controller.inspect(contract.operation_root(value), source_root=ROOT,
            inspector_factory=lambda *_: adapter, journal_base=tmp_path)
        assert inspected["state"] == "INSPECTED_WITH_KNOWN_EFFECT"
        assert inspected["observed_stage_state"] == "JOB_CREATED_NO_RUN_SUBMISSION"
        assert adapter.calls.count("execute") == 0
    else:
        adapter.fail_at = None
        diagnostic = diagnostics.report(ValueError("synthetic-secret-marker"), stage="capture")
        adapter.result = evidence.certificate(value, "capture", [], state=diagnostic["state"],
            effect=diagnostics.NO_GRANT, data={}, diagnostic=diagnostic)
        before = adapter.calls.count("create"), adapter.calls.count("execute")
        inspected = controller.inspect(contract.operation_root(value), source_root=ROOT,
            inspector_factory=lambda *_: adapter, journal_base=tmp_path)
        assert inspected["state"] == "INSPECTED_WITH_KNOWN_EFFECT"
        assert inspected["observed_stage_state"] == "CAPTURE_FAILED_NO_GRANT"
        assert (adapter.calls.count("create"), adapter.calls.count("execute")) == before
    assert result["consumer_acceptance"] == inspected["consumer_acceptance"] == "PENDING_RO04"


def test_correctable_local_validation_failure_spends_no_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)
    value, verifier, private = plan()
    bad = {**authority(value), "plan_sha256": "f" * 64}
    with pytest.raises(ValueError, match="RO04_DIRECT_BOUNDED_PLAN_AUTHORITY_REQUIRED"):
        controller.execute(value, bad, app_context(value, verifier, private), source_root=ROOT,
            expected_git=GIT, backend_factory=lambda *_: pytest.fail("no provider"), journal_base=tmp_path)
    assert not list(tmp_path.iterdir())
    assert contract.dry_run(value, source_root=ROOT, expected_git=GIT)["dispatches"] == 0


def test_preflight_failure_is_before_identity_allocation(monkeypatch, tmp_path):
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)
    value, verifier, private = plan()
    class Refused(FakeProvider):
        def preflight(self, *_args):
            raise contract.ContractError("CURRENT_TARGET_NOT_EMPTY")
    with pytest.raises(ValueError, match="CURRENT_TARGET_NOT_EMPTY"):
        controller.execute(value, authority(value), app_context(value, verifier, private),
            source_root=ROOT, expected_git=GIT, backend_factory=Refused, journal_base=tmp_path)
    assert not list(tmp_path.glob("mf-reader-internal-*"))


def test_task_failure_never_discards_useful_sealed_capture_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)
    value, verifier, private = plan()
    class Failed(FakeProvider):
        def execution(self, *args):
            result = super().execution(*args)
            result["status"] = {"conditions": [{"type": "Completed", "status": "False"}], "failedCount": 1}
            return result
        def certificate(self, *_args):
            import psycopg
            diagnostic = diagnostics.report(psycopg.errors.InsufficientPrivilege("synthetic-secret-marker"), stage="capture")
            return evidence.certificate(value, "capture", [], state=diagnostic["state"],
                effect=diagnostics.NO_GRANT, data={}, diagnostic=diagnostic)
    result = controller.execute(value, authority(value), app_context(value, verifier, private),
        source_root=ROOT, expected_git=GIT, backend_factory=Failed, journal_base=tmp_path)
    assert result["state"] == "CAPTURE_FAILED_NO_GRANT"
    assert result["safe_cause"] == "PERMISSION_DENIED"
    assert "synthetic-secret-marker" not in contract.canonical(result)


def test_complete_recorded_request_round_trip_and_tamper_rejection():
    value, _, _ = plan()
    prepared = contract.prepare_request(value, "capture", [], source_root=ROOT, expected_git=GIT)
    stored = controller.recorded_request(prepared)
    assert "body" not in stored
    assert controller.restored_request(stored) == prepared
    stored["request"]["metadata"]["name"] = "unrelated-target"
    with pytest.raises(ValueError, match="ORIGINAL_REQUEST_DRIFT"):
        controller.restored_request(stored)


def test_inspection_rederives_whole_fixed_request_not_a_forged_self_hash():
    value, _, _ = plan()
    prepared = contract.prepare_request(value, "capture", [], source_root=ROOT, expected_git=GIT)
    stored = controller.recorded_request(prepared)
    assert controller.inspect_request(value, "capture", [], stored) == prepared
    import hashlib
    stored["request"]["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]["args"] = ["arbitrary.py"]
    raw = contract.canonical(stored["request"]).encode()
    stored.update(request_sha256=hashlib.sha256(raw).hexdigest(), request_bytes=len(raw))
    with pytest.raises(ValueError, match="ORIGINAL_REQUEST_DRIFT"):
        controller.inspect_request(value, "capture", [], stored)
