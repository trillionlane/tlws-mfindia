"""Exact immediate-parent byte/mode proof for the bounded DEV reader slice.

The previous source-reference preservation test is fixed to its already
reviewed b56 commit; its original full bytes are independently reconstructed
here. Every production/image source file remains byte-exact to that parent.
"""

import hashlib
import os
from pathlib import Path
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PARENT = "b56fe2ae0806fc0eaa80692be82c854bd429af84"
CHANGED = {
    "tests/test_reader_source_prior_view_preservation.py",
}
ADDED = {
    "src/mfdataindia/review/broad_reader_embedded_authority.py",
    "src/mfdataindia/review/broad_reader_dependency_scope.py",
    "src/mfdataindia/review/broad_reader_direct_grant.py",
    "src/mfdataindia/review/broad_reader_direct_plan.py",
    "src/mfdataindia/review/broad_reader_direct_reader_check.py",
    "src/mfdataindia/review/broad_reader_function_closure.py",
    "src/mfdataindia/review/broad_reader_function_scope.py",
    "src/mfdataindia/review/broad_reader_live_snapshot.py",
    "src/mfdataindia/review/broad_reader_lock_witness.py",
    "src/mfdataindia/review/broad_reader_source_authority.py",
    "src/mfdataindia/review/broad_reader_view_profile.py",
    "tests/test_broad_reader_dependency_scope.py",
    "tests/test_broad_reader_direct_grant.py",
    "tests/test_broad_reader_direct_grant_postgres.py",
    "tests/broad_reader_postcommit_diagnostic.py",
    "tests/test_broad_reader_postcommit_diagnostic.py",
    "tests/broad_reader_native_oid_isomorphism.py",
    "tests/test_broad_reader_native_oid_isomorphism.py",
    "tests/test_broad_reader_direct_plan.py",
    "tests/test_broad_reader_direct_reader_check.py",
    "tests/test_broad_reader_function_closure.py",
    "tests/test_broad_reader_function_closure_postgres.py",
    "tests/test_broad_reader_function_scope.py",
    "tests/test_broad_reader_live_snapshot.py",
    "tests/test_broad_reader_lock_witness.py",
    "tests/test_broad_reader_lock_witness_postgres.py",
    "tests/test_broad_reader_source_authority.py",
    "tests/test_broad_reader_view_profile.py",
    "tests/test_broad_reader_direct_parent_preservation.py",
}
_OLD_REF = b'PARENT + "..HEAD"'
_NEW_REF = b'PARENT + "..b56fe2ae0806fc0eaa80692be82c854bd429af84"'


def restore(raw, name):
    if name == "tests/test_reader_source_prior_view_preservation.py":
        assert raw.count(_NEW_REF) == 1 and _OLD_REF not in raw
        raw = raw.replace(_NEW_REF, _OLD_REF, 1)
    else:
        raise AssertionError(name)
    return raw


def test_every_parent_byte_and_mode_and_exact_finite_delta():
    delta = subprocess.check_output(
        ["git", "diff", "--name-status", "--no-renames", PARENT + "..HEAD"],
        cwd=ROOT).decode().splitlines()
    assert set(delta) == {"M\t" + name for name in CHANGED} | {
        "A\t" + name for name in ADDED}
    records = subprocess.check_output(
        ["git", "ls-tree", "-r", "-z", PARENT], cwd=ROOT).split(b"\0")
    records = [record for record in records if record]
    assert len(records) == 1003
    for record in records:
        identity, encoded_name = record.split(b"\t", 1)
        mode, kind, blob = identity.decode().split()
        name = encoded_name.decode()
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
            if name in CHANGED:
                raw = restore(raw, name)
        actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        assert actual == blob, name
    assert len(ADDED) == 29
    for name in ADDED:
        path = ROOT / name
        assert path.is_file() and not path.is_symlink()
        assert not (path.stat().st_mode & 0o111)
