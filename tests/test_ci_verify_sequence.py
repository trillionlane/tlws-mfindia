"""Sequential CI retains every original check and fixed per-job limits."""

import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/verify.yml"


def inventory(job):
    steps = job.split("    steps:\n", 1)[1]
    blocks = [b for b in re.split(r"(?=^      - )", steps, flags=re.M) if b.strip()]
    return [
        {"label": b.splitlines()[0].strip(),
         "sha256": hashlib.sha256(b.strip().encode()).hexdigest()}
        for b in blocks
    ]


def jobs():
    text = WORKFLOW.read_text()
    tests, images = text.split("  tests:\n", 1)[1].split("  verify:\n", 1)
    return text, tests, images


def test_every_original_step_body_and_order_is_preserved():
    _, tests, images = jobs()
    expected = json.loads((ROOT / "tests/fixtures/ci_verify_step_inventory.json").read_text())
    first, second = inventory(tests), inventory(images)
    assert second[:3] == expected[:3]  # Only fresh-runner setup repeats.
    assert first + second[3:] == expected
    assert len(expected) == 40


def test_required_final_gate_is_sequential_and_has_no_service_or_live_dsn():
    text, tests, images = jobs()
    assert "    name: Test, audit, and build\n" in images
    assert "    needs: tests\n" in images
    assert "    services:\n" in tests and "image: postgres:18.6-alpine" in tests
    assert "MF_TEST_DSN:" in tests
    assert "services:" not in images and "MF_TEST_DSN:" not in images
    assert text.count("timeout-minutes: 30") == 2
    assert text.count("runs-on: ubuntu-latest") == 2
    for forbidden in ("continue-on-error:", "always()", "strategy:", "matrix:", "timeout-minutes: 60"):
        assert forbidden not in text


def test_both_full_pg_partitions_and_native_restore_smoke_remain_mandatory():
    _, tests, images = jobs()
    assert "scripts/run_ci_tests.py focused" in tests
    assert "scripts/run_ci_tests.py remainder" in tests
    assert tests.index("scripts/run_ci_tests.py focused") < tests.index("scripts/run_ci_tests.py remainder")
    assert "uv run pip-audit" in tests
    assert "./scripts/scan_tracked_secrets.sh" in tests
    assert "docker build --build-arg" in images
    assert "--file Dockerfile.review-tools" in images
    assert "/app/scripts/smoke_clone_checkpoint.py" in images
