from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_optional_association_writer_dev.sh"
SERVICE = "tlws-mf-data-association-writer-dev"


def _writer_service(*, template: str, traffic: list[dict], latest: str) -> dict:
    return {
        "metadata": {"name": SERVICE},
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "env": [
                                {
                                    "name": "MFDATAINDIA_ASSOCIATION_WRITES_ENABLED",
                                    "value": template,
                                }
                            ]
                        }
                    ]
                }
            }
        },
        "status": {"latestReadyRevisionName": latest, "traffic": traffic},
    }


def _writer_revision(enabled: str) -> dict:
    return {
        "spec": {
            "containers": [
                {
                    "env": [
                        {
                            "name": "MFDATAINDIA_ASSOCIATION_WRITES_ENABLED",
                            "value": enabled,
                        }
                    ]
                }
            ]
        }
    }


def _run_guard(tmp_path: Path, scenario: dict) -> subprocess.CompletedProcess[str]:
    fake_gcloud = tmp_path / "gcloud"
    fake_gcloud.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

scenario = json.loads(os.environ["FAKE_GCLOUD_SCENARIO"])
args = sys.argv[1:]
if args[:3] == ["run", "services", "list"]:
    if scenario.get("list_error"):
        print(scenario["list_error"], file=sys.stderr)
        raise SystemExit(1)
    if scenario.get("absent"):
        print("[]")
    else:
        print(json.dumps([{"metadata": {"name": scenario["service_name"]}}]))
elif args[:3] == ["run", "services", "describe"]:
    print(json.dumps(scenario["service"]))
elif args[:3] == ["run", "revisions", "describe"]:
    revision = args[3]
    if revision not in scenario["revisions"]:
        print(f"revision lookup failed: {revision}", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(scenario["revisions"][revision]))
else:
    print(f"unexpected gcloud arguments: {args}", file=sys.stderr)
    raise SystemExit(2)
""",
        encoding="utf-8",
    )
    fake_gcloud.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"
    env["FAKE_GCLOUD_SCENARIO"] = json.dumps(
        {"service_name": SERVICE, "revisions": {}, **scenario}
    )
    return subprocess.run(
        ["bash", str(SCRIPT), "trillionlane-dev", "asia-south1", SERVICE],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )


def test_guard_accepts_only_confirmed_absence(tmp_path: Path) -> None:
    result = _run_guard(tmp_path, {"absent": True})

    assert result.returncode == 0
    assert "confirmed absent" in result.stdout


def test_guard_fails_closed_when_service_listing_fails(tmp_path: Path) -> None:
    result = _run_guard(tmp_path, {"list_error": "PERMISSION_DENIED: denied"})

    assert result.returncode != 0
    assert "PERMISSION_DENIED" in result.stderr
    assert "confirmed absent" not in result.stdout


def test_guard_rejects_enabled_revision_still_receiving_traffic(tmp_path: Path) -> None:
    service = _writer_service(
        template="false",
        latest="writer-disabled",
        traffic=[{"revisionName": "writer-enabled", "percent": 100}],
    )
    result = _run_guard(
        tmp_path,
        {
            "service": service,
            "revisions": {"writer-enabled": _writer_revision("true")},
        },
    )

    assert result.returncode != 0
    assert "writer-enabled is not disabled" in result.stderr


def test_guard_checks_every_traffic_serving_revision(tmp_path: Path) -> None:
    service = _writer_service(
        template="false",
        latest="writer-current",
        traffic=[
            {"revisionName": "writer-previous", "percent": 20},
            {"latestRevision": True, "percent": 80},
        ],
    )
    result = _run_guard(
        tmp_path,
        {
            "service": service,
            "revisions": {
                "writer-current": _writer_revision("false"),
                "writer-previous": _writer_revision("false"),
            },
        },
    )

    assert result.returncode == 0
    assert "2 serving revision(s)" in result.stdout


def test_guard_rejects_enabled_latest_template(tmp_path: Path) -> None:
    service = _writer_service(
        template="true",
        latest="writer-current",
        traffic=[{"revisionName": "writer-current", "percent": 100}],
    )
    result = _run_guard(
        tmp_path,
        {
            "service": service,
            "revisions": {"writer-current": _writer_revision("false")},
        },
    )

    assert result.returncode != 0
    assert "latest template is not disabled" in result.stderr
