"""Independent full parent/copy restoration, including the changed guard itself."""

from pypdf_patch_preservation import restore_pypdf_parent as _pypdf_parent_bytes

import hashlib
import os
import stat
import subprocess

import pytest

from broad_capture_successor_preservation import PLAN, ROOT, restore_successor_parent
from test_ci_risk_policy import git_repo as _git_repo, git_at
import ci_risk_preservation as old_preservation

git_repo = _git_repo


def parent_bytes(name, data):
    if name in old_preservation.PRESENTATION:
        return old_preservation.restore_policy_parent(name, data)
    return restore_successor_parent(name, data)


def test_all896_whole_parent_bytes_modes_including_changed_guard():
    assert PLAN["base"] == "ded578a363a00584aaa5cc655aed3dfe05de8b3d"
    assert len(PLAN["files"]) == PLAN["tracked_parent_count"] == 896
    assert set(PLAN["parent_recipes"]) == {
        ".github/workflows/verify.yml",
        "tests/ci_risk_preservation.py",
    }
    for name, expected in PLAN["files"].items():
        path = ROOT / name
        if expected["mode"] == "120000":
            assert path.is_symlink(), name
            data = os.readlink(path).encode()
        else:
            assert not path.is_symlink() and stat.S_ISREG(path.stat().st_mode), name
            assert bool(path.stat().st_mode & 0o111) == (expected["mode"] == "100755"), name
            data = _pypdf_parent_bytes(path, path.read_bytes())
        assert hashlib.sha256(parent_bytes(name, data)).hexdigest() == expected["sha256"], name


def test_five_whole_copy_inverses_and_all_frozen_source_pins():
    for item in PLAN["copies"]:
        value = _pypdf_parent_bytes(ROOT / item["new_path"], (ROOT / item["new_path"]).read_bytes())
        assert hashlib.sha256(value).hexdigest() == item["new_sha256"]
        for rule in reversed(item["substitutions"]):
            assert value.count(rule["new"].encode()) == rule["count"]
            value = value.replace(rule["new"].encode(), rule["old"].encode())
        assert value == _pypdf_parent_bytes(ROOT / item["old_path"], (ROOT / item["old_path"]).read_bytes())
        assert hashlib.sha256(value).hexdigest() == item["old_sha256"]


@pytest.mark.parametrize("name", sorted(PLAN["parent_recipes"]))
@pytest.mark.parametrize("mutation", ["append", "drop", "duplicate", "change"])
def test_inverse_rejects_unapproved_guard_and_workflow_bytes(name, mutation):
    data = _pypdf_parent_bytes(ROOT / name, (ROOT / name).read_bytes())
    if mutation == "append":
        data += b"\n# UNAPPROVED\n"
    elif mutation == "drop":
        data = data[1:]
    elif mutation == "duplicate":
        data += data
    else:
        data = bytes([data[0] ^ 1]) + data[1:]
    with pytest.raises(AssertionError):
        restore_successor_parent(name, data)


def test_both_image_synthetic_entrypoint_smoke_without_secret():
    result = subprocess.run(
        ["uv", "run", "python", "scripts/smoke_broad_reader_capture_v2_binding.py"],
        cwd=ROOT,
        env=dict(os.environ, DEPLOYMENT_GIT_SHA="a" * 40),
        capture_output=True,
        text=True,
        check=True,
    )
    import json

    receipt = json.loads(result.stdout)
    assert receipt == dict(
        packaged_broad_capture_v2_entrypoint="PASS",
        strict_approval_parse_before_missing_DSN=True,
        provider_requests=0,
        database_queries=0,
        runtime_or_write_authority=False,
    )


@pytest.mark.parametrize("mutation", ["exact", "append", "drop", "duplicate", "change"])
def test_only_exact_historical_workflow_stage_can_pass_outer_inverse(mutation):
    import base64
    import gzip
    import json

    policy = json.loads((ROOT / "docs/CI_RISK_POLICY_LITERAL_PLAN_20261026.json").read_text())
    row = policy["exact_changed_old_files"][".github/workflows/verify.yml"]
    data = gzip.decompress(base64.b64decode(row["old_complete_gzip_base64"]))
    assert (
        hashlib.sha256(data).hexdigest()
        == row["old_full_sha256"]
        == PLAN["historical_restored_workflow_sha256"]
    )
    if mutation == "exact":
        assert restore_successor_parent(".github/workflows/verify.yml", data) == data
        return
    if mutation == "append":
        data += b"\n# UNAPPROVED\n"
    elif mutation == "drop":
        data = data[1:]
    elif mutation == "duplicate":
        data += data
    else:
        data = bytes([data[0] ^ 1]) + data[1:]
    with pytest.raises(AssertionError):
        restore_successor_parent(".github/workflows/verify.yml", data)


@pytest.mark.parametrize("name", sorted(old_preservation.PRESENTATION))
def test_prospective_committed_presentation_uses_same896_parent_helper(git_repo, monkeypatch, name):
    root = git_repo
    monkeypatch.setattr(old_preservation, "ROOT", root)
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    changed = b"SYNTHETIC_NEW_PRESENTATION_COMMITTED\n"
    path.write_bytes(changed)
    git_at(root, "add", name)
    git_at(root, "commit", "-m", "synthetic presentation")
    restored = parent_bytes(name, _pypdf_parent_bytes(path, path.read_bytes()))
    assert restored != changed
    assert hashlib.sha256(restored).hexdigest() == PLAN["files"][name]["sha256"]
    assert _pypdf_parent_bytes(path, path.read_bytes()) == changed


def test_exact_existing_tenfile_authority_no_new_exception():
    expected = PLAN["existing_approved_presentation_exception"]
    assert set(expected["files"]) == set(old_preservation.PRESENTATION)
    assert len(expected["files"]) == 10
    raw = _pypdf_parent_bytes(ROOT / expected["authority_path"], (ROOT / expected["authority_path"]).read_bytes())
    assert hashlib.sha256(raw).hexdigest() == expected["authority_sha256"]
    assert all(
        PLAN["files"][n]["sha256"] == sha == old_preservation.PRESENTATION[n]["sha256"]
        for n, sha in expected["files"].items()
    )


@pytest.mark.parametrize(
    "name",
    [
        "README.extra.md",
        "docs/CI_RISK_POLICY_LITERAL_PLAN_20261026.json",
        "src/mfdataindia/review/reader_access_correction.py",
    ],
)
def test_nonallowlisted_paths_not_normalized_to_parent_bytes(name):
    data = b"SYNTHETIC_UNAPPROVED_SOURCE_EDIT\n"
    assert name not in old_preservation.PRESENTATION
    assert parent_bytes(name, data) == data
    if name in PLAN["files"]:
        assert hashlib.sha256(data).hexdigest() != PLAN["files"][name]["sha256"]


@pytest.mark.parametrize(
    "defect",
    [
        "dirty",
        "wrong_argument",
        "partial",
        "symlink",
        "executable",
        "committed_executable",
        "missing",
    ],
)
def test_same896_helper_keeps_original_presentation_custody_rejections(
    git_repo, monkeypatch, defect
):
    root = git_repo
    monkeypatch.setattr(old_preservation, "ROOT", root)
    path = root / "README.md"
    data = _pypdf_parent_bytes(path, path.read_bytes())
    if defect == "dirty":
        path.write_bytes(b"SYNTHETIC_UNCOMMITTED\n")
    elif defect == "wrong_argument":
        data = b"SYNTHETIC_NOT_HEAD\n"
    elif defect == "partial":
        data = data[:-1]
    elif defect == "symlink":
        path.rename(root / "saved-readme")
        path.symlink_to(root / "saved-readme")
    elif defect == "executable":
        path.chmod(0o755)
    elif defect == "committed_executable":
        path.chmod(0o755)
        git_at(root, "add", "README.md")
        git_at(root, "commit", "-m", "synthetic wrong mode")
        path.chmod(0o644)
    else:
        path.rename(root / "saved-readme")
    with pytest.raises((AssertionError, FileNotFoundError)):
        parent_bytes("README.md", data)
