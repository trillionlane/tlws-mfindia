from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/private_read_invokers_dev.sh"
MEMBERS = [
    "serviceAccount:tlws-mf-data-runtime-dev@trillionlane-dev.iam.gserviceaccount.com",
    "serviceAccount:trillion-insights-runtime-dev@trillionlane-dev.iam.gserviceaccount.com",
    "serviceAccount:tl-dwf-dev-runtime@trillionlane-dev.iam.gserviceaccount.com",
]


def _scenario(members=None):
    return {
        "service": {
            "metadata": {
                "name": "tlws-mf-data-dev",
                "annotations": {"run.googleapis.com/ingress": "internal"},
            },
            "spec": {"template": {"spec": {"serviceAccountName": MEMBERS[0].split(":", 1)[1]}}},
        },
        "policy": {
            "bindings": [
                {"role": "roles/run.invoker", "members": MEMBERS if members is None else members}
            ]
        },
    }


def _run(tmp_path, scenario, mode, script=None):
    state = tmp_path / "state.json"
    state.write_text(json.dumps(scenario))
    calls = tmp_path / "calls.jsonl"
    calls.write_text("")
    gcloud = tmp_path / "gcloud"
    gcloud.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
state_path = Path(os.environ['MOCK_STATE'])
state = json.loads(state_path.read_text())
with Path(os.environ['MOCK_CALLS']).open('a') as log:
    log.write(json.dumps(args)+'\\n')
if '--project=trillionlane-dev' not in args or '--region=asia-south1' not in args:
    raise SystemExit('unexpected cloud target')
prefix = ' '.join(args[:3])
if state.get('fail') == prefix:
    raise SystemExit('PERMISSION_DENIED or unavailable API')
if prefix == 'run services list':
    assert '--filter=metadata.name=tlws-mf-data-dev' in args
    print(json.dumps(state.get('list', [] if state.get('absent') else [state['service']])))
elif prefix == 'run services describe':
    assert args[3] == 'tlws-mf-data-dev'
    print(json.dumps(state['service']))
elif prefix == 'run services get-iam-policy':
    assert args[3] == 'tlws-mf-data-dev'
    print(json.dumps(state['policy']))
elif prefix == 'run services add-iam-policy-binding':
    assert args[3] == 'tlws-mf-data-dev'
    assert '--role=roles/run.invoker' in args and '--condition=None' in args
    member = next(a.removeprefix('--member=') for a in args if a.startswith('--member='))
    if not state.get('ignore_grants'):
        bindings = state['policy'].setdefault('bindings', [])
        binding = next((b for b in bindings if b['role']=='roles/run.invoker'), None)
        if binding is None:
            binding = {'role':'roles/run.invoker','members':[]}
            bindings.append(binding)
        if member not in binding['members']:
            binding['members'].append(member)
        state_path.write_text(json.dumps(state))
    print('{}')
else:
    raise SystemExit('unexpected mutation or command')
""")
    gcloud.chmod(0o755)
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "MOCK_STATE": str(state),
        "MOCK_CALLS": str(calls),
        # Caller-controlled configuration must not enlarge or retarget the contract.
        "PROJECT_ID": "unapproved-project",
        "TDW_RUNTIME_SERVICE_ACCOUNT": "unapproved@other.iam.gserviceaccount.com",
    }
    result = subprocess.run(
        ["bash", "-c", script] if script else ["bash", str(SCRIPT), mode],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result, [json.loads(line) for line in calls.read_text().splitlines()]


@pytest.mark.parametrize("mode", ["preflight", "required", "reconcile"])
def test_exact_three_private_callers_are_accepted(tmp_path, mode):
    result, calls = _run(tmp_path, _scenario(), mode)
    assert result.returncode == 0, result.stderr
    grants = [c for c in calls if c[2] == "add-iam-policy-binding"]
    assert len(grants) == (3 if mode == "reconcile" else 0)
    if grants:
        assert {next(a for a in c if a.startswith("--member=")) for c in grants} == {
            "--member=" + m for m in MEMBERS
        }


@pytest.mark.parametrize("missing", MEMBERS)
@pytest.mark.parametrize("mode", ["preflight", "required", "reconcile"])
def test_missing_approved_grants_are_repairable_but_not_certified(tmp_path, missing, mode):
    result, _ = _run(tmp_path, _scenario([m for m in MEMBERS if m != missing]), mode)
    assert (result.returncode == 0) is (mode != "required"), result.stderr


@pytest.mark.parametrize(
    "extra",
    [
        "allUsers",
        "allAuthenticatedUsers",
        "user:operator@example.com",
        "serviceAccount:unknown@example.iam.gserviceaccount.com",
    ],
)
@pytest.mark.parametrize("mode", ["preflight", "required", "reconcile"])
def test_public_and_unknown_callers_stop_before_any_mutation(tmp_path, extra, mode):
    result, calls = _run(tmp_path, _scenario(MEMBERS + [extra]), mode)
    assert result.returncode != 0
    assert not any(c[2] == "add-iam-policy-binding" for c in calls)


@pytest.mark.parametrize("mode", ["preflight", "required", "reconcile"])
def test_conditional_read_grants_are_not_treated_as_unconditional(tmp_path, mode):
    scenario = _scenario()
    scenario["policy"]["bindings"][0]["condition"] = {"expression": "true"}
    result, calls = _run(tmp_path, scenario, mode)
    assert result.returncode != 0
    assert not any(c[2] == "add-iam-policy-binding" for c in calls)


@pytest.mark.parametrize(
    "failure", ["run services list", "run services describe", "run services get-iam-policy"]
)
def test_lookup_failures_never_count_as_absence(tmp_path, failure):
    scenario = _scenario()
    scenario["fail"] = failure
    result, _ = _run(tmp_path, scenario, "preflight")
    assert result.returncode != 0


@pytest.mark.parametrize("mode", ["preflight", "required", "reconcile"])
def test_only_confirmed_absence_allows_initial_deployment(tmp_path, mode):
    scenario = _scenario()
    scenario["absent"] = True
    result, calls = _run(tmp_path, scenario, mode)
    assert (result.returncode == 0) is (mode == "preflight")
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["public-ingress", "iam-disabled", "wrong-runtime"])
def test_provider_boundary_drift_is_rejected(tmp_path, change):
    scenario = _scenario()
    if change == "wrong-runtime":
        scenario["service"]["spec"]["template"]["spec"]["serviceAccountName"] = (
            "privileged@example.com"
        )
    else:
        key, value = (
            ("run.googleapis.com/ingress", "all")
            if change == "public-ingress"
            else ("run.googleapis.com/invoker-iam-disabled", "true")
        )
        scenario["service"]["metadata"]["annotations"][key] = value
    result, calls = _run(tmp_path, scenario, "reconcile")
    assert result.returncode != 0
    assert not any(c[2] == "add-iam-policy-binding" for c in calls)


@pytest.mark.parametrize(
    "policy",
    [
        [],
        {"bindings": "invalid"},
        {"bindings": None},
        {"bindings": [{"role": "roles/run.invoker", "members": "invalid"}]},
        {"bindings": [{"role": "roles/run.invoker", "members": [None]}]},
        {"bindings": [{"role": "roles/run.invoker", "members": MEMBERS + [MEMBERS[-1]]}]},
    ],
)
def test_malformed_or_duplicate_policy_fails_closed(tmp_path, policy):
    scenario = _scenario()
    scenario["policy"] = policy
    result, _ = _run(tmp_path, scenario, "preflight")
    assert result.returncode != 0


def test_reconciliation_verifies_that_grants_actually_took_effect(tmp_path):
    scenario = _scenario(MEMBERS[:2])
    scenario["ignore_grants"] = True
    result, calls = _run(tmp_path, scenario, "reconcile")
    assert result.returncode != 0
    assert len([c for c in calls if c[2] == "get-iam-policy"]) == 2


def test_grant_failure_stops_reconciliation(tmp_path):
    scenario = _scenario(MEMBERS[:2])
    scenario["fail"] = "run services add-iam-policy-binding"
    result, calls = _run(tmp_path, scenario, "reconcile")
    assert result.returncode != 0
    assert len([c for c in calls if c[2] == "add-iam-policy-binding"]) == 1


def test_non_invoker_bindings_are_preserved(tmp_path):
    scenario = _scenario()
    other = {"role": "roles/logging.viewer", "members": ["user:observer@example.com"]}
    scenario["policy"]["bindings"].append(other)
    result, _ = _run(tmp_path, scenario, "reconcile")
    assert result.returncode == 0, result.stderr
    assert other in json.loads((tmp_path / "state.json").read_text())["policy"]["bindings"]


@pytest.mark.parametrize("mode", ["preflight", "required", "reconcile"])
def test_no_invoker_grants_are_not_mistaken_for_a_complete_contract(tmp_path, mode):
    scenario = _scenario()
    scenario["policy"] = {}
    result, _ = _run(tmp_path, scenario, mode)
    assert (result.returncode == 0) is (mode != "required"), result.stderr


@pytest.mark.parametrize("mode", ["invalid", "--project=other", ""])
def test_unsupported_cli_modes_cannot_call_cloud(tmp_path, mode):
    result, calls = _run(tmp_path, _scenario(), mode)
    assert result.returncode == 2
    assert calls == []


@pytest.mark.parametrize("stage,mode", [("Reconcile private invoker policy", "reconcile")])
def test_real_workflow_reconciliation_restores_tdw_without_mutating_other_services(
    tmp_path, stage, mode
):
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
    step = next(s for s in workflow["jobs"]["deploy"]["steps"] if s.get("name") == stage)
    result, calls = _run(tmp_path, _scenario(MEMBERS[:2]), mode, script=step["run"])
    assert result.returncode == 0, result.stderr
    grants = [c for c in calls if c[2] == "add-iam-policy-binding"]
    assert len(grants) == 3
    assert all(c[3] == "tlws-mf-data-dev" for c in grants)
    assert any("--member=" + MEMBERS[-1] in c for c in grants)


def test_real_workflow_preflight_stops_before_publication_or_migration(tmp_path):
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
    steps = workflow["jobs"]["deploy"]["steps"]
    names = [s.get("name") for s in steps]
    preflight = "Verify private-read access before publishing"
    assert names.index(preflight) < names.index("Configure Artifact Registry")
    assert names.index(preflight) < names.index("Build and publish immutable image")
    assert names.index(preflight) < names.index("Deploy and execute the zero-retry migration job")
    step = steps[names.index(preflight)]
    result, calls = _run(
        tmp_path,
        _scenario(MEMBERS + ["allUsers"]),
        "preflight",
        script="set -euo pipefail\n" + step["run"] + "\necho SHOULD_NOT_PUBLISH_OR_MIGRATE",
    )
    assert result.returncode != 0
    assert "SHOULD_NOT_PUBLISH_OR_MIGRATE" not in result.stdout
    assert all(c[:2] == ["run", "services"] for c in calls)


def test_workflow_uses_shared_contract_without_broadening_writer_or_adding_gate_variables():
    source = (ROOT / ".github/workflows/deploy-dev.yml").read_text()
    workflow = yaml.safe_load(source)
    steps = workflow["jobs"]["deploy"]["steps"]
    by_name = {s.get("name"): s for s in steps}
    assert (
        by_name["Reconcile private invoker policy"]["run"]
        == "bash scripts/private_read_invokers_dev.sh reconcile"
    )
    assert (
        "bash scripts/private_read_invokers_dev.sh required"
        in by_name["Reconcile private service and immutable revision"]["run"]
    )
    for name in (
        "Deploy private association writer disabled",
        "Reconcile private association-writer invoker policy",
        "Reconcile the optional association writer revision",
    ):
        assert "tl-dwf" not in by_name[name]["run"]
        assert "private_read_invokers_dev.sh" not in by_name[name]["run"]
    assert (
        "([$insights] | sort)"
        in by_name["Reconcile the optional association writer revision"]["run"]
    )
    assert "vars.TDW" not in source and "vars.MFDATAINDIA_PRIVATE" not in source
    assert "remove-iam-policy-binding" not in SCRIPT.read_text()
    assert "secrets" not in " ".join(SCRIPT.read_text().splitlines()[15:])
