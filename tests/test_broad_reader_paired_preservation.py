"""Independent complete parent and copy bytes, including the modified guard."""
import base64
import gzip
import hashlib
import json
import os
import stat
import subprocess

import pytest

from broad_reader_paired_preservation import PLAN, ROOT, restore_paired_parent
import ci_risk_preservation as old_preservation


def test_all907_parent_whole_bytes_type_mode_and_changed_guard():
    raw = gzip.decompress(base64.b64decode(PLAN["parent_authority_gzip_base64"]))
    assert hashlib.sha256(raw).hexdigest() == PLAN["parent_authority_sha256"]
    authority = json.loads(raw)
    assert PLAN["base"] == "cda143a1778218eb401388e7e7ddd450f2a4e927"
    assert len(authority) == PLAN["tracked_parent_count"] == 907
    assert set(PLAN["parent_recipes"]) == {".github/workflows/verify.yml", "tests/broad_capture_successor_preservation.py"}
    for name, expected in authority.items():
        path = ROOT / name
        if expected["mode"] == "120000":
            assert path.is_symlink(), name
            data = os.readlink(path).encode()
        else:
            assert not path.is_symlink() and stat.S_ISREG(path.stat().st_mode), name
            assert bool(path.stat().st_mode & 0o111) == (expected["mode"] == "100755"), name
            data = path.read_bytes()
        if name in old_preservation.PRESENTATION:
            data = old_preservation.restore_policy_parent(name, data)
        else:
            data = restore_paired_parent(name, data)
        assert hashlib.sha256(data).hexdigest() == expected["sha256"], name


def test_all18_full_copies_restore_exact_complete_original_sources():
    assert len(PLAN["copies"]) == 18
    for row in PLAN["copies"]:
        raw = (ROOT / row["new_path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["new_sha256"]
        for rule in reversed(row["substitutions"]):
            assert raw.count(rule["new"].encode()) == rule["count"]
            raw = raw.replace(rule["new"].encode(), rule["old"].encode())
        assert raw == (ROOT / row["old_path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["old_sha256"]


@pytest.mark.parametrize("name", sorted(PLAN["parent_recipes"]))
@pytest.mark.parametrize("mutation", ["append", "drop", "duplicate", "change"])
def test_new_inverse_does_not_ignore_any_unapproved_bytes(name, mutation):
    raw = (ROOT / name).read_bytes()
    raw = raw + b"\n# UNAPPROVED\n" if mutation == "append" else raw[1:] if mutation == "drop" else raw + raw if mutation == "duplicate" else bytes([raw[0] ^ 1]) + raw[1:]
    with pytest.raises(AssertionError):
        restore_paired_parent(name, raw)


@pytest.mark.parametrize("script,key", [
    ("smoke_broad_reader_capture_v3_binding.py", "packaged_broad_capture_v3_entrypoint"),
    ("smoke_broad_reader_paired_binding.py", "packaged_broad_reader_capture_owner_binding"),
])
def test_actual_packaged_paired_smokes_have_no_secret_or_provider_io(script, key):
    completed = subprocess.run(["uv", "run", "python", "scripts/" + script], cwd=ROOT,
        env=dict(os.environ, DEPLOYMENT_GIT_SHA="a" * 40), capture_output=True, text=True, check=True)
    value = json.loads(completed.stdout)
    assert value[key] == "PASS"
    assert value["provider_requests"] == value["database_queries"] == 0
    assert value["runtime_or_write_authority"] is False


def test_both_image_old_commands_prefix_and_only_six_new_commands():
    raw = (ROOT / ".github/workflows/verify.yml").read_bytes()
    row = PLAN["parent_recipes"][".github/workflows/verify.yml"]
    assert len(row["substitutions"]) == 1
    rule = row["substitutions"][0]
    assert rule["new"].startswith(rule["old"])
    assert rule["new"][len(rule["old"]):].count("docker run --rm") == 6
    assert hashlib.sha256(restore_paired_parent(".github/workflows/verify.yml", raw)).hexdigest() == row["old_sha256"]
