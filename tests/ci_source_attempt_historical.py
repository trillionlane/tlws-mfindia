"""Finite full-byte test-only source version; not permission/runtime authority."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE = "69037c0ec078ad67a8dfd708bc3cf86040b655e6"
MANIFEST_SHA = "74cd9092c03bddd4829e3d5e61e51fb89406f310d99acd44654362a3e8d17a0d"


def authority():
    raw = (ROOT / "docs/CI_SOURCE_ATTEMPT_CURRENT_CHANGES_20261031.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == MANIFEST_SHA
    value = json.loads(raw)
    assert value["format"] == "test-only-current-CI-source-version-v1"
    assert value["base"] == BASE and value["historical_only"] is True
    assert value["runtime_authority"] is False and len(value["modified"]) == 5
    return value["modified"]


def restore_ci_source_parent(path, data):
    assert type(data) is bytes
    try:
        name = Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return data
    row = authority().get(name)
    if row is None:
        return data
    actual = hashlib.sha256(data).hexdigest()
    if actual == row["previous_sha256"]:
        return data
    assert actual == row["current_sha256"] and len(data) == row["current_bytes"], name
    previous = _previous(name)
    assert hashlib.sha256(previous).hexdigest() == row["previous_sha256"]
    assert len(previous) == row["previous_bytes"]
    return previous


@lru_cache(maxsize=16)
def _previous(name):
    return subprocess.check_output(["git", "show", BASE + ":" + name], cwd=ROOT)
