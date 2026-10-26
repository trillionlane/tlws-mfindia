"""Invert ONLY the reviewed counted CI-cost edits before whole-byte checks."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESIGN_SHA = '8d4c3437d34f30cba094faf53de0b35b37eade4709dcdb6f663757a7745d75ba'
DESIGN = (ROOT / 'docs/CI_COST_REDUCTION_DESIGN_20261026.json').read_bytes()
assert hashlib.sha256(DESIGN).hexdigest() == DESIGN_SHA
RECIPES = json.loads(DESIGN)['counted_literal_recipe_before_code']
APPROVED = frozenset({
    'tests/test_verified_review_store.py',
    'scripts/run_ci_tests.py',
    'tests/test_ci_test_partition.py',
    'tests/test_broad_reader_policy_preservation.py',
    'tests/test_reader_permission_capture_preservation.py',
    'tests/test_reader_access_transport.py',
})


def restore_parent_file(name, data):
    if name not in APPROVED:
        return data
    recipe = RECIPES[name]
    for change in reversed(recipe['substitutions']):
        old, new = change['old'].encode(), change['new'].encode()
        assert change['count'] == 1 and data.count(new) == 1, name
        data = data.replace(new, old, 1)
    assert hashlib.sha256(data).hexdigest() == recipe['old_full_sha256'], name
    return data
