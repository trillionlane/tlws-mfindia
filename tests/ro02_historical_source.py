"""Finite current-source inverse for historical assertions only.

No runtime import, ignored region, normalized SQL, updated historical hash or
whole-repository freeze. New behavior has independent execution regressions.
"""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE = "69037c0ec078ad67a8dfd708bc3cf86040b655e6"
MANIFEST_SHA = "dfc5c883b1ab6bb431581f53f8f1d183437a07f810b1e6c79a23c7675b2307b4"


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
        return data
    digest = hashlib.sha256(data).hexdigest()
    if digest == row["previous_sha256"]:
        return data
    assert digest == row["current_sha256"], name
    old = _old(name)
    assert hashlib.sha256(old).hexdigest() == row["previous_sha256"]
    return old
