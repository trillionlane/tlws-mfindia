from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "promote_association_revision_dev.sh"


def _fake_command(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
    path.chmod(0o755)


def _run_promotion(tmp_path: Path, *, http_status: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "gcloud.log"
    _fake_command(
        fake_bin / "gcloud",
        """
printf '%s\n' "$*" >> "$FAKE_GCLOUD_LOG"
if [[ "$*" == "run services describe "* ]]; then
  printf '%s\n' '{"metadata":{"annotations":{"run.googleapis.com/ingress":"internal"}},"status":{"url":"https://writer.internal","traffic":[{"revisionName":"writer-enabled","percent":100}]}}'
fi
""",
    )
    _fake_command(fake_bin / "curl", 'printf \'%s\' "$FAKE_HTTP_STATUS"\n')
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["FAKE_GCLOUD_LOG"] = str(log)
    env["FAKE_HTTP_STATUS"] = http_status
    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "writer-service",
            "test-project",
            "test-region",
            "writer-disabled",
            "writer-enabled",
            "a" * 40,
        ],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )
    return result, log.read_text().splitlines()


def test_public_http_200_rolls_back_to_disabled_revision(tmp_path: Path) -> None:
    result, calls = _run_promotion(tmp_path, http_status="200")

    assert result.returncode == 1
    assert "unexpectedly reachable" in result.stderr
    assert "restoring disabled revision writer-disabled" in result.stderr
    assert "--to-revisions=writer-enabled=100" in calls[0]
    assert "--to-revisions=writer-disabled=100" in calls[-1]


def test_non_public_response_keeps_enabled_revision(tmp_path: Path) -> None:
    result, calls = _run_promotion(tmp_path, http_status="403")

    assert result.returncode == 0
    assert "Association writes enabled" in result.stdout
    assert any("--to-revisions=writer-enabled=100" in call for call in calls)
    assert not any("--to-revisions=writer-disabled=100" in call for call in calls)
