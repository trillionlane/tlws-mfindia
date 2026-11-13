"""The traffic fix has one closed exact-byte historical inverse."""

import hashlib
from pathlib import Path

import pytest

from tag_deploy_traffic_history import BASE, ROOT, manifest, previous


def test_exact_modified_files_restore_to_tag04_main() -> None:
    rows = manifest()
    assert set(rows) == {
        ".github/workflows/deploy-dev.yml",
        "tests/pypdf_patch_preservation.py",
        "tests/test_pypdf_patch_preservation.py",
        "tests/test_private_deployment.py",
        "tests/test_release_admission.py",
    }
    for name, row in rows.items():
        current = (ROOT / name).read_bytes()
        assert hashlib.sha256(current).hexdigest() == row["current_sha256"]
        restored = previous(name, current)
        assert hashlib.sha256(restored).hexdigest() == row["previous_sha256"]


def test_unlisted_external_and_tampered_bytes_fail_closed(tmp_path: Path) -> None:
    arbitrary = b"unchanged"
    assert previous("README.md", arbitrary) is arbitrary
    assert previous(tmp_path / "outside", arbitrary) is arbitrary
    with pytest.raises(AssertionError):
        previous(".github/workflows/deploy-dev.yml", arbitrary)


def test_manifest_base_is_exact_tag04_main() -> None:
    assert BASE == "3741d683c78447cc9f53890625b449f709423406"
