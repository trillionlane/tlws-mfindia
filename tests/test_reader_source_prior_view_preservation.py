"""New whole-tree proof while retaining every older full-byte inverse."""

import hashlib
import os
from pathlib import Path
import stat
import subprocess

from reader_source_prior_view_preservation import (
    CHANGED, PARENT, restore_prior_view_parent,
)

ROOT = Path(__file__).resolve().parents[1]
ADDED = {
    "docs/CI_SOURCE_REFERENCE_BOOK_PRIOR_VIEW_PRESERVATION_20261029_v7.json",
    "tests/reader_source_prior_view_preservation.py",
    "tests/test_reader_source_prior_view_preservation.py",
    "tests/reader_source_view_locks.py",
    "tests/test_reader_source_view_locks_postgres.py",
}


def test_complete_998_file_immediate_parent_bytes_modes_and_finite_delta():
    delta = subprocess.check_output(
        ["git", "diff", "--name-status", "--no-renames", PARENT + "..HEAD"], cwd=ROOT
    ).decode().splitlines()
    assert set(delta) == {"M\t" + path for path in CHANGED} | {
        "A\t" + path for path in ADDED
    } and len(delta) == len(CHANGED) + len(ADDED)
    records = subprocess.check_output(["git", "ls-tree", "-r", "-z", PARENT], cwd=ROOT).split(b"\0")
    records = [record for record in records if record]
    assert len(records) == 998
    for record in records:
        identity, name = record.split(b"\t", 1)
        mode, kind, expected = identity.decode().split()
        path = ROOT / name.decode()
        assert kind == "blob"
        if mode == "120000":
            assert path.is_symlink()
            raw = os.readlink(path).encode()
        else:
            assert path.is_file() and not path.is_symlink() and stat.S_ISREG(path.stat().st_mode)
            assert bool(path.stat().st_mode & 0o111) == (mode == "100755")
            raw = path.read_bytes()
            if name.decode() in CHANGED:
                raw = restore_prior_view_parent(raw, name.decode())
        assert hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == expected, name
