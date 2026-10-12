from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from mfdataindia.store.locks import DataOperationBusy, acquire_nav_data_operation_lock

ROOT = Path(__file__).resolve().parents[1]


class _LockConnection:
    def __init__(self, acquired: bool):
        self.acquired = acquired
        self.query = None
        self.params = None

    def execute(self, query, params):
        self.query = query
        self.params = params
        return self

    def fetchone(self):
        return {"acquired": self.acquired}


def test_nav_operation_lock_is_transaction_scoped_and_fail_closed() -> None:
    acquired = _LockConnection(True)
    acquire_nav_data_operation_lock(acquired)
    assert "pg_try_advisory_xact_lock" in acquired.query
    assert acquired.params == (1_413_423_425, 1)

    busy = _LockConnection(False)
    with pytest.raises(DataOperationBusy, match="already running"):
        acquire_nav_data_operation_lock(busy)


def test_refresh_and_audit_share_the_same_lock_helper() -> None:
    refresh = (ROOT / "scripts" / "refresh_daily.py").read_text()
    audit = (ROOT / "scripts" / "audit_nav_integrity.py").read_text()
    for source in (refresh, audit):
        assert "from mfdataindia.store.locks import acquire_nav_data_operation_lock" in source
        assert "acquire_nav_data_operation_lock(conn)" in source


def test_nav_audit_workflows_keep_deploy_operate_and_schedule_separate() -> None:
    deploy = (ROOT / ".github/workflows/deploy-nav-audit-dev.yml").read_text()
    operate = (ROOT / ".github/workflows/operate-nav-audit-dev.yml").read_text()
    enable = (ROOT / ".github/workflows/enable-nav-audit-schedule-dev.yml").read_text()
    disable = (ROOT / ".github/workflows/disable-nav-audit-schedule-dev.yml").read_text()

    assert "workflow_dispatch:" in deploy
    assert "gcloud run jobs execute \"$AUDIT_JOB\"" in deploy
    assert "verify_nav_audit_privileges.py" in deploy
    assert "gcloud run jobs update \"$AUDIT_JOB\"" in deploy
    assert "audit_nav_integrity.py,--summary-only" in deploy
    assert "gcloud scheduler jobs create" not in deploy
    assert "gcloud scheduler jobs update" not in deploy
    assert "taskCount == 1" in deploy
    assert "maxRetries == 0" in deploy

    assert "options: [dry-run, apply, certify]" in operate
    assert "--expected-dataset-version" in operate
    assert "DATA-01-" in operate
    assert "Verify the exact preceding gate" in operate
    assert 'test "$actual_sha256" = "$expected_prior"' in operate
    assert 'steps.prior.outputs.findings_sha256' in operate
    assert "--max-retries" not in operate
    assert "gcloud run jobs execute \"$AUDIT_JOB\"" in operate

    assert 'SCHEDULE: "10 */6 * * *"' in enable
    assert "NAV_INTEGRITY_AUDIT_CERTIFIED" in enable
    assert "certification_result_sha256" in enable
    assert "--report-dataset-version" in enable
    assert "NAV_DATASET_VERSION_CURRENT" in enable
    assert "--limit=20" in enable
    assert ".spec.template.spec.containers[0].image" in enable
    assert 'certificate_digest" != "$requested_digest' in enable
    assert 'certificate_sha" != "$requested_sha' in enable
    assert "--max-retry-attempts=0" in enable
    assert "gcloud scheduler jobs pause" in disable
    assert "trap rollback_on_exit EXIT" in enable
    assert "activation_succeeded=true" in enable
    for workflow in (deploy, operate, enable, disable):
        shell_blocks = workflow.replace("${{ inputs.mode }}", "")
        assert "${{ inputs." not in shell_blocks
        assert '"$GITHUB_EVENT_PATH"' in workflow


def test_audit_role_is_the_only_runtime_with_derived_table_writes() -> None:
    migration = (ROOT / "sql/020_nav_quality_audit_privileges.sql").read_text()
    provision = (ROOT / "scripts/provision_dev_infra.sh").read_text()
    verifier = (ROOT / "scripts/verify_nav_audit_privileges.py").read_text()

    assert "tlws-mf-data-audit-dev@" in provision
    assert "mfdata_audit" in provision
    assert "--database-roles=" in provision
    assert "--revoke-existing-roles" in provision
    assert "REVOKE cloudsqlsuperuser FROM mfdata_audit" in migration
    assert (
        "REVOKE INSERT, UPDATE, DELETE ON mf.nav_quality_assessments FROM mfdata_ingest"
        in migration
    )
    assert "ALTER ROLE mfdata_audit NOINHERIT NOCREATEROLE NOCREATEDB" in migration
    assert "GRANT INSERT, UPDATE, DELETE ON mf.nav_quality_assessments" in migration
    assert "source table" in verifier


def test_schedule_activation_pauses_on_post_enable_verification_failure(tmp_path) -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/enable-nav-audit-schedule-dev.yml").read_text()
    )
    steps = workflow["jobs"]["enable"]["steps"]
    activation = next(step for step in steps if step.get("name") == "Enable offset six-hour schedule")

    log_path = tmp_path / "gcloud.log"
    gcloud = tmp_path / "gcloud"
    gcloud.write_text(
        """#!/usr/bin/env bash
set -u
printf '%s\\n' "$*" >> "$MOCK_GCLOUD_LOG"
case "$*" in
  "scheduler jobs describe "*)
    if [[ "$*" == *"--format=value(state)"* ]]; then
      printf 'ENABLED\\n'
      exit 0
    fi
    if [[ "$*" == *"--format=json"* ]]; then
      printf '{}\\n'
      exit 0
    fi
    grep -q '^scheduler jobs create http ' "$MOCK_GCLOUD_LOG"
    ;;
  "scheduler jobs create http "*|"scheduler jobs update http "*|\
  "scheduler jobs pause "*|"scheduler jobs resume "*|\
  "run jobs add-iam-policy-binding "*) exit 0 ;;
  *) exit 1 ;;
esac
"""
    )
    gcloud.chmod(0o755)
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "MOCK_GCLOUD_LOG": str(log_path),
        "AUDIT_JOB": "tlws-mf-data-nav-audit-dev",
        "AUDIT_SCHEDULER": "tlws-mf-data-nav-audit-dev",
        "EXPECTED_PROJECT": "trillionlane-dev",
        "EXPECTED_REGION": "asia-south1",
        "SCHEDULER_SERVICE_ACCOUNT": (
            "tlws-mf-data-scheduler-dev@trillionlane-dev.iam.gserviceaccount.com"
        ),
        "SCHEDULE": "10 */6 * * *",
    }
    result = subprocess.run(
        ["bash", "-c", activation["run"]],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    calls = log_path.read_text()
    assert "scheduler jobs create http tlws-mf-data-nav-audit-dev" in calls
    assert "scheduler jobs pause tlws-mf-data-nav-audit-dev" in calls


def test_schedule_activation_rejects_certificate_from_older_image(tmp_path) -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/enable-nav-audit-schedule-dev.yml").read_text()
    )
    steps = workflow["jobs"]["enable"]["steps"]
    gate = next(
        step
        for step in steps
        if step.get("name") == "Require the exact certified audit execution"
    )

    requested_digest = f"sha256:{'b' * 64}"
    requested_sha = "b" * 40
    old_digest = f"sha256:{'a' * 64}"
    old_sha = "a" * 40
    certificate = {
        "assessments_written": 0,
        "dataset_version": 7,
        "status": "NAV_INTEGRITY_AUDIT_CERTIFIED",
    }
    canonical = json.dumps(certificate, sort_keys=True, separators=(",", ":"))
    certificate_hash = hashlib.sha256(canonical.encode()).hexdigest()
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "inputs": {
                    "certification_result_sha256": certificate_hash,
                    "expected_dataset_version": 7,
                    "expected_git_sha": requested_sha,
                    "expected_image_digest": requested_digest,
                }
            }
        )
    )

    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps(
            {
                "spec": {
                    "template": {
                        "spec": {
                            "taskCount": 1,
                            "template": {
                                "spec": {
                                    "containers": [
                                        {
                                            "args": [
                                                "/app/scripts/audit_nav_integrity.py",
                                                "--summary-only",
                                            ],
                                            "env": [
                                                {
                                                    "name": "DEPLOYMENT_GIT_SHA",
                                                    "value": requested_sha,
                                                }
                                            ],
                                            "image": (
                                                "example.invalid/audit@"
                                                f"{requested_digest}"
                                            ),
                                        }
                                    ],
                                    "maxRetries": 0,
                                    "serviceAccountName": "audit@example.invalid",
                                }
                            },
                        }
                    }
                }
            }
        )
    )
    execution_path = tmp_path / "execution.json"
    execution_path.write_text(
        json.dumps(
            {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "env": [
                                        {"name": "DEPLOYMENT_GIT_SHA", "value": old_sha}
                                    ],
                                    "image": f"example.invalid/audit@{old_digest}",
                                }
                            ]
                        }
                    }
                },
                "status": {
                    "conditions": [{"status": "True", "type": "Completed"}]
                },
            }
        )
    )
    logs_path = tmp_path / "logs.json"
    logs_path.write_text(
        json.dumps(
            [
                {
                    "jsonPayload": certificate,
                    "logName": "projects/test/logs/run.googleapis.com%2Fstdout",
                }
            ]
        )
    )
    call_log = tmp_path / "gcloud.log"
    gcloud = tmp_path / "gcloud"
    gcloud.write_text(
        """#!/usr/bin/env bash
set -u
printf '%s\\n' "$*" >> "$MOCK_GCLOUD_LOG"
case "$*" in
  "run jobs describe "*) cat "$MOCK_JOB_JSON" ;;
  "run jobs executions list "*) printf 'audit-old-certificate\\n' ;;
  "run jobs executions describe "*) cat "$MOCK_EXECUTION_JSON" ;;
  "logging read "*) cat "$MOCK_LOGS_JSON" ;;
  "run jobs execute "*) printf 'unexpected-probe\\n' ;;
  *) exit 1 ;;
esac
"""
    )
    gcloud.chmod(0o755)
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "GITHUB_EVENT_PATH": str(event_path),
        "MOCK_GCLOUD_LOG": str(call_log),
        "MOCK_JOB_JSON": str(job_path),
        "MOCK_EXECUTION_JSON": str(execution_path),
        "MOCK_LOGS_JSON": str(logs_path),
        "AUDIT_JOB": "tlws-mf-data-nav-audit-dev",
        "AUDIT_SERVICE_ACCOUNT": "audit@example.invalid",
        "EXPECTED_PROJECT": "trillionlane-dev",
        "EXPECTED_REGION": "asia-south1",
    }
    result = subprocess.run(
        ["bash", "-c", gate["run"]],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "run jobs execute" not in call_log.read_text()


def test_cloud_run_result_parser_accepts_structured_and_text_payloads() -> None:
    parser = ROOT / "scripts/extract_cloud_run_json_result.sh"
    structured_logs = [
        {
            "logName": "projects/test/logs/run.googleapis.com%2Fstderr",
            "jsonPayload": {"status": "WRONG_STREAM"},
        },
        {
            "logName": "projects/test/logs/run.googleapis.com%2Fstdout",
            "jsonPayload": {"status": "STRUCTURED", "dataset_version": 8},
        },
    ]
    structured = subprocess.run(
        ["bash", str(parser)],
        input=json.dumps(structured_logs),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(structured.stdout) == {
        "dataset_version": 8,
        "status": "STRUCTURED",
    }

    text_logs = [
        {
            "logName": "projects/test/logs/run.googleapis.com%2Fstdout",
            "textPayload": '{"status":"TEXT_FALLBACK","dataset_version":7}',
        }
    ]
    fallback = subprocess.run(
        ["bash", str(parser)],
        input=json.dumps(text_logs),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(fallback.stdout) == {
        "dataset_version": 7,
        "status": "TEXT_FALLBACK",
    }
