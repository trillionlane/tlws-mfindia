"""Execute the actual workflow shell boundaries with disposable Git and tool sentinels.

No Actions runner, cloud authentication, publication, deployment or SQL is run.
Checkout/context/action handling is synthetic; the admitted shell bodies are not.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FIRST = "Require exact approved image revision before checkout"
SECOND = "Verify checked-out image revision before tooling and cloud authentication"


def document(name):
    return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)


def git(directory, *args):
    return subprocess.check_output(["git", *args], cwd=directory, text=True).strip()


def expand(value, context):
    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", lambda match: context[match[1]], value)


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, "-c", "init.defaultBranch=main", "init", "-q")
    git(tmp_path, "config", "user.name", "synthetic")
    git(tmp_path, "config", "user.email", "synthetic@example.invalid")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "release_admission.py").write_bytes((ROOT / "scripts/release_admission.py").read_bytes())
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "trusted workflow source")
    trusted = git(tmp_path, "rev-parse", "HEAD")
    (scripts / "release_admission.py").write_text(
        "import os,pathlib\n"
        "pathlib.Path(os.environ['SENTINELS']).write_text('CANDIDATE_VALIDATOR_EXECUTED')\n"
        "pathlib.Path(os.environ['GITHUB_OUTPUT']).write_text('candidate_sha=' + "
        "os.environ['CANDIDATE_SHA'] + '\\n')\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "unadmitted candidate replaces validator")
    candidate = git(tmp_path, "rev-parse", "HEAD")
    return tmp_path, trusted, candidate


@pytest.mark.parametrize("workflow", ["deploy-dev.yml", "rollout-verified-review-dev.yml"])
def test_candidate_cannot_supply_its_own_admission_or_reach_cloud_boundary(repository, tmp_path,
                                                                         workflow):
    directory, trusted, candidate = repository
    job = document(workflow)["jobs"]["admission"]
    context = {"github.workflow_sha": trusted, "env.CANDIDATE_SHA": candidate,
               "inputs.expected_git_sha": candidate, "inputs.verify_run_id": "0",
               "inputs.verify_run_attempt": "1", "github.token": "synthetic-not-a-token"}
    markers = tmp_path.parent / (workflow + ".sentinels")
    env = dict(os.environ, CANDIDATE_SHA=candidate, VERIFY_RUN_ID="0", VERIFY_RUN_ATTEMPT="1",
               GITHUB_TOKEN="synthetic-not-a-token", GITHUB_WORKFLOW_SHA=trusted,
               RUNNER_TEMP=str(tmp_path.parent), GITHUB_STEP_SUMMARY=str(tmp_path.parent / "summary"),
               GITHUB_OUTPUT=str(tmp_path.parent / "output"), SENTINELS=str(markers))
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
    results = []
    for step in job["steps"]:
        if step.get("uses", "").startswith("actions/checkout@"):
            ref = expand(step["with"]["ref"], context)
            git(directory, "checkout", "--detach", "-q", ref)
        elif "run" in step:
            step_env = dict(env, **{key: expand(value, context)
                                   for key, value in step.get("env", {}).items()})
            result = subprocess.run(["bash", "-eo", "pipefail", "-c", expand(step["run"], context)],
                                    cwd=directory, env=step_env, capture_output=True, timeout=10)
            results.append(result)
            if result.returncode:
                break
    else:
        markers.write_text("CLOUD_AUTH_REACHED")  # Synthetic downstream needs:admission sentinel.
    assert results and results[-1].returncode != 0
    assert b"run ID must be a positive integer" in results[-1].stderr
    assert not markers.exists(), "unadmitted candidate executed or admission allowed cloud authentication"
    assert git(directory, "rev-parse", "HEAD") == trusted
    assert job["permissions"] == {"actions": "read", "contents": "read"}


def rollout_prefix(directory, *, candidate, expected, event, operation, checked_out=None):
    """Run guards from the real workflow, resolving only the job's trusted contexts."""
    workflow = document("rollout-verified-review-dev.yml")
    job = workflow["jobs"]["rollout"]
    context = {"needs.admission.outputs.candidate_sha": candidate,
               "inputs.expected_git_sha": expected, "inputs.operation": operation}
    env = dict(os.environ, GITHUB_SHA=event, RELEASE_CANDIDATE_SHA=candidate,
               EXPECTED_GIT_SHA=expected)
    reached = []
    for step in job["steps"]:
        condition = step.get("if")
        if condition and condition != f"inputs.operation == '{operation}'":
            continue
        if step.get("name") in (FIRST, SECOND):
            step_env = dict(env, **{key: expand(value, context)
                                   for key, value in step.get("env", {}).items()})
            result = subprocess.run(["bash", "-c", step["run"]], cwd=directory,
                                    env=step_env, capture_output=True, timeout=5)
            if result.returncode:
                return False, reached
        elif step.get("uses", "").startswith("actions/checkout@"):
            requested = expand(step.get("with", {}).get("ref", event), context)
            reached.append(("checkout", requested))
            git(directory, "checkout", "--detach", "-q", checked_out or requested)
        elif step.get("uses", "").startswith("actions/setup-python@"):
            reached.append(("tooling", None))
            return True, reached  # Boundary sentinel: never install or authenticate.
        else:
            raise AssertionError("unexpected executable action before the guarded tooling boundary")
    raise AssertionError("tooling boundary missing")


@pytest.mark.parametrize("operation", ["prepare-image", "execute"])
@pytest.mark.parametrize("defect", ["expected", "event", "checkout", "missing", "malformed", "dirty"])
def test_sha_mismatches_fail_before_candidate_tooling_or_cloud_auth(repository, operation, defect):
    directory, trusted, candidate = repository
    expected = trusted if defect == "expected" else candidate
    event = trusted if defect == "event" else candidate
    admitted = "" if defect == "missing" else "Z" * 40 if defect == "malformed" else candidate
    if defect == "dirty":
        (directory / "unadmitted-file").write_text("synthetic")
    passed, reached = rollout_prefix(directory, candidate=admitted, expected=expected, event=event,
                                    operation=operation,
                                    checked_out=trusted if defect == "checkout" else None)
    assert passed is False
    assert not any(label == "tooling" for label, _ in reached)
    if defect in {"expected", "event", "missing", "malformed"}:
        assert reached == []  # Not even candidate checkout is reached.


@pytest.mark.parametrize("operation", ["prepare-image", "execute"])
def test_matching_admitted_event_and_checkout_reaches_tooling(repository, operation):
    directory, _, candidate = repository
    passed, reached = rollout_prefix(directory, candidate=candidate, expected=candidate,
                                    event=candidate, operation=operation)
    assert passed and reached == [("checkout", candidate), ("tooling", None)]
    job = document("rollout-verified-review-dev.yml")["jobs"]["rollout"]
    assert job["env"]["RELEASE_CANDIDATE_SHA"] == "${{ needs.admission.outputs.candidate_sha }}"


def test_manifest_validation_and_execution_receive_admitted_sha(repository, tmp_path):
    directory, _, candidate = repository
    steps = document("rollout-verified-review-dev.yml")["jobs"]["rollout"]["steps"]
    validate = next(s for s in steps if s.get("name", "").startswith("Install locked tooling"))
    execute = next(s for s in steps if s.get("name", "").startswith("Execute bounded"))
    tools = tmp_path / "fake-tools"
    tools.mkdir()
    calls = tmp_path / "calls.jsonl"
    stub = (f"#!{sys.executable}\nimport json,os,sys\n"
            "with open(os.environ['CALLS'], 'a') as stream:\n"
            " stream.write(json.dumps([os.path.basename(sys.argv[0]), *sys.argv[1:]]) + '\\n')\n")
    for name in ("python", "uv", "gcloud"):
        (tools / name).write_text(stub)
        (tools / name).chmod(0o700)
    env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"], CALLS=str(calls),
               OPERATION="execute", MANIFEST_JSON="{}", APPROVED_HASH="synthetic",
               RELEASE_CANDIDATE_SHA=candidate, GITHUB_SHA="f" * 40)
    for step in (validate, execute):
        subprocess.run(["bash", "-c", step["run"]], cwd=directory, env=env, check=True, timeout=10)
    recorded = [json.loads(line) for line in calls.read_text().splitlines()]
    commands = [row for row in recorded if "--git-sha" in row]
    assert len(commands) == 2
    assert {row[row.index("--git-sha") + 1] for row in commands} == {candidate}
    assert {row[4] for row in commands} == {"validate", "execute"}
    image = next(s for s in steps if s.get("name", "").startswith("Prepare provenance"))
    assert "$GITHUB_SHA" not in image["run"]
    assert image["run"].count('VCS_REF="$RELEASE_CANDIDATE_SHA"') == 2


def test_actual_workflow_dependency_and_step_order_prevent_auth_before_admission():
    for name, consumer in (("deploy-dev.yml", "deploy"),
                           ("rollout-verified-review-dev.yml", "rollout")):
        jobs = document(name)["jobs"]
        assert jobs[consumer]["needs"] == "admission"
        source = jobs["admission"]["steps"]
        checkouts = [s for s in source if s.get("uses", "").startswith("actions/checkout@")]
        assert len(checkouts) == 1
        assert checkouts[0]["with"] == {"ref": "${{ github.workflow_sha }}",
                                         "persist-credentials": "false"}
        assert all("google-github-actions" not in s.get("uses", "") for s in source)
        consume = next(s for s in source if s.get("id") == "admit")
        assert "${{" not in consume["run"]  # Caller inputs enter argv as data through env.
        downstream = jobs[consumer]["steps"]
        checkout = next(s for s in downstream if s.get("uses", "").startswith("actions/checkout@"))
        assert checkout["with"]["ref"] == "${{ needs.admission.outputs.candidate_sha }}"
        auth_index = next(i for i, s in enumerate(downstream)
                          if s.get("uses", "").startswith("google-github-actions/auth@"))
        if consumer == "rollout":
            assert [downstream[i].get("name") for i in (0, 2)] == [FIRST, SECOND]
            assert all("if" not in downstream[i] for i in (0, 2))
            validation_index = next(i for i, s in enumerate(downstream)
                                    if s.get("name", "").startswith("Install locked tooling"))
            assert 2 < validation_index < auth_index
        else:
            guard_index = next(i for i, s in enumerate(downstream)
                               if s.get("name") == "Validate immutable deployment contract")
            assert guard_index < auth_index
            assert "${{" not in downstream[guard_index]["run"]
            assert 'test "$(git rev-parse HEAD)" = "$RELEASE_CANDIDATE_SHA"' in downstream[guard_index]["run"]
