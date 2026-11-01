"""RO-03 current-source inverse for historical release assertions only.

Current runtime/tests use the actual RO-03 bytes.  Older preservation chains
receive the exact reviewed RO-02 predecessor only after the whole current file
matches the versioned RO-03 authority.
"""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE = "d84748eeacc6c8cd16e12beeb6465ac8639a45ca"
MANIFEST_SHA = "5ddf61243b05d2d46d9f3306b802b03c8c08c9fd16e452a26573dbd5dad2307a"


def authority():
    raw = (ROOT / "docs/RO03_CURRENT_SOURCE_CHANGES_20261101.json").read_bytes()
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
        return data
    current = hashlib.sha256(data).hexdigest()
    if current == row["previous_sha256"]:
        return data
    assert current == row["current_sha256"], name
    old = _old(name)
    assert hashlib.sha256(old).hexdigest() == row["previous_sha256"]
    return old
