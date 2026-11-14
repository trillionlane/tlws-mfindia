"""Finite exact-byte inverse for the reviewed DEV traffic reconciliation."""

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE = "3741d683c78447cc9f53890625b449f709423406"
MANIFEST_SHA256 = "d6a0c6c68417f119a9bc4cb4f59d99e8d0ca46e0306c43a8a24cb178604d6b24"


@lru_cache(maxsize=1)
def manifest():
    raw = (ROOT / "docs/TAG_DEPLOY_TRAFFIC_SOURCE_CHANGES_20261113.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == MANIFEST_SHA256
    value = json.loads(raw)
    assert value["base"] == BASE and value["historical_only"] is True
    return value["modified"]


@lru_cache(maxsize=16)
def _old(name):
    return subprocess.check_output(["git", "show", f"{BASE}:{name}"], cwd=ROOT)


def previous(path, data):
    assert type(data) is bytes
    try:
        name = Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return data
    row = manifest().get(name)
    if row is None:
        return data
    digest = hashlib.sha256(data).hexdigest()
    if digest == row["previous_sha256"]:
        return data
    assert digest == row["current_sha256"], name
    old = _old(name)
    assert hashlib.sha256(old).hexdigest() == row["previous_sha256"], name
    return old
