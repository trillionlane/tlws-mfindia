"""The source-reference extension changes no previously tracked byte or mode."""

import hashlib
import os
from pathlib import Path
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PARENT = "016e63afb7b82aa1369b97bfdc63a729524d0c1d"


def test_all981_parent_whole_bytes_and_modes_remain_exact():
    records = subprocess.check_output(["git", "ls-tree", "-r", "-z", PARENT], cwd=ROOT).split(b"\0")
    records = [r for r in records if r]
    assert len(records) == 981
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
        assert (
            hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == expected
        ), name
