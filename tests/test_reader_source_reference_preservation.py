"""All parent bytes and modes unchanged in the non-mergeable calibration slice."""

from pypdf_patch_preservation import restore_pypdf_parent as _pypdf_parent_bytes
import hashlib
import os
from pathlib import Path
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[1]
BASE = "c550e3c21482f45684a1b63b7c70fc84b8a62b50"


def test_all972_parent_tracked_whole_bytes_and_modes_unchanged():
    records = subprocess.check_output(["git", "ls-tree", "-r", "-z", BASE], cwd=ROOT).split(b"\0")
    records = [r for r in records if r]
    assert len(records) == 972
    for record in records:
        identity, raw_name = record.split(b"\t", 1)
        mode, kind, oid = identity.decode().split()
        assert kind == "blob"
        name = raw_name.decode()
        path = ROOT / name
        if mode == "120000":
            assert path.is_symlink(), name
            raw = os.readlink(path).encode()
        else:
            assert not path.is_symlink() and stat.S_ISREG(path.stat().st_mode), name
            assert bool(path.stat().st_mode & 0o111) == (mode == "100755"), name
            raw = _pypdf_parent_bytes(path, path.read_bytes())
        actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        assert actual == oid, name
