"""Entire951-parent and whole-copy inverses, no ignored bytes or old hash updates."""
import base64
import gzip
import hashlib
import json
import os
import stat
import subprocess

import pytest

from reader_function_attestation_preservation import PLAN, ROOT, restore_attestation_parent


def test_all951_parent_full_bytes_modes_and_modified_old_guard_restore_exactly():
    raw = gzip.decompress(base64.b64decode(PLAN["parent_authority_gzip_base64"]))
    assert hashlib.sha256(raw).hexdigest() == PLAN["parent_authority_sha256"]
    authority = json.loads(raw)
    assert authority["file_count"] == PLAN["tracked_parent_count"] == 951
    assert PLAN["base"] == "67bdd1fbf8ef7cee11b79a24bd300d32eab1845d"
    assert set(PLAN["parent_recipes"]) == {
        ".github/workflows/verify.yml", "tests/reader_function_identification_preservation.py"}
    for row in authority["all_files"]:
        p = ROOT / row["path"]
        if row["mode"] == "120000":
            assert p.is_symlink()
            data = os.readlink(p).encode()
        else:
            assert not p.is_symlink() and stat.S_ISREG(p.stat().st_mode)
            assert bool(p.stat().st_mode & 0o111) == (row["mode"] == "100755")
            data = p.read_bytes()
        restored = restore_attestation_parent(row["path"], data)
        assert len(restored) == row["bytes"], row["path"]
        assert hashlib.sha256(restored).hexdigest() == row["sha256"], row["path"]


def test_all11_source_and_test_whole_literal_copies_restore_their_entire_parent_bytes():
    assert len(PLAN["copies"]) == 11
    for row in PLAN["copies"]:
        raw = (ROOT / row["new_path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["new_sha256"], row["new_path"]
        for rule in reversed(row["substitutions"]):
            assert raw.count(rule["new"].encode()) == rule["count"], row["new_path"]
            raw = raw.replace(rule["new"].encode(), rule["old"].encode())
        assert hashlib.sha256(raw).hexdigest() == row["old_sha256"], row["old_path"]
        assert raw == (ROOT / row["old_path"]).read_bytes(), row["old_path"]


@pytest.mark.parametrize("name", sorted(PLAN["parent_recipes"]))
@pytest.mark.parametrize("mutation", ["append", "drop", "duplicate", "change"])
def test_finite_inverse_rejects_every_unapproved_change(name, mutation):
    raw = (ROOT / name).read_bytes()
    changed = (raw + b"\n# UNAPPROVED\n" if mutation == "append" else raw[1:]
               if mutation == "drop" else raw + raw if mutation == "duplicate"
               else bytes([raw[0] ^ 1]) + raw[1:])
    with pytest.raises(AssertionError):
        restore_attestation_parent(name, changed)


def test_exact_3commands_only_after_all_prior_BOTH_image_commands_and_old_workflow_full_SHA():
    row = PLAN["parent_recipes"][".github/workflows/verify.yml"]
    assert len(row["substitutions"]) == 1
    rule = row["substitutions"][0]
    assert rule["count"] == 1
    assert rule["new"].count("docker run --rm") - rule["old"].count("docker run --rm") == 3
    raw = (ROOT / ".github/workflows/verify.yml").read_bytes()
    assert hashlib.sha256(restore_attestation_parent(
        ".github/workflows/verify.yml", raw)).hexdigest() == row["old_sha256"]


def test_actual_packaged_worker_strict_approval_without_secret_SQL_or_provider():
    completed = subprocess.run(
        [str(ROOT / ".venv/bin/python") if (ROOT / ".venv/bin/python").exists() else "python",
         "scripts/smoke_reader_function_attestation_binding.py"], cwd=ROOT,
        env=dict(os.environ, DEPLOYMENT_GIT_SHA="a" * 40),
        capture_output=True, text=True, check=True)
    result = json.loads(completed.stdout)
    assert result["packaged_reader_function_attestation_entrypoint"] == "PASS"
    assert result["database_queries"] == result["provider_requests"] == 0
    assert result["runtime_or_write_authority"] is False


def test_frozen_policy_default_rejection_and_migration_reference_remain_unmodified():
    from mfdataindia.review import broad_reader_policy as policy
    from mfdataindia.review import reader_function_attestation_reference as reference
    authority = json.loads(gzip.decompress(base64.b64decode(PLAN["parent_authority_gzip_base64"])))
    for name in ("src/mfdataindia/review/broad_reader_policy.py",
                 "sql/optional/036_official_fact_verification.sql"):
        row = next(r for r in authority["all_files"] if r["path"] == name)
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == row["sha256"]
    assert "PRIVILEGED_DOMAIN_FUNCTION_NOT_ALLOWED" in policy.catalogue.__code__.co_consts
    assert hashlib.sha256((ROOT / "sql/optional/036_official_fact_verification.sql").read_bytes()).hexdigest() == reference.MIGRATION_SHA256
