"""Versioned exact-byte receipt for all six evolved CI files.

The recipe is externally pinned by this NEW test, so the changed historical
preservation helper does not have to hash itself. Older 981-file restoration
checks remain independent and unchanged.
"""

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "docs/CI_SOURCE_REFERENCE_IMMEDIATE_PARENT_PRESERVATION_20261029_v5.json"
RECIPE_SHA256 = "10a3db9c923f06b1a92b63f38d4f287e92d23d74162246f8ffa4a7a74b09e0f0"
PARENT = "a8218d7747b6222a55d373c8c4a33bda23eec4e8"
CHANGED = {
    "tests/reader_source_packets.py",
    "tests/reader_source_packets_preservation.py",
    "tests/test_reader_source_packets.py",
    "tests/test_reader_source_reference_postgres.py",
    "tests/test_reader_source_variants_postgres.py",
    "tests/test_reader_source_variants_preservation.py",
}
ADDED = {
    "docs/CI_SOURCE_REFERENCE_IMMEDIATE_PARENT_PRESERVATION_20261029_v5.json",
    "tests/test_reader_source_immediate_parent_preservation.py",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def test_exact_immediate_parent_bytes_modes_and_finite_delta():
    raw_recipe = RECIPE.read_bytes()
    assert sha(raw_recipe) == RECIPE_SHA256
    recipe = json.loads(raw_recipe)
    assert set(recipe) == {"format", "parent", "files"}
    assert recipe["format"] == "CI_SOURCE_REFERENCE_IMMEDIATE_PARENT_FULL_BYTE_MODE_RECEIPT_V5"
    assert recipe["parent"] == PARENT and set(recipe["files"]) == CHANGED
    delta = subprocess.check_output(
        ["git", "diff", "--name-status", "--no-renames", PARENT + "..HEAD"], cwd=ROOT
    ).decode().splitlines()
    assert set(delta) == {"M\t" + path for path in CHANGED} | {
        "A\t" + path for path in ADDED
    }
    for name, record in recipe["files"].items():
        assert set(record) == {"mode", "parent_sha256", "current_sha256"}
        assert record["mode"] == "100644"
        parent_raw = subprocess.check_output(["git", "show", PARENT + ":" + name], cwd=ROOT)
        assert sha(parent_raw) == record["parent_sha256"]
        entry = subprocess.check_output(["git", "ls-tree", PARENT, name], cwd=ROOT)
        assert entry.startswith(b"100644 blob ") and entry.endswith(name.encode() + b"\n")
        path = ROOT / name
        assert path.is_file() and not path.is_symlink()
        assert stat.S_ISREG(path.stat().st_mode) and not (os.stat(path).st_mode & 0o111)
        assert sha(path.read_bytes()) == record["current_sha256"]
