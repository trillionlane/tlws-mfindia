"""Behavior-level RO-03 aggregation and release-consumer boundaries."""
from copy import deepcopy
import importlib.util
import io
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "release_admission_under_test", ROOT / "scripts/release_admission.py")
admission = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admission)
SHA = "a" * 40


def plan(mode="full", event="push", *, checkout=SHA):
    return {"version": 1, "event": event, "checkout": checkout, "head": SHA,
            "base": None if event != "pull_request" else "b" * 40,
            "paths": [] if event != "pull_request" else ["README.md"], "mode": mode}


def context(event="push", *, workflow_sha=SHA):
    ref = "refs/heads/main" if event != "pull_request" else "refs/pull/9/merge"
    return {"repository": admission.REPOSITORY, "repository_id": admission.REPOSITORY_ID,
            "workflow": admission.WORKFLOW_NAME,
            "workflow_ref": f"{admission.REPOSITORY}/{admission.WORKFLOW_PATH}@{ref}",
            "workflow_sha": workflow_sha, "run_id": 123, "run_attempt": 2,
            "event": event, "ref": ref, "candidate_sha": SHA}


def core_jobs():
    inventory = json.loads((ROOT / "docs/CI_RISK_POLICY_STEP_INVENTORY_20261026.json").read_text())
    jobs = [{"name": admission.CORE_JOBS["plan"], "status": "completed",
             "conclusion": "success", "run_attempt": 2, "head_sha": SHA,
             "steps": [{"name": name, "status": "completed", "conclusion": "success"}
                       for name in ("Classify exact change", "CI plan: full")]}]
    for key in ("tests", "verify"):
        labels = [row["label"] for row in inventory[key]]
        labels += ["Validate CI plan", "Run reduced PR checks", "CI policy admitted: full"]
        jobs.append({"name": admission.CORE_JOBS[key], "status": "completed",
                     "conclusion": "success", "run_attempt": 2, "head_sha": SHA,
                     "steps": [{"name": label, "status": "completed",
                                "conclusion": "skipped" if label == "Run reduced PR checks"
                                else "success"} for label in labels]})
    return jobs


def packet_archive(packet):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("release-admission.json", json.dumps(packet))
    return stream.getvalue()


class FakeAPI:
    def __init__(self, *, packet=None):
        self.run = {"id": 123, "run_attempt": 2, "name": admission.WORKFLOW_NAME,
                    "path": admission.WORKFLOW_PATH, "workflow_id": 99,
                    "repository": {"full_name": admission.REPOSITORY},
                    "head_repository": {"full_name": admission.REPOSITORY},
                    "event": "push", "head_branch": "main", "head_sha": SHA,
                    "status": "completed", "conclusion": "success"}
        aggregate = {"name": admission.AGGREGATE_JOB, "status": "completed",
                     "conclusion": "success", "run_attempt": 2, "head_sha": SHA,
                     "steps": [{"name": "Build exact release-admission decision",
                                "status": "completed", "conclusion": "success"}]}
        self.jobs = core_jobs() + [aggregate]
        self.packet = packet or admission.build_packet(
            plan(), context(), {"plan": "success", "tests": "success", "verify": "success"})
        self.artifact = {"id": 456, "name": "release-admission-123-2", "expired": False,
                         "archive_download_url": "https://example.invalid/admission.zip",
                         "workflow_run": {"id": 123, "head_sha": SHA}}
        self.main_sha = SHA
        self.calls = []

    def json(self, path):
        self.calls.append(("json", path))
        if path.endswith("/actions/runs/123"):
            return deepcopy(self.run)
        if "/attempts/2/jobs" in path:
            return {"total_count": len(self.jobs), "jobs": deepcopy(self.jobs)}
        if path.endswith("/actions/runs/123/artifacts?per_page=100"):
            return {"total_count": 1, "artifacts": [deepcopy(self.artifact)]}
        if path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": self.main_sha}}
        raise AssertionError(path)

    def bytes(self, path):
        self.calls.append(("bytes", path))
        assert path == self.artifact["archive_download_url"]
        return packet_archive(self.packet)


def test_complete_correct_full_main_run_is_release_candidate_and_live_state_stays_pending():
    packet = admission.build_packet(
        plan(), context(), {"plan": "success", "tests": "success", "verify": "success"})
    assert packet["source_verified"] and packet["release_candidate_eligible"]
    assert packet["verification_scope"] == "MAIN_RELEASE_CANDIDATE_FULL"
    assert packet["accepted_exclusions"] == packet["blocking_reasons"] == []
    assert packet["permission_enabled"] is packet["deployment_performed"] is False
    assert packet["consumer_acceptance"] == "PENDING_RO04"
    result = admission.consume(FakeAPI(packet=packet), repository=admission.REPOSITORY,
                               run_id=123, run_attempt=2, candidate_sha=SHA)
    assert result["release_admitted"] and not result["permission_enabled"]
    assert result["consumer_acceptance"] == "PENDING_RO04"


@pytest.mark.parametrize("mode", ["docs", "targeted", "full"])
def test_pr_scoped_verification_can_pass_but_never_become_release_eligibility(mode):
    packet = admission.build_packet(
        plan(mode, "pull_request"), context("pull_request"),
        {"plan": "success", "tests": "success", "verify": "success"})
    assert packet["source_verified"] and not packet["release_candidate_eligible"]
    assert packet["verification_scope"] == f"SOURCE_VERIFIED_{mode.upper()}"
    assert bool(packet["accepted_exclusions"]) is (mode != "full")


def test_pr_candidate_and_merge_checkout_are_bound_separately_and_admitted():
    merge_checkout = "c" * 40
    packet = admission.build_packet(
        plan("full", "pull_request", checkout=merge_checkout),
        context("pull_request", workflow_sha=merge_checkout),
        {"plan": "success", "tests": "success", "verify": "success"})
    assert packet["head_sha"] == packet["candidate_sha"] == SHA
    assert packet["checkout_sha"] == packet["workflow"]["sha"] == merge_checkout
    assert packet["source_verified"] is True
    assert packet["release_candidate_eligible"] is False
    assert packet["verification_scope"] == "SOURCE_VERIFIED_FULL"


def test_automatic_main_checkout_must_equal_candidate_head():
    wrong_checkout = "c" * 40
    packet = admission.build_packet(
        plan(checkout=wrong_checkout), context(workflow_sha=wrong_checkout),
        {"plan": "success", "tests": "success", "verify": "success"})
    assert packet["source_verified"] is False
    assert packet["release_candidate_eligible"] is False
    assert packet["verification_scope"] == "REJECTED"
    assert packet["blocking_reasons"] == [
        "automatic main checkout does not equal candidate head"]


@pytest.mark.parametrize("result", [None, "failure", "skipped", "cancelled", "timed_out"])
@pytest.mark.parametrize("job", ["plan", "tests", "verify"])
def test_any_required_job_missing_or_unsuccessful_rejects_aggregation(job, result):
    results = {"plan": "success", "tests": "success", "verify": "success"}
    results[job] = result
    packet = admission.build_packet(plan(), context(), results)
    assert not packet["source_verified"] and not packet["release_candidate_eligible"]
    assert packet["verification_scope"] == "REJECTED"
    assert any(admission.CORE_JOBS[job] in reason for reason in packet["blocking_reasons"])


@pytest.mark.parametrize("defect", [
    "repo", "workflow", "head", "event", "running", "failure", "job_failure",
    "job_skipped", "job_cancelled", "job_timed_out", "missing_job", "attempt",
    "artifact_run", "packet_run", "packet_checkout", "packet_base", "packet_workflow_ref",
    "packet_workflow_sha", "stale_main",
])
def test_wrong_stale_mixed_or_incomplete_provider_evidence_rejected(defect):
    api = FakeAPI()
    if defect == "repo":
        api.run["repository"]["full_name"] = "wrong/repo"
    elif defect == "workflow":
        api.run["path"] = ".github/workflows/other.yml"
    elif defect == "head":
        api.run["head_sha"] = "b" * 40
    elif defect == "event":
        api.run["event"] = "workflow_dispatch"
    elif defect == "running":
        api.run["status"] = "in_progress"
    elif defect == "failure":
        api.run["conclusion"] = "failure"
    elif defect.startswith("job_"):
        api.jobs[1]["conclusion"] = defect.removeprefix("job_")
    elif defect == "missing_job":
        api.jobs.pop()
    elif defect == "attempt":
        api.jobs[0]["run_attempt"] = 1
    elif defect == "artifact_run":
        api.artifact["workflow_run"]["id"] = 999
    elif defect == "packet_run":
        api.packet["run"]["id"] = 999
        api.packet["packet_sha256"] = admission.digest(
            {key: value for key, value in api.packet.items() if key != "packet_sha256"})
    elif defect in {"packet_checkout", "packet_base"}:
        key = "checkout" if defect == "packet_checkout" else "base"
        api.packet["ci_plan"][key] = "b" * 40
        api.packet["ci_plan_binding"] = admission.digest(api.packet["ci_plan"])
        api.packet["packet_sha256"] = admission.digest(
            {k: v for k, v in api.packet.items() if k != "packet_sha256"})
    elif defect in {"packet_workflow_ref", "packet_workflow_sha"}:
        key = "ref" if defect == "packet_workflow_ref" else "sha"
        api.packet["workflow"][key] = "wrong" if key == "ref" else "b" * 40
        api.packet["packet_sha256"] = admission.digest(
            {k: v for k, v in api.packet.items() if k != "packet_sha256"})
    elif defect == "stale_main":
        api.main_sha = "b" * 40
    with pytest.raises(admission.AdmissionError):
        admission.consume(api, repository=admission.REPOSITORY,
                          run_id=123, run_attempt=2, candidate_sha=SHA)


def test_partial_rerun_cannot_launder_success_from_an_older_attempt():
    api = FakeAPI()
    api.jobs = [api.jobs[1], api.jobs[-1]]
    with pytest.raises(admission.AdmissionError, match="complete distinct job set"):
        admission.consume(api, repository=admission.REPOSITORY,
                          run_id=123, run_attempt=2, candidate_sha=SHA)


def test_rejected_consumer_evidence_causes_zero_release_mutations():
    called = []
    with pytest.raises(admission.AdmissionError):
        admission.guarded_mutation({"release_admitted": False}, lambda value: called.append(value))
    assert called == []


def test_current_release_workflows_gate_cloud_mutations_behind_admission_job():
    for name in ("deploy-dev.yml", "rollout-verified-review-dev.yml"):
        text = (ROOT / ".github/workflows" / name).read_text()
        admission_job, mutation_job = text.split("  admission:\n", 1)[1].split("  deploy:\n", 1) \
            if name == "deploy-dev.yml" else \
            text.split("  admission:\n", 1)[1].split("  rollout:\n", 1)
        assert "scripts/release_admission.py consume" in admission_job
        assert "id-token: write" not in admission_job
        assert "needs: admission" in mutation_job
        assert "id-token: write" in mutation_job
        assert "google-github-actions/auth" not in admission_job


def test_runtime_dependency_workflow_and_unknown_changes_remain_full_coverage():
    policy = admission._policy()
    for changed in (["src/mfdataindia/review/reader_execution_worker.py"],
                    ["uv.lock"], [".github/workflows/deploy-dev.yml"],
                    ["scripts/release_admission.py"], ["unknown/new.file"]):
        assert policy.classify(changed) == "full"


def test_ro03_current_authority_preserves_every_whole_predecessor_and_rejects_tamper():
    import ro03_historical_source as history
    rows = history.authority()
    assert set(rows) == {
        ".github/workflows/deploy-dev.yml", ".github/workflows/rollout-verified-review-dev.yml",
        ".github/workflows/verify.yml", "scripts/prepush_fs13.py",
        "tests/ro02_historical_source.py",
        "tests/test_ci_risk_policy.py", "tests/test_image_preparation_revision_guard.py",
        "tests/test_prepush_fs13.py", "tests/test_reader_execution_source.py",
    }
    for name, row in rows.items():
        current = (ROOT / name).read_bytes()
        assert hashlib.sha256(current).hexdigest() == row["current_sha256"]
        previous = history.previous(name, current)
        assert hashlib.sha256(previous).hexdigest() == row["previous_sha256"]
        with pytest.raises(AssertionError):
            history.previous(name, current + b"\n# unreviewed mutation\n")
