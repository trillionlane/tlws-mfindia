"""Parallel jobs retain every original check, inner order and per-job limits."""

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
    addition = "- name: Verify bounded certification input native exec in both images"
    security = "- name: Verify patched PDF workers and dormant source guards in both images"
    # Preserve all frozen original bodies/order byte-for-byte. The sole new
    # mandatory step is additive, after every original gate, in the same job.
    assert [s["label"] for s in second[-2:]] == [addition, security]
    assert first + second[3:-2] == expected
    assert sum(s["label"] == addition for s in first + second) == 1
    assert sum(s["label"] == security for s in first + second) == 1
    assert len(expected) == 40


def test_independent_existing_jobs_keep_both_mandatory_and_have_no_live_dsn():
    text, tests, images = jobs()
    assert "    name: Test, audit, and build\n" in images
    assert "needs:" not in images and "needs:" not in tests
    assert re.findall(r"^  ([a-z_]+):\n", text.split("jobs:\n", 1)[1], re.M) == ["tests", "verify"]
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


def test_new_transport_smoke_executes_both_final_images_without_resource_flags():
    _, _, images = jobs()
    addition = images.split("      - name: Verify bounded certification input native exec in both images\n")[1]
    assert addition.count("/app/scripts/smoke_certification_input_transport.py") == 2
    assert '"$image" /app/scripts/smoke_certification_input_transport.py' in addition
    assert '"$tools_image" /app/scripts/smoke_certification_input_transport.py' in addition
    assert '"$image" /app/scripts/certify_amc_books_transport_dev.py --help' in addition
    for forbidden in ("--privileged", "--ulimit", "--memory", "--cpus", "|| true", "continue-on-error"):
        assert forbidden not in addition


def test_security_smoke_is_additive_both_packaged_images_without_resource_or_gate_changes():
    _, _, images = jobs()
    addition = images.split("      - name: Verify patched PDF workers and dormant source guards in both images\n")[1]
    assert addition.count("/app/scripts/smoke_pdf_security_compatibility.py") == 2
    assert '"$image" /app/scripts/smoke_pdf_security_compatibility.py' in addition
    assert '"$tools_image" /app/scripts/smoke_pdf_security_compatibility.py' in addition
    for forbidden in ("--privileged", "--ulimit", "--memory", "--cpus", "|| true", "continue-on-error"):
        assert forbidden not in addition
