"""Exact new broad-transport inverses BEFORE existing historical CI inverses."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESIGN_BYTES = (ROOT / "docs/BROAD_READER_TRANSPORT_DESIGN_20261026.json").read_bytes()
assert hashlib.sha256(DESIGN_BYTES).hexdigest() == "ccb2f2698a7d15f7d71261049503bf03ff07bfceccc87c315d2b7572ff2aa696"
DESIGN = json.loads(DESIGN_BYTES)
RECIPES = DESIGN["counted_parent_recipe"]


def restore_broad_parent(name, data):
    if name not in RECIPES:
        return data
    recipe = RECIPES[name]
    if "append" in recipe:
        suffix = recipe["append"].encode()
        assert data.endswith(suffix) and data.count(suffix) == 1, name
        data = data[: -len(suffix)]
    for row in reversed(recipe.get("substitutions", [])):
        old, new = row["old"].encode(), row["new"].encode()
        assert data.count(new) == row["count"], name
        data = data.replace(new, old)
    assert hashlib.sha256(data).hexdigest() == recipe["old_full_sha256"], name
    return data


def restore_ci_parent_file(name, data):
    from ci_cost_preservation import restore_parent_file

    return restore_parent_file(name, restore_broad_parent(name, data))
