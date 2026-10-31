"""Restore every immediate-parent byte and mode under only the reviewed edits."""

from pypdf_patch_preservation import (
    PATCH_ADDED, PATCH_MODIFIED, restore_pypdf_parent as _pypdf_parent_bytes,
)

import hashlib
import os
from pathlib import Path
import stat
import subprocess

from broad_reader_runtime_environment_preservation import (
    MODIFIED, NEW_ADDED, restore_runtime_parent,
)


ROOT = Path(__file__).resolve().parents[1]
PARENT = "fdc1d73c748972265aed99391508b91e34a8f56e"


def git(*arguments):
    return subprocess.check_output(["git", *arguments], cwd=ROOT)


def test_all_1056_parent_full_bytes_modes_and_exact_repair_delta():
    records = [row for row in git("ls-tree", "-r", "-z", PARENT).split(b"\0") if row]
    assert len(records) == 1056
    names = set()
    restored = set()
    for record in records:
        identity, encoded = record.split(b"\t", 1)
        mode, kind, blob = identity.decode().split()
        name = encoded.decode()
        names.add(name)
        path = ROOT / name
        assert kind == "blob", name
        if mode == "120000":
            assert path.is_symlink(), name
            raw = os.readlink(path).encode()
        else:
            assert path.is_file() and not path.is_symlink(), name
            assert stat.S_ISREG(path.stat().st_mode), name
            assert bool(path.stat().st_mode & 0o111) == (mode == "100755"), name
            raw = _pypdf_parent_bytes(path, path.read_bytes())
        original = restore_runtime_parent(name, raw)
        if name in MODIFIED:
            assert original != raw, name
            restored.add(name)
        else:
            assert original == raw, name
        actual = hashlib.sha1(b"blob " + str(len(original)).encode() + b"\0" + original).hexdigest()
        assert actual == blob, name
    assert len(MODIFIED) == 6 and restored == MODIFIED and MODIFIED <= names
    assert len(NEW_ADDED) == 3 and not NEW_ADDED & names
    changed = set(git("diff", "--name-only", PARENT).decode().splitlines())
    untracked = set(git("ls-files", "--others", "--exclude-standard").decode().splitlines())
    assert changed | untracked == MODIFIED | NEW_ADDED | PATCH_MODIFIED | PATCH_ADDED
    for name in NEW_ADDED:
        path = ROOT / name
        assert path.is_file() and not path.is_symlink(), name
        assert stat.S_ISREG(path.stat().st_mode) and not path.stat().st_mode & 0o111, name
