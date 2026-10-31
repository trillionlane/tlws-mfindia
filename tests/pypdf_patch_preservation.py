"""One finite test-only inverse; no runtime source authority or global hooks."""

import hashlib
import json
from pathlib import Path

from ro01_historical_source import historical_parent

ROOT = Path(__file__).resolve().parents[1]
PLAN_SHA = "b415b3a256aff8883d6f4ffe3f3e4dd18f1e8b95f7b35708640632cc7fa2a5c8"
PLAN_BYTES = (ROOT / "docs/PYPDF_SECURITY_REMEDIATION_LITERAL_PLAN_20261030.json").read_bytes()
assert hashlib.sha256(PLAN_BYTES).hexdigest() == PLAN_SHA
PLAN = json.loads(PLAN_BYTES)
PATCH_MODIFIED = frozenset(PLAN["modified"])
PATCH_ADDED = frozenset(PLAN["added"])
assert len(PATCH_MODIFIED) == 26 and len(PATCH_ADDED) == 11
assert not PATCH_MODIFIED & PATCH_ADDED


def restore_pypdf_parent(path, data):
    assert type(data) is bytes
    try:
        name = Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return data
    if name in PATCH_MODIFIED and hashlib.sha256(data).hexdigest() == PLAN["modified"][name]["old_full_sha256"]:
        return data
    data = historical_parent(path, data)
    if name not in PATCH_MODIFIED:
        return data
    recipe = PLAN["modified"][name]
    for change in reversed(recipe["substitutions"]):
        old, new = change["old"].encode(), change["new"].encode()
        count = change["count"]
        assert type(count) is int and count > 0 and old and new and old != new
        assert data.count(new) == count, name
        data = data.replace(new, old)
    assert hashlib.sha256(data).hexdigest() == recipe["old_full_sha256"], name
    return data
