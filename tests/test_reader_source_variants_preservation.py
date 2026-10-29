"""The source-reference extension changes no previously tracked byte or mode."""

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

from test_reader_source_trigger import TRIGGER_RECIPE_SHA

ROOT = Path(__file__).resolve().parents[1]
PARENT = "016e63afb7b82aa1369b97bfdc63a729524d0c1d"
TRIGGER_PARENT = "c7c1b1f62ddb54af926907537ecbbd7055a38053"
TRIGGER_PATHS = {"tests/reader_source_reference.py", "tests/reader_source_variants.py",
                 "tests/test_reader_source_variants_preservation.py"}


def restore_trigger_source(raw, path):
    recipe = (ROOT / "docs/CI_SOURCE_REFERENCE_PG_TRIGGER_CORRECTION_LITERALS_20261029.json").read_bytes()
    assert hashlib.sha256(recipe).hexdigest() == TRIGGER_RECIPE_SHA
    plan = json.loads(recipe)
    assert set(plan) == {"format", "parent", "files"}
    assert plan["format"] == "EXACT_COUNTED_TEST_ONLY_PG_TRIGGER_LITERAL_INVERSES"
    assert plan["parent"] == TRIGGER_PARENT and set(plan["files"]) == TRIGGER_PATHS
    assert {name: len(pairs) for name, pairs in plan["files"].items()} == {
        "tests/reader_source_reference.py": 8,
        "tests/reader_source_variants.py": 2,
        "tests/test_reader_source_variants_preservation.py": 4,
    }
    assert all(type(pair) is dict and set(pair) == {"old", "new"}
               and type(pair["old"]) is str and type(pair["new"]) is str
               and pair["new"] for pairs in plan["files"].values() for pair in pairs)
    assert path in TRIGGER_PATHS
    for pair in reversed(plan["files"][path]):
        old, new = pair["old"].encode(), pair["new"].encode()
        assert raw.count(new) == 1
        raw = raw.replace(new, old, 1)
    return raw


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
            if name.decode() in TRIGGER_PATHS:
                raw = restore_trigger_source(raw, name.decode())
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
    assert restore_dependency_source(restore_trigger_source(current, "tests/reader_source_reference.py")) == original


def test_all990_c7_whole_bytes_and_modes_remain_exact_after_trigger_inverse():
    delta = subprocess.check_output(
        ["git", "diff", "--name-status", TRIGGER_PARENT + "..HEAD"], cwd=ROOT
    ).decode().splitlines()
    assert set(delta) == {
        "A\tdocs/CI_SOURCE_REFERENCE_PG_TRIGGER_CORRECTION_LITERALS_20261029.json",
        "M\ttests/reader_source_reference.py",
        "M\ttests/reader_source_variants.py",
        "A\ttests/test_reader_source_trigger.py",
        "M\ttests/test_reader_source_variants_preservation.py",
    } and len(delta) == 5
    records = subprocess.check_output(["git", "ls-tree", "-r", "-z", TRIGGER_PARENT], cwd=ROOT).split(b"\0")
    records = [r for r in records if r]
    assert len(records) == 990
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
            if name.decode() in TRIGGER_PATHS:
                raw = restore_trigger_source(raw, name.decode())
        assert hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == expected, name
