"""Finite whole-byte paired inverse before unchanged historical inverse layers."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = (ROOT / "docs/BROAD_READER_PAIRED_FROZEN_PLAN_20261027.json").read_bytes()
assert hashlib.sha256(RAW).hexdigest() == "2c52ee6af29a6d51f0b8109c490fd98ddc603d84d861693ece136104cdf0c908"
PLAN = json.loads(RAW)


def restore_paired_parent(name, data):
    if name not in PLAN["parent_recipes"]:
        return data
    row = PLAN["parent_recipes"][name]
    actual = hashlib.sha256(data).hexdigest()
    if name == ".github/workflows/verify.yml" and actual in PLAN["exact_historical_workflow_stages"]:
        return data  # Exact whole historical stages only, never ignored regions.
    assert actual in (row["old_sha256"], row["new_sha256"]), name
    if actual == row["new_sha256"]:
        for rule in reversed(row["substitutions"]):
            old, new = rule["old"].encode(), rule["new"].encode()
            assert data.count(new) == rule["count"], name
            data = data.replace(new, old)
    assert hashlib.sha256(data).hexdigest() == row["old_sha256"], name
    return data
