"""Actual workflow Bash guards, synthetic environments and disposable Git only."""

import base64
from copy import deepcopy
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import pytest
import yaml
from ro03_historical_source import previous as ro03_previous

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/rollout-verified-review-dev.yml"
AUTHORITY = ROOT / "docs/IMAGE_PREPARATION_REVISION_GUARD_BASE_AUTHORITY_20261025.json"
PLAN = ROOT / "docs/IMAGE_PREPARATION_REVISION_GUARD_LITERAL_PLAN_20261025.json"
FIRST = "Require exact approved image revision before checkout"
SECOND = "Verify checked-out image revision before tooling and cloud authentication"
GUARDS = (FIRST, SECOND)


def workflow(raw=None):
    return yaml.load(WORKFLOW.read_text() if raw is None else raw, Loader=yaml.BaseLoader)


def guard(label):
    found = [s for s in workflow()["jobs"]["rollout"]["steps"] if s.get("name") == label]
    assert len(found) == 1, "required revision guard is absent or duplicated"
    return found[0]


def environment(expected, actual):
    value = {
        "PATH": os.environ["PATH"],
        "LC_ALL": "C",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GITHUB_SHA": actual,
    }
    if expected is not None:
        value["EXPECTED_GIT_SHA"] = expected
    return value


def run_guard(label, expected, actual, directory):
    result = subprocess.run(
        ["bash", "-c", guard(label)["run"] + "\nprintf IMAGE_STEP_REACHED"],
        cwd=directory,
        env=environment(expected, actual),
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result


@pytest.fixture
def checkout(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("-c", "init.defaultBranch=main", "init", "-q")
    git(
        "-c",
        "user.name=synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "--allow-empty",
        "-qm",
        "synthetic guard fixture",
    )
    return tmp_path, git("rev-parse", "HEAD")


@pytest.mark.parametrize("label", GUARDS)
@pytest.mark.parametrize(
    "expected",
    [
        None,
        "",
        "a" * 39,
        "a" * 41,
        "A" * 40,
        "g" * 40,
        " a" + "a" * 39,
        "a" * 40 + "\n",
        "\n" + "a" * 40,
        "0x" + "a" * 38,
        "０" * 40,
        "a" * 20 + " " + "a" * 19,
        "a" * 20 + "\t" + "a" * 19,
    ],
)
def test_missing_malformed_expected_sha_cannot_reach_image_step(checkout, label, expected):
    directory, actual = checkout
    result = run_guard(label, expected, actual, directory)
    assert result.returncode != 0 and "IMAGE_STEP_REACHED" not in result.stdout


@pytest.mark.parametrize("label", GUARDS)
def test_raced_main_sha_is_denied_before_image_step(checkout, label):
    directory, approved = checkout
    raced = "f" * 40 if approved != "f" * 40 else "e" * 40
    result = run_guard(label, approved, raced, directory)
    assert result.returncode != 0 and "IMAGE_STEP_REACHED" not in result.stdout


@pytest.mark.parametrize("label", GUARDS)
def test_exact_expected_event_and_checkout_sha_reaches_image_step(checkout, label):
    directory, actual = checkout
    result = run_guard(label, actual, actual, directory)
    assert result.returncode == 0 and result.stdout == "IMAGE_STEP_REACHED"


def test_correct_event_but_wrong_actual_checkout_is_denied(checkout):
    directory, actual = checkout
    other = "b" * 40 if actual != "b" * 40 else "c" * 40
    result = run_guard(SECOND, other, other, directory)
    assert result.returncode != 0 and "IMAGE_STEP_REACHED" not in result.stdout


@pytest.mark.parametrize("dirty", ["untracked", "tracked"])
def test_nonclean_checkout_is_denied_before_image_step(checkout, dirty):
    directory, actual = checkout
    (directory / "synthetic-content").write_text("not approved\n")
    if dirty == "tracked":
        subprocess.run(["git", "add", "synthetic-content"], cwd=directory, check=True)
    result = run_guard(SECOND, actual, actual, directory)
    assert result.returncode != 0 and "IMAGE_STEP_REACHED" not in result.stdout


@pytest.mark.parametrize("label", GUARDS)
@pytest.mark.parametrize(
    "attack",
    [
        "$(touch INJECTION_EXECUTED)",
        '";touch INJECTION_EXECUTED;#',
        chr(96) + "touch INJECTION_EXECUTED" + chr(96),
        "a\n touch INJECTION_EXECUTED",
    ],
)
def test_input_remains_data_not_executable_shell_source(checkout, label, attack):
    directory, actual = checkout
    result = run_guard(label, attack, actual, directory)
    assert result.returncode != 0 and "IMAGE_STEP_REACHED" not in result.stdout
    assert not (directory / "INJECTION_EXECUTED").exists()


def simulate_prepare_prefix(document, expected, actual, directory):
    """Only guards run; never checkout/auth/tooling/Docker or a provider."""
    for step in document["jobs"]["rollout"]["steps"]:
        if step.get("name") in GUARDS:
            assert step["if"] == "inputs.operation == 'prepare-image'"
            result = subprocess.run(
                ["bash", "-c", step["run"]],
                cwd=directory,
                env=environment(expected, actual),
                capture_output=True,
                timeout=5,
            )
            if result.returncode:
                return False
        if step.get("name") == "Prepare provenance-labelled immutable image only":
            return True  # A synthetic sentinel, not a build/push.
    raise AssertionError("original image step missing")


def test_original_mutable_ref_race_is_reproduced_and_current_workflow_denies(checkout):
    directory, approved = checkout
    baseline = json.loads(AUTHORITY.read_text())["rollout_workflow"]
    raw = gzip.decompress(base64.b64decode(baseline["gzip_base64"]))
    assert hashlib.sha256(raw).hexdigest() == baseline["sha256"]
    raced = "d" * 40 if approved != "d" * 40 else "e" * 40
    assert simulate_prepare_prefix(workflow(raw.decode()), approved, raced, directory)
    assert not simulate_prepare_prefix(workflow(), approved, raced, directory)


def test_guards_execute_before_checkout_tooling_auth_and_image_side_effects():
    document = workflow()
    steps = document["jobs"]["rollout"]["steps"]
    assert steps[0] == guard(FIRST)
    assert steps[1] == {"uses": "actions/checkout@v4"}
    assert steps[2] == guard(SECOND)
    assert steps[3]["uses"] == "actions/setup-python@v5"
    for label in GUARDS:
        value = guard(label)
        assert value["if"] == "inputs.operation == 'prepare-image'"
        assert value["env"] == {
            "EXPECTED_GIT_SHA": "$" + "{{ inputs.expected_git_sha }}",
            "LC_ALL": "C",
        }
        assert "$" + "{{" not in value["run"]
        assert value["run"].startswith("set -euo pipefail\n")
        assert not re.search(r"(?m)^\s*(?:gcloud|docker|curl|pip|uv)(?:\s|$)", value["run"])
    inputs = document["on"]["workflow_dispatch"]["inputs"]
    assert inputs["expected_git_sha"] == {
        "type": "string",
        "required": "true",
        "description": "Exact current main commit admitted by automatic Verify",
    }
    assert inputs["verify_run_id"]["required"] == "true"
    assert inputs["verify_run_attempt"]["required"] == "true"


def test_entire_old_workflow_restores_exactly_and_execute_mode_is_unchanged():
    authority = json.loads(AUTHORITY.read_text())
    baseline = gzip.decompress(base64.b64decode(authority["rollout_workflow"]["gzip_base64"]))
    plan = json.loads(PLAN.read_text())
    current = ro03_previous(WORKFLOW, WORKFLOW.read_bytes()).decode()
    restored = current
    for item in reversed(plan["insertions"]):
        assert restored.count(item["before"]) == item["count"] == 1
        restored = restored.replace(item["before"], "", 1)
    assert restored.encode() == baseline
    assert hashlib.sha256(baseline).hexdigest() == plan["base_workflow_sha256"]
    old_document = workflow(baseline.decode())
    new_document = deepcopy(workflow(current))
    del new_document["on"]["workflow_dispatch"]["inputs"]["expected_git_sha"]
    new_document["jobs"]["rollout"]["steps"] = [
        s for s in new_document["jobs"]["rollout"]["steps"] if s.get("name") not in GUARDS
    ]
    assert new_document == old_document
    assert all(guard(label)["if"] == "inputs.operation == 'prepare-image'" for label in GUARDS)


def test_every_other_parent_file_is_full_byte_unchanged():
    authority = json.loads(AUTHORITY.read_text())
    assert authority["count"] == len(authority["files"]) == 821
    changed = []
    for path, expected in authority["files"].items():
        from test_reader_permission_capture_preservation import original_parent_bytes
        if hashlib.sha256(original_parent_bytes(path)).hexdigest() != expected:
            changed.append(path)
    assert changed == [
        ".github/workflows/rollout-verified-review-dev.yml",
        "tests/test_reader_access_transport.py",
    ]


def test_prior_preservation_test_restores_entire_original_bytes_exactly():
    authority = json.loads(AUTHORITY.read_text())
    substitution = json.loads(PLAN.read_text())["preservation_test_substitution"]
    from test_reader_permission_capture_preservation import original_parent_bytes
    current = original_parent_bytes(substitution["path"]).decode()
    assert current.count(substitution["new"]) == substitution["count"] == 1
    restored = current.replace(substitution["new"], substitution["old"], 1).encode()
    assert hashlib.sha256(restored).hexdigest() == authority["files"][substitution["path"]]
