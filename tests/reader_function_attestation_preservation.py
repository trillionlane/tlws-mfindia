"""Finite full-byte attestation inverse before unchanged older inverse layers."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = (ROOT / "docs/FUNCTION_ATTESTATION_FROZEN_PLAN_20261028.json").read_bytes()
assert hashlib.sha256(RAW).hexdigest() == "9f5411ca4255729dfb7e87a65f0171a4ea619ac477aba3ba23b679768ff5a999"
PLAN = json.loads(RAW)


def restore_attestation_parent(name, data):
    if name not in PLAN["parent_recipes"]:
        return data
    row = PLAN["parent_recipes"][name]
    actual = hashlib.sha256(data).hexdigest()
    if name == ".github/workflows/verify.yml" and actual in PLAN["exact_historical_workflow_stages"]:
        return data
    assert actual in (row["old_sha256"], row["new_sha256"]), name
    if actual == row["new_sha256"]:
        for rule in reversed(row["substitutions"]):
            old, new = rule["old"].encode(), rule["new"].encode()
            assert data.count(new) == rule["count"], name
            data = data.replace(new, old)
    assert hashlib.sha256(data).hexdigest() == row["old_sha256"], name
    return data
