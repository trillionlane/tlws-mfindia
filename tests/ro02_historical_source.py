"""Finite current-source inverse for historical assertions only.

No runtime import, ignored region, normalized SQL, updated historical hash or
whole-repository freeze. New behavior has independent execution regressions.
"""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import subprocess
from ro03_historical_source import previous as ro03_previous

ROOT = Path(__file__).resolve().parents[1]
BASE = "d0deb7102d505a8a0ffb8c2af802ba2b4c121dab"
MANIFEST_SHA = "19329bab7ccd0fba6fc14e5d39d14cc08de751211dc8d2c30062817c7f182acb"


def authority():
    raw = (ROOT / "docs/RO02_CURRENT_SOURCE_CHANGES_20261031.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == MANIFEST_SHA
    value = json.loads(raw)
    assert value["base"] == BASE and value["historical_only"] is True
    return value["modified"]


@lru_cache(maxsize=64)
def _old(name):
    return subprocess.check_output(["git", "show", BASE + ":" + name], cwd=ROOT)


def previous(path, data):
    assert type(data) is bytes
    try:
        name = Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return data
    row = authority().get(name)
    if row is None:
        return ro03_previous(path, data)
    digest = hashlib.sha256(data).hexdigest()
    # Historical chains may call this layer more than once.  Preserve either
    # known RO-02 state directly; only ask the RO-03 inverse to translate an
    # actual current RO-03 file into the reviewed RO-02 current state.
    if digest not in {row["previous_sha256"], row["current_sha256"]}:
        data = ro03_previous(path, data)
        digest = hashlib.sha256(data).hexdigest()
    if digest == row["previous_sha256"]:
        return data
    assert digest == row["current_sha256"], name
    old = _old(name)
    assert hashlib.sha256(old).hexdigest() == row["previous_sha256"]
    return old
