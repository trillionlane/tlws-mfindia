"""No GitHub/provider/DB operations; strict fast-loop and merge admission tests."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "fs13_preflight_fixture", Path(__file__).resolve().parents[1] / "scripts/prepush_fs13.py")
pre = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pre)
SHA = "a" * 40


def fixture():
    inventory = json.loads((Path(__file__).resolve().parents[1]
        / "docs/CI_RISK_POLICY_STEP_INVENTORY_20261026.json").read_text())
    jobs = [{"name": "Plan CI coverage", "status": "completed", "conclusion": "success",
             "steps": [{"name": name, "status": "completed", "conclusion": "success"}
                       for name in ("Classify exact change", "CI plan: full")]}]
    for key, name in (("tests", "PostgreSQL tests, contracts, and audit"),
                      ("verify", "Test, audit, and build")):
        names = [row["label"] for row in inventory[key]] + [
            "Validate CI plan", "Run reduced PR checks", "CI policy admitted: full"]
        jobs.append({"name": name, "status": "completed", "conclusion": "success",
                     "steps": [{"name": label, "status": "completed",
                                "conclusion": "skipped" if label == "Run reduced PR checks" else "success"}
                               for label in names]})
    return {"databaseId": 123, "headSha": SHA, "name": "Verify", "status": "completed",
            "conclusion": "success", "jobs": jobs}


def test_both_complete_jobs_are_required_even_when_images_finish_first():
    doc = fixture()
    assert pre.require_whole_verify(doc, run_id=123, head_sha=SHA)["both_jobs_all_required_steps"] == "PASS"
    for index in (1, 2):
        changed = deepcopy(doc)
        changed["jobs"][index]["status"] = "in_progress"
        with pytest.raises(ValueError):
            pre.require_whole_verify(changed, run_id=123, head_sha=SHA)


@pytest.mark.parametrize("defect", ["run", "sha", "workflow", "running", "failure", "missing",
                                   "duplicate", "job_failed", "job_skipped", "step_failed",
                                   "step_skipped", "step_running", "no_steps"])
def test_no_partial_wrong_or_skipped_run_can_admit_merge(defect):
    d = fixture()
    if defect == "run":
        d["databaseId"] = 124
    elif defect == "sha":
        d["headSha"] = "b" * 40
    elif defect == "workflow":
        d["name"] = "other"
    elif defect == "running":
        d["status"] = "in_progress"
    elif defect == "failure":
        d["conclusion"] = "failure"
    elif defect == "missing":
        d["jobs"].pop()
    elif defect == "duplicate":
        d["jobs"][1] = deepcopy(d["jobs"][0])
    elif defect == "job_failed":
        d["jobs"][0]["conclusion"] = "failure"
    elif defect == "job_skipped":
        d["jobs"][0]["conclusion"] = "skipped"
    elif defect == "step_failed":
        d["jobs"][0]["steps"][0]["conclusion"] = "failure"
    elif defect == "step_skipped":
        d["jobs"][0]["steps"][0]["conclusion"] = "skipped"
    elif defect == "step_running":
        d["jobs"][0]["steps"][0]["status"] = "in_progress"
    else:
        d["jobs"][0]["steps"] = []
    with pytest.raises(ValueError):
        pre.require_whole_verify(d, run_id=123, head_sha=SHA)


def test_fast_and_stable_candidate_modes_preserve_all_static_checks_without_local_pg():
    fast, full = pre.local_checks(), pre.local_checks(full=True)
    assert len(fast) == len(full) == 26
    assert fast[1][1][:7] == ["uv", "run", "pytest", "-q", "-m", "not postgres",
                              "tests/test_certification_input_transport.py"]
    assert full[1][1] == ["uv", "run", "pytest", "-q", "-m", "not postgres"]
    assert fast[:1] + fast[2:] == full[:1] + full[2:]
    assert sum(label.startswith("contract-") for label, _ in fast) == 21
    assert any(argv == ["uv", "run", "pip-audit"] for _, argv in fast)
    forbidden = {"docker", "gcloud", "psql", "pg_restore", "curl"}
    assert not any(forbidden.intersection(argv) for _, argv in fast)


def test_local_failure_stops_before_any_later_command(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(pre.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        pre.run_local()
    assert len(calls) == 1


def test_readonly_ci_inspection_never_merges_or_dispatches(monkeypatch):
    calls = []
    monkeypatch.setattr(pre.subprocess, "run", lambda argv, **kwargs:
        calls.append(argv) or SimpleNamespace(stdout=json.dumps(fixture())))
    result = pre.inspect_verify(123, SHA)
    assert result["read_only"] and not result["merge_or_deployment_started"]
    assert len(calls) == 1 and calls[0][:3] == ["gh", "run", "view"]


def test_malformed_input_rejected_before_network(monkeypatch):
    monkeypatch.setattr(pre.subprocess, "run", lambda *a, **kw: pytest.fail("unexpected network"))
    with pytest.raises(ValueError):
        pre.inspect_verify(123, "not-a-sha")
