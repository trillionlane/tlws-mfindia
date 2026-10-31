"""Historical release assertions, not current-source or runtime acceptance.

RO-01 deliberately changes listed current sources. Validate their complete new
bytes before returning the committed predecessor for older release proofs.
Current behavior is separately exercised by the RO-01 native/API regressions.
There is no ignored source region, monkeypatch, or runtime permission bypass.
"""

import hashlib
import json
from functools import lru_cache
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE = "89af628c88f4b905d2136acc7a222683c672fa44"
MANIFEST_SHA = "0fe263ced7456a6ef47d91e3355be48378748c8c7eb02135e36a39f76eb31549"


def authority():
    raw = (ROOT / "docs/RO01_CURRENT_SOURCE_CHANGES_20261031.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == MANIFEST_SHA
    value = json.loads(raw)
    assert value["base"] == BASE and value["historical_only"] is True
    return value["modified"]


def historical_parent(path, data):
    assert type(data) is bytes
    try:
        name = Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return data
    rows = authority()
    if name not in rows:
        return data
    row = rows[name]
    digest = hashlib.sha256(data).hexdigest()
    if digest == row["previous_sha256"]:
        return data
    assert digest == row["current_sha256"], name
    previous = _previous(name)
    assert hashlib.sha256(previous).hexdigest() == row["previous_sha256"]
    return previous


@lru_cache(maxsize=128)
def _previous(name):
    # Cache immutable Git objects only. Every supplied current byte string is
    # still independently checked, including all tamper regressions.
    return subprocess.check_output(["git", "show", BASE + ":" + name], cwd=ROOT)
