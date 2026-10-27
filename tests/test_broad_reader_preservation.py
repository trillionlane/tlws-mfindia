"""Independent complete parent/guard/source inverse checks, no ignored regions."""

import hashlib
import json
from pathlib import Path
import pytest
from broad_transport_preservation import restore_broad_parent
from ci_risk_preservation import restore_policy_parent

ROOT = Path(__file__).resolve().parents[1]


def test_all_857_parent_files_full_bytes_including_each_edited_guard():
    assert hashlib.sha256((ROOT / "docs/BROAD_READER_TRANSPORT_PARENT_AUTHORITY_20261026.json").read_bytes()).hexdigest() == "0ee1d68903622d67440a0495d262789e8f0e777effc7f24bbfb54dc190022cba"
    authority = json.loads(
        (ROOT / "docs/BROAD_READER_TRANSPORT_PARENT_AUTHORITY_20261026.json").read_text()
    )
    design = json.loads((ROOT / "docs/BROAD_READER_TRANSPORT_DESIGN_20261026.json").read_text())
    assert authority["base"] == design["base"] == "fd69b4ce355e91e5b77b98393b7f066a0bb17e28"
    assert authority["old_tracked_files"] == len(authority["files"]) == 857
    assert len(design["counted_parent_recipe"]) == 6
    for name, expected in authority["files"].items():
        path = ROOT / name
        assert path.is_symlink() == (expected["kind"] == "symlink")
        data = (
            str(path.readlink()).encode()
            if path.is_symlink()
            else restore_broad_parent(name, path.read_bytes())
        )
        assert hashlib.sha256(data).hexdigest() == expected["sha256"], name


def test_all_controller_backend_full_inverse():
    plan = json.loads((ROOT / "docs/BROAD_READER_TRANSPORT_COPY_RECIPES_20261026.json").read_text())
    for item in plan["copies"]:
        value = (ROOT / item["new_path"]).read_bytes()
        for rule in reversed(item["substitutions"]):
            old, new = rule["old"].encode(), rule["new"].encode()
            assert value.count(new) == rule["count"], (item["new_path"], rule["old"][:80])
            value = value.replace(new, old)
        assert value == (ROOT / item["old_path"]).read_bytes(), item["new_path"]


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/verify.yml",
        "tests/test_ci_cost_preservation.py",
        "tests/test_broad_reader_policy_preservation.py",
        "tests/test_reader_permission_capture_preservation.py",
        "tests/test_reader_access_transport.py",
        "tests/test_ci_verify_sequence.py",
    ],
)
@pytest.mark.parametrize("mutation", ["append", "drop", "double"])
def test_full_guard_inverse_rejects_every_unapproved_byte(path, mutation):
    value = (ROOT / path).read_bytes()
    if mutation == "append":
        value += b"\n# UNAPPROVED\n"
    elif mutation == "drop":
        value = value[1:]
    else:
        value += value
    with pytest.raises(AssertionError):
        restore_broad_parent(path, value)


def test_complete_old_workflow_is_prefix_and_one_new_both_image_smoke():
    authority = json.loads(
        (ROOT / "docs/BROAD_READER_TRANSPORT_PARENT_AUTHORITY_20261026.json").read_text()
    )
    raw = restore_policy_parent(".github/workflows/verify.yml", (ROOT / ".github/workflows/verify.yml").read_bytes())
    before = restore_broad_parent(".github/workflows/verify.yml", raw)
    assert raw.startswith(before)
    assert (
        hashlib.sha256(before).hexdigest()
        == authority["files"][".github/workflows/verify.yml"]["sha256"]
    )
    suffix = raw[len(before) :].decode()
    for script in (
        "collect_broad_reader_capture_dev.py",
        "operate_broad_reader_capture_dev.py",
        "apply_broad_reader_access_dev.py",
        "operate_broad_reader_access_dev.py",
        "smoke_broad_reader_binding.py",
    ):
        assert suffix.count("/app/scripts/" + script) == 1
    for forbidden in (
        "MFDATAINDIA_DSN",
        "gcloud",
        "--privileged",
        "--ulimit",
        "--memory",
        "--cpus",
        "|| true",
    ):
        assert forbidden not in suffix
