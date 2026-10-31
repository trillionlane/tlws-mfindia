"""Independent full parent bytes, modes, copy inverses and mutation negatives."""

import hashlib
import os
import stat
import subprocess

import pytest

from pypdf_patch_preservation import (
    PATCH_ADDED, PATCH_MODIFIED, PLAN, PLAN_BYTES, PLAN_SHA, ROOT, restore_pypdf_parent,
)

BASE = "03f3932eb22218478f82e7985e1d0ba527f22cde"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def test_all_1059_parent_bytes_modes_and_exact_finite_patch():
    assert sha(PLAN_BYTES) == PLAN_SHA and PLAN["base"] == BASE
    records = [r for r in git("ls-tree", "-rz", BASE).split(b"\0") if r]
    assert len(records) == 1059
    restored = set()
    for record in records:
        identity, name = record.split(b"\t", 1)
        mode, kind, blob = identity.decode().split()
        name = name.decode()
        path = ROOT / name
        assert kind == "blob"
        if mode == "120000":
            assert path.is_symlink()
            raw = os.readlink(path).encode()
        else:
            assert path.is_file() and not path.is_symlink()
            assert stat.S_ISREG(path.stat().st_mode)
            assert bool(path.stat().st_mode & 0o111) == (mode == "100755")
            raw = path.read_bytes()
        if name in PATCH_MODIFIED:
            recipe = PLAN["modified"][name]
            assert mode == recipe["mode"]
            assert sha(raw) == recipe["new_full_sha256"], name
            raw = restore_pypdf_parent(path, raw)
            assert sha(raw) == recipe["old_full_sha256"]
            restored.add(name)
        assert hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == blob, name
    assert restored == PATCH_MODIFIED and len(restored) == 26
    changed = set(git("diff", "--name-only", BASE).decode().splitlines())
    untracked = set(git("ls-files", "--others", "--exclude-standard").decode().splitlines())
    assert changed | untracked == PATCH_MODIFIED | PATCH_ADDED
    for name in PATCH_ADDED:
        path = ROOT / name
        assert path.is_file() and not path.is_symlink()
        assert stat.S_ISREG(path.stat().st_mode) and not path.stat().st_mode & 0o111


@pytest.mark.parametrize("recipe", PLAN["whole_copies"], ids=lambda r: r["new_path"])
def test_entire_six_copy_inverses(recipe):
    raw = (ROOT / recipe["new_path"]).read_bytes()
    assert sha(raw) == recipe["new_full_sha256"]
    for change in reversed(recipe["substitutions"]):
        old, new = change["old"].encode(), change["new"].encode()
        assert raw.count(new) == change["count"]
        raw = raw.replace(new, old)
    original = git("show", BASE + ":" + recipe["old_path"])
    assert raw == original and sha(raw) == recipe["old_full_sha256"]


@pytest.mark.parametrize("name", sorted(PATCH_MODIFIED))
@pytest.mark.parametrize("mutation", ["append", "drop", "double", "substitute"])
def test_all_modified_guards_and_functional_bytes_reject_tamper(name, mutation):
    raw = (ROOT / name).read_bytes()
    changed = {"append": raw + b"\n# tamper", "drop": raw[:-1],
               "double": raw + raw, "substitute": bytes([raw[0] ^ 1]) + raw[1:]}[mutation]
    with pytest.raises(AssertionError):
        restore_pypdf_parent(ROOT / name, changed)


def test_unknown_paths_are_not_normalized_and_original_idempotence_is_exact(tmp_path):
    raw = b"synthetic unchanged bytes"
    assert restore_pypdf_parent(tmp_path / "pyproject.toml", raw) is raw
    assert restore_pypdf_parent(ROOT / "unlisted", raw) is raw
    for name in PATCH_MODIFIED:
        old = git("show", BASE + ":" + name)
        assert restore_pypdf_parent(ROOT / name, old) is old
    with pytest.raises(AssertionError):
        restore_pypdf_parent(ROOT / "pyproject.toml", bytearray(raw))
