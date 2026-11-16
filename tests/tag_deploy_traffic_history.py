"""Finite exact-byte inverse for the reviewed DEV traffic reconciliation."""

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE = "3741d683c78447cc9f53890625b449f709423406"
MANIFEST_SHA256 = "2db476fa6c07a6ebb7566e97521a84c20306dcb864bf96b9f5124bd20919023b"
ISSUER_BINDING_BASE = "f8f67c137d5ed946ce8f9161b388d601b601ac07"
NAV_CONTINUITY = "src/mfdataindia/jobs/nav_continuity.py"
ISSUER_BINDING_NAV_SHA256 = "a66eb1b95290f89a17b39e908f12a8c17bfac0c3177b66b1b69d602f3e76ff3d"

TAG_STREAM_BASE = "f90f740302769ab93c0d0edcb02fdc677a13c8bf"
TAG_STORE = "src/mfdataindia/tagging/store.py"
TAG_UNIT = "tests/test_fund_tagging.py"
TAG_STREAM_UNIT_SHA256 = "3665519ed1cd29048a538dd9cbc5cf3f9b068b9a1466b96f185e0d98cea1c4a9"
TAG_STREAM_STORE_SHA256 = "aa352c5e48bdaaaa9e437bbfdcb7bc377ed186ab8c2544edc63188dba8530b08"
TAG_DEPLOY = ".github/workflows/deploy-dev.yml"
TAG_DEPLOY_SHA256 = "d3b707ecd9256bf95d2308cabc40167563f1e4ebb21dab5aa12fc502d6fc4d8f"
TAG_STATEMENT_BASE = "faf25bd5839a1a42c4ed10ab0fb3a0f501afd8cb"
TAG_STATEMENT_STORE_SHA256 = "799eba42bc5c0dfaca68e148fb483d2c22a4f6d342bec82c2da95fa0065b5987"


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


def before_issuer_binding(name, data):
    if name == TAG_STORE and hashlib.sha256(data).hexdigest() == TAG_STATEMENT_STORE_SHA256:
        data = subprocess.check_output(["git", "show", f"{TAG_STATEMENT_BASE}:{name}"], cwd=ROOT)
        assert hashlib.sha256(data).hexdigest() == TAG_STREAM_STORE_SHA256
    stream_hashes = {TAG_STORE: TAG_STREAM_STORE_SHA256, TAG_UNIT: TAG_STREAM_UNIT_SHA256, TAG_DEPLOY: TAG_DEPLOY_SHA256}
    if stream_hashes.get(name) == hashlib.sha256(data).hexdigest():
        restored = subprocess.check_output(["git", "show", f"{TAG_STREAM_BASE}:{name}"], cwd=ROOT)
        assert hashlib.sha256(restored).hexdigest() == manifest()[name]["current_sha256"]
        return restored
    if name != NAV_CONTINUITY or hashlib.sha256(data).hexdigest() != ISSUER_BINDING_NAV_SHA256:
        return data
    restored = subprocess.check_output(["git", "show", f"{ISSUER_BINDING_BASE}:{name}"], cwd=ROOT)
    assert hashlib.sha256(restored).hexdigest() == manifest()[name]["current_sha256"]
    return restored


def previous(path, data):
    assert type(data) is bytes
    try:
        name = Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return data
    row = manifest().get(name)
    if row is None:
        return data
    data = before_issuer_binding(name, data)
    digest = hashlib.sha256(data).hexdigest()
    if digest == row["previous_sha256"]:
        return data
    assert digest == row["current_sha256"], name
    old = _old(name)
    assert hashlib.sha256(old).hexdigest() == row["previous_sha256"], name
    return old
