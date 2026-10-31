"""Whole930 parent proof, including every modified old guard and workflow."""

from pypdf_patch_preservation import restore_pypdf_parent as _pypdf_parent_bytes
import base64
import gzip
import hashlib
import json
import os
import stat
import subprocess

import pytest

from reader_function_identification_preservation import PLAN, ROOT, restore_identification_parent


def test_all930_parent_bytes_modes_and_all_guards_unchanged():
    raw = gzip.decompress(base64.b64decode(PLAN["parent_authority_gzip_base64"]))
    assert hashlib.sha256(raw).hexdigest() == PLAN["parent_authority_sha256"]
    authority = json.loads(raw)
    assert authority["file_count"] == PLAN["tracked_parent_count"] == 930
    assert PLAN["base"] == "bec953339af734e452a557df7cc0c775b5b43772"
    assert set(PLAN["parent_recipes"]) == {
        ".github/workflows/verify.yml", "tests/broad_reader_paired_preservation.py"}
    for row in authority["all_files"]:
        p = ROOT / row["path"]
        if row["mode"] == "120000":
            assert p.is_symlink()
            data = os.readlink(p).encode()
        else:
            assert not p.is_symlink() and stat.S_ISREG(p.stat().st_mode)
            assert bool(p.stat().st_mode & 0o111) == (row["mode"] == "100755")
            data = _pypdf_parent_bytes(p, p.read_bytes())
        data = restore_identification_parent(row["path"], data)
        assert len(data) == row["bytes"], row["path"]
        assert hashlib.sha256(data).hexdigest() == row["sha256"], row["path"]


def test_all9_complete_source_copies_restore_exact_original_bytes():
    assert len(PLAN["copies"]) == 9
    for row in PLAN["copies"]:
        raw = _pypdf_parent_bytes(ROOT / row["new_path"], (ROOT / row["new_path"]).read_bytes())
        assert hashlib.sha256(raw).hexdigest() == row["new_sha256"], row["new_path"]
        for rule in reversed(row["substitutions"]):
            assert raw.count(rule["new"].encode()) == rule["count"], row["new_path"]
            raw = raw.replace(rule["new"].encode(), rule["old"].encode())
        assert hashlib.sha256(raw).hexdigest() == row["old_sha256"], row["old_path"]
        if row["source_kind"] == "repo":
            assert raw == _pypdf_parent_bytes(ROOT / row["old_path"], (ROOT / row["old_path"]).read_bytes())


@pytest.mark.parametrize("name", sorted(PLAN["parent_recipes"]))
@pytest.mark.parametrize("mutation", ["append", "drop", "duplicate", "change"])
def test_outer_inverse_rejects_any_unapproved_old_file_change(name, mutation):
    raw = _pypdf_parent_bytes(ROOT / name, (ROOT / name).read_bytes())
    changed = (raw + b"\n# UNAPPROVED\n" if mutation == "append" else raw[1:]
               if mutation == "drop" else raw + raw if mutation == "duplicate"
               else bytes([raw[0] ^ 1]) + raw[1:])
    with pytest.raises(AssertionError):
        restore_identification_parent(name, changed)


def test_query_has_single_exact_inverse_not_an_OID_admission_waiver():
    rows = [r for r in PLAN["copies"] if r["old_path"] == "fixed-function-identification.sql"]
    assert len(rows) == 1 and len(rows[0]["substitutions"]) == 1
    assert rows[0]["substitutions"][0]["count"] == 1
    assert rows[0]["old_sha256"] == "9ed77c234669c6608e68c3b6adb3cf830239912238af7adc1b6a5ff6e287b74f"
    assert rows[0]["new_sha256"] == "9c1c9c4f9681c407d7e76d4fca272957ce3c5a069b025c97b6095716a33678ed"
    from mfdataindia.review import reader_function_identification_inventory as inv
    assert inv.IDENTIFICATION_SQL.encode() == _pypdf_parent_bytes(ROOT / rows[0]["new_path"], (ROOT / rows[0]["new_path"]).read_bytes())


def test_both_image_old_commands_exact_and_only_three_new_commands():
    row = PLAN["parent_recipes"][".github/workflows/verify.yml"]
    assert len(row["substitutions"]) == 1
    rule = row["substitutions"][0]
    assert rule["new"].count("docker run --rm") - rule["old"].count("docker run --rm") == 3
    raw = _pypdf_parent_bytes(ROOT / ".github/workflows/verify.yml", (ROOT / ".github/workflows/verify.yml").read_bytes())
    assert hashlib.sha256(restore_identification_parent(".github/workflows/verify.yml", raw)).hexdigest() == row["old_sha256"]


def test_actual_packaged_worker_entrypoint_smoke_has_no_secret_provider_or_SQL():
    env = dict(os.environ, DEPLOYMENT_GIT_SHA="a" * 40)
    completed = subprocess.run(
        ["uv", "run", "python", "scripts/smoke_reader_function_identification_binding.py"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    value = json.loads(completed.stdout)
    assert value["packaged_reader_function_identification_entrypoint"] == "PASS"
    assert value["provider_requests"] == value["database_queries"] == 0
    assert value["runtime_or_write_authority"] is False
