from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_missing_receipt_parser_stops_before_publication_submission(tmp_path: Path) -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/refresh-tags-dev.yml").read_text()
    )
    steps = workflow["jobs"]["refresh"]["steps"]
    validation = next(
        step["run"]
        for step in steps
        if step.get("name") == "Validate supervised execution contract"
    )
    publication = next(step["run"] for step in steps if step.get("id") == "publication")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git_stub = bin_dir / "git"
    git_stub.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$GITHUB_SHA\"\n")
    git_stub.chmod(0o755)
    marker = tmp_path / "gcloud-called"
    gcloud_stub = bin_dir / "gcloud"
    gcloud_stub.write_text(
        "#!/usr/bin/env bash\nprintf called >\"$GCLOUD_MARKER\"\nexit 99\n"
    )
    gcloud_stub.chmod(0o755)

    sha = "a" * 40
    result = subprocess.run(
        ["bash", "-c", validation + "\n" + publication],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CONFIRM_PROJECT": "trillionlane-dev",
            "CONFIRM_JOB": "tlws-mf-data-tag-refresh-dev",
            "EXPECTED_PROJECT": "trillionlane-dev",
            "EXPECTED_REGION": "asia-south1",
            "SERVICE_NAME": "tlws-mf-data-dev",
            "TAG_REFRESH_JOB": "tlws-mf-data-tag-refresh-dev",
            "TAG_RUNTIME_SERVICE_ACCOUNT": "tag@example.test",
            "GITHUB_REPOSITORY": "trillionlane/tlws-mfindia",
            "GITHUB_REPOSITORY_ID": "1403671867",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": sha,
            "GCLOUD_MARKER": str(marker),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert not marker.exists()


def test_cutover_cleanup_removes_only_deployer_admin_member(tmp_path: Path) -> None:
    deploy_member = "serviceAccount:tlws-mf-data-deploy-dev@trillionlane-dev.iam.gserviceaccount.com"
    operator_member = "serviceAccount:security-operator@trillionlane-dev.iam.gserviceaccount.com"
    tag_member = "serviceAccount:tlws-mf-data-tag-writer-dev@trillionlane-dev.iam.gserviceaccount.com"
    state = tmp_path / "policy.json"
    state.write_text(
        json.dumps(
            {
                "version": 3,
                "etag": "synthetic-etag",
                "bindings": [
                    {
                        "role": "roles/secretmanager.admin",
                        "members": [deploy_member, operator_member],
                        "condition": {
                            "title": "mfdata-tag-writer-cutover",
                            "expression": "request.time < timestamp('2099-01-01T00:00:00Z')",
                        },
                    },
                    {
                        "role": "roles/secretmanager.secretAccessor",
                        "members": [tag_member],
                    },
                ],
            }
        )
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gcloud_stub = bin_dir / "gcloud"
    gcloud_stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "case \"$1 $2\" in\n"
        "  'secrets get-iam-policy') cat \"$POLICY_STATE\" ;;\n"
        "  'secrets set-iam-policy') cp \"$4\" \"$POLICY_STATE\" ;;\n"
        "  *) exit 99 ;;\n"
        "esac\n"
    )
    gcloud_stub.chmod(0o755)

    subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/remove_tag_cutover_admin.sh"),
            "trillionlane-dev",
            "tlws-mf-data-deploy-dev@trillionlane-dev.iam.gserviceaccount.com",
        ],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "POLICY_STATE": str(state),
        },
        check=True,
    )

    policy = json.loads(state.read_text())
    admin = next(
        binding
        for binding in policy["bindings"]
        if binding["role"] == "roles/secretmanager.admin"
    )
    accessor = next(
        binding
        for binding in policy["bindings"]
        if binding["role"] == "roles/secretmanager.secretAccessor"
    )
    assert admin["members"] == [operator_member]
    assert accessor["members"] == [tag_member]
    assert policy["etag"] == "synthetic-etag"
