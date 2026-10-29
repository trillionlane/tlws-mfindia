"""Exact immediate-parent inverse for the disposable book-view fixture only."""

import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PARENT = "4f8468a33a2843ee9758a5ffd9a49b83cf0e8c0d"
RECIPE = ROOT / "docs/CI_SOURCE_REFERENCE_BOOK_PRIOR_VIEW_PRESERVATION_20261029_v7.json"
RECIPE_SHA256 = "683a51696ca4dfaecbf084b97ed4611192f37608218ee812672afc16af92192a"
CHANGED = frozenset({
    "docs/CI_SOURCE_REFERENCE_BOOK_VARIANT_AUTHORITY_20261029.json",
    "tests/reader_source_variants.py",
    "tests/test_reader_source_variants.py",
    "tests/test_reader_source_variants_postgres.py",
    "tests/test_reader_source_variants_preservation.py",
    "tests/test_reader_source_immediate_parent_preservation.py",
    "tests/reader_source_packets_preservation.py",
})


def restore_prior_view_parent(raw, path):
    assert type(raw) is bytes and path in CHANGED
    recipe_raw = RECIPE.read_bytes()
    assert hashlib.sha256(recipe_raw).hexdigest() == RECIPE_SHA256
    recipe = json.loads(recipe_raw)
    assert set(recipe) == {"format", "parent", "files"}
    assert recipe["format"] == "CI_BOOK_PRIOR_VIEW_EXACT_PARENT_BYTES_MODES_V7"
    assert recipe["parent"] == PARENT and set(recipe["files"]) == CHANGED
    record = recipe["files"][path]
    assert set(record) == {"mode", "parent_sha256", "current_sha256"}
    assert record["mode"] == "100644"
    assert hashlib.sha256(raw).hexdigest() == record["current_sha256"], path
    original = subprocess.check_output(["git", "show", PARENT + ":" + path], cwd=ROOT)
    assert hashlib.sha256(original).hexdigest() == record["parent_sha256"], path
    assert raw != original, path
    return original
