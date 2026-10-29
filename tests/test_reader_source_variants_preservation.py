"""The source-reference extension changes no previously tracked byte or mode."""

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PARENT = "016e63afb7b82aa1369b97bfdc63a729524d0c1d"


def restore_dependency_source(raw):
    recipe = (
        ROOT / "docs/CI_SOURCE_REFERENCE_DEPENDENCY_CORRECTION_LITERALS_20261029.json"
    ).read_bytes()
    assert (
        hashlib.sha256(recipe).hexdigest()
        == "23e56ed93cd4ef57174552dccae3b4d737c21c639ae426788f6281a09bfb870a"
    )
    plan = json.loads(recipe)
    assert plan["parent"] == PARENT and plan["path"] == "tests/reader_source_reference.py"
    assert len(plan["replacements"]) == 3
    for pair in reversed(plan["replacements"]):
        old, new = pair["old"].encode(), pair["new"].encode()
        assert raw.count(new) == 1
        raw = raw.replace(new, old, 1)
    return raw


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
            if name == b"tests/reader_source_reference.py":
                raw = restore_dependency_source(raw)
        assert (
            hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == expected
        ), name


def test_exact_three_dependency_inverses_restore_entire_original_compiler():
    original = subprocess.check_output(
        ["git", "show", PARENT + ":tests/reader_source_reference.py"], cwd=ROOT
    )
    current = (ROOT / "tests/reader_source_reference.py").read_bytes()
    assert current != original
    assert restore_dependency_source(current) == original
