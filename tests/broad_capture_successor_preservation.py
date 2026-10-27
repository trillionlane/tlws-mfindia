"""Finite complete-byte inverse before unchanged historical preservation layers."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = (ROOT / "docs/BROAD_CAPTURE_SUCCESSOR_FROZEN_PLAN_20261027.json").read_bytes()
assert (
    hashlib.sha256(RAW).hexdigest()
    == "2b7f341df9f0a58df88552ab5874f7da703c929839279e5449563c9e05916782"
)
PLAN = json.loads(RAW)


def restore_successor_parent(name, data):
    if name not in PLAN["parent_recipes"]:
        return data
    row = PLAN["parent_recipes"][name]
    actual = hashlib.sha256(data).hexdigest()
    if name == ".github/workflows/verify.yml" and actual == PLAN["historical_restored_workflow_sha256"]:
        return data  # Exact whole historical stage only, for nested old inverses.
    assert actual in (row["old_sha256"], row["new_sha256"]), name
    if actual == row["new_sha256"]:
        for rule in reversed(row["substitutions"]):
            old, new = rule["old"].encode(), rule["new"].encode()
            assert data.count(new) == rule["count"], name
            data = data.replace(new, old)
    assert hashlib.sha256(data).hexdigest() == row["old_sha256"], name
    return data
